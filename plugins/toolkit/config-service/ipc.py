"""Bounded Unix IPC; peer identity comes only from the kernel."""
import ctypes
import os
from pathlib import Path
import socket
import socketserver
import struct
import stat
import sys
import threading
from secure_store import LIMIT, dumps, loads, protected_ancestors


def peer(sock):
    if sys.platform == 'darwin':
        libc = ctypes.CDLL(None)
        uid, gid = ctypes.c_uint(), ctypes.c_uint()
        if libc.getpeereid(sock.fileno(), ctypes.byref(uid), ctypes.byref(gid)):
            raise PermissionError('No peer identity')
        pid = struct.unpack('i', sock.getsockopt(0, 2, 4))[0]  # SOL_LOCAL, LOCAL_PEERPID
        return uid.value, pid
    pid, uid, _ = struct.unpack('3i', sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    return uid, pid


def receive(stream):
    raw = stream.readline(LIMIT + 1)
    if len(raw) > LIMIT or not raw.endswith(b'\n'):
        raise ValueError('Invalid frame')
    return loads(raw)


def send(stream, value):
    stream.write(dumps(value).encode() + b'\n')
    stream.flush()


def call(path, request):
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(30)
        sock.connect(str(path))
        with sock.makefile('rwb', buffering=0) as stream:
            send(stream, request)
            response = receive(stream)
            if not response['ok']:
                raise ValueError(response['error'])
            return response['result']


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    request_queue_size = 32

    def __init__(self, path, store, authorize):
        self.store, self.authorize = store, authorize
        self.capacity = threading.BoundedSemaphore(32)
        # No unlink of an existing endpoint: launchd/systemd or an operator owns lifecycle.
        if os.path.lexists(path):
            old = os.lstat(path)
            if not stat.S_ISSOCK(old.st_mode) or old.st_uid != os.geteuid():
                raise PermissionError('Unsafe stale endpoint')
            os.unlink(path)  # caller already holds exclusive lifetime store lock
        super().__init__(str(path), Handler)
        os.chmod(path, 0o666)  # authentication precedes parsing; DB remains private

    def verify_request(self, request, address):
        return self.capacity.acquire(blocking=False)

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.capacity.release()


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(30)
        try:
            actor = self.server.authorize(self.request)
            r = receive(self.rfile)
            if r.get('op') == 'read-range' and hasattr(self.server.store, 'stream_read'):
                self.server.store.stream_read(actor, r, self.rfile, self.wfile, peer(self.request)[1])
            else:
                result = self.server.store.handle(actor, r)
                send(self.wfile, {'ok': True, 'result': result})
        except (ValueError, PermissionError, KeyError, OSError) as e:
            try:
                send(self.wfile, {'ok': False, 'error': str(e)[:256]})
            except OSError:
                pass
        except Exception:
            try:
                send(self.wfile, {'ok': False, 'error': 'Storage result unknown; retry the same request_id'})
            except OSError:
                pass


def config_authorizer(tenant, requirement):
    if os.geteuid() in (0, tenant):
        raise PermissionError('Configuration service requires independent non-root UID')
    protected_ancestors(Path(__file__).resolve())
    library = ctypes.CDLL(str(Path(__file__).with_name('peer_identity.dylib')))
    library.orca_verify_peer.argtypes = [ctypes.c_int, ctypes.c_char_p]
    def check(sock):
        uid, pid = peer(sock)
        if uid != tenant:
            raise PermissionError('Unauthorized IPC tenant')
        status = library.orca_verify_peer(sock.fileno(), requirement.encode())
        if status != 0:
            raise PermissionError('Unauthorized signed IPC client (Security status ' + str(status) + ')')
        return uid
    return check
