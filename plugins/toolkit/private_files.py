"""Small POSIX syscall boundary shared by settings and the remote SSH reader.

Locks serialize cooperating writers only. The final comparison and rename are
not a filesystem compare-and-swap; see the release audit's open H-02/H-03 limits.
Never unlink the stable lock inode, including after timeout or process death.
"""
import contextlib
import ctypes
import struct
import fcntl
import hashlib
import os
import secrets
import stat
import time


def identity(s):
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def require_local_filesystem(fd):
    """Reject remote/unknown filesystems, not a proof against a broken kernel.

    Darwin statfs64 layout: SDK sys/mount.h __DARWIN_STRUCT_STATFS64.
    Linux statfs starts with a native long f_type (linux/magic.h).
    """
    system = os.uname().sysname
    libc = ctypes.CDLL(None, use_errno=True)
    buf = ctypes.create_string_buffer(4096)
    fn = getattr(libc, 'fstatfs64' if system == 'Darwin' else 'fstatfs')
    fn.argtypes = [ctypes.c_int, ctypes.c_void_p]
    fn.restype = ctypes.c_int
    if fn(fd, buf) != 0:
        raise ValueError('Cannot identify filesystem; manual cleanup required')
    if system == 'Darwin':
        allowed = buf.raw[72:88].split(b'\0')[0] in (b'apfs', b'hfs') and bool(struct.unpack_from('=I', buf.raw, 64)[0] & 0x1000)
    elif system == 'Linux':
        allowed = ctypes.c_long.from_buffer(buf).value in (0xEF53, 0x58465342, 0x9123683E, 0x01021994)
    else:
        allowed = False
    if not allowed:
        raise ValueError('Unsupported filesystem; preserve task and key for manual cleanup')


class PrivateDirectory:
    def __init__(self, path):
        path = os.path.abspath(path)
        # macOS system aliases, not user-controlled links. All remaining
        # components are opened individually with O_NOFOLLOW.
        if os.uname().sysname == 'Darwin':
            for alias in ('/var', '/tmp'):
                if path.startswith(alias + '/'):
                    path = '/private' + path
        self.path = path
        self.fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        try:
            parts = path.split('/')[1:]
            for index, part in enumerate(parts):
                if not part or part in ('.', '..'):
                    raise ValueError('Invalid private directory')
                try:
                    os.mkdir(part, 0o700, dir_fd=self.fd)
                except FileExistsError:
                    pass
                fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.fd)
                os.close(self.fd)
                self.fd = fd
                s = os.fstat(fd)
                if index == len(parts) - 1:
                    if s.st_uid != os.getuid() or stat.S_IMODE(s.st_mode) != 0o700:
                        raise ValueError('Expected a user-owned private directory (0700)')
                elif s.st_uid not in (0, os.getuid()) or (s.st_mode & 0o022 and not s.st_mode & stat.S_ISVTX):
                    raise ValueError('Unsafe ancestor directory')
            require_local_filesystem(self.fd)
            self.original = identity(os.fstat(self.fd))[:2]
        except BaseException:
            os.close(self.fd)
            raise

    def close(self):
        os.close(self.fd)

    def check(self):
        # Rewalk instead of trusting the startup path. Operations themselves
        # remain relative to the original descriptor if a name is moved.
        other = PrivateDirectory(self.path)
        try:
            if other.original != self.original:
                raise ValueError('Directory changed')
        finally:
            other.close()

    @staticmethod
    def name(name):
        if not name or '/' in name or name in ('.', '..'):
            raise ValueError('Invalid file name')
        return name

    def checked(self, fd):
        s = os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid() or s.st_nlink != 1 or stat.S_IMODE(s.st_mode) != 0o600:
            raise ValueError('Expected a private regular file (0600, one link)')
        return s

    def read(self, name):
        self.name(name)
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
        except FileNotFoundError:
            return None, None
        try:
            before = identity(self.checked(fd))
            with os.fdopen(os.dup(fd), 'rb') as stream:
                data = stream.read(4 * 1024 * 1024 + 1)
            if len(data) > 4 * 1024 * 1024 or before != identity(self.checked(fd)):
                raise ValueError('File changed or too large')
            named = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
            if before != identity(named):
                raise ValueError('File identity changed')
            return data, [*(str(part) for part in before), hashlib.sha256(data).hexdigest()]
        finally:
            os.close(fd)

    @contextlib.contextmanager
    def lock(self, name, timeout=3):
        self.name(name)
        # Separate exclusive initialization from opening the stable inode.
        # Concurrent O_CREAT without O_EXCL can return ENOENT on APFS.
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
        try:
            fd = os.open(name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=self.fd)
        except FileExistsError:
            fd = os.open(name, flags, dir_fd=self.fd)
        try:
            original = identity(self.checked(fd))[:2]
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ValueError('Private file lock timed out; manual inspection may be needed')
                    time.sleep(.02)
            self.check()
            if identity(os.stat(name, dir_fd=self.fd, follow_symlinks=False))[:2] != original:
                raise ValueError('Lock identity changed')
            yield
            self.check()
            if identity(os.stat(name, dir_fd=self.fd, follow_symlinks=False))[:2] != original or self.checked(fd).st_nlink != 1:
                raise ValueError('Lock changed during operation; manual inspection required')
        finally:
            os.close(fd)

    def replace(self, name, data, expected):
        self.name(name)
        temp = '.' + name + '.' + secrets.token_hex(16) + '.tmp'
        fd = None
        created = False
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd)
            created = True
            with os.fdopen(os.dup(fd), 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            temp_identity = identity(self.checked(fd))
            self.check()
            if identity(os.stat(temp, dir_fd=self.fd, follow_symlinks=False)) != temp_identity:
                raise ValueError('Temporary file identity changed')
            if self.read(name)[1] != expected:
                raise ValueError('External file change; reload before saving')
            # No POSIX atomic compare-and-replace primitive exists here. This
            # boundary is deliberately documented, not claimed race-free.
            os.replace(temp, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
            created = False
            os.fsync(self.fd)
            return self.read(name)[1]
        finally:
            if fd is not None:
                os.close(fd)
            if created:
                os.unlink(temp, dir_fd=self.fd)
