"""Shared isolated service storage. Never imports desktop JSON implicitly."""
import contextlib
import decimal
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import threading
import uuid

LIMIT = 2 * 1024 * 1024


def loads(raw):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError('Duplicate JSON key')
            out[key] = value
        return out
    if len(raw) > LIMIT:
        raise ValueError('Message too large')
    if isinstance(raw, bytes):
        raw = raw.decode('utf-8-sig')
    return json.loads(raw, object_pairs_hook=pairs, parse_float=decimal.Decimal,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite JSON')))


def dumps(value):
    # Decimal keeps unknown JSON numbers exact, including outside JS Number range.
    if isinstance(value, decimal.Decimal):
        if not value.is_finite():
            raise ValueError('Non-finite JSON')
        return str(value)
    if isinstance(value, dict):
        return '{' + ','.join(json.dumps(k, ensure_ascii=True) + ':' + dumps(v)
                              for k, v in sorted(value.items())) + '}'
    if isinstance(value, list):
        return '[' + ','.join(map(dumps, value)) + ']'
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def private(path, directory=False):
    s = os.lstat(path)
    if (s.st_uid != os.geteuid() or stat.S_IMODE(s.st_mode) != (0o700 if directory else 0o600)
            or not (stat.S_ISDIR(s.st_mode) if directory else stat.S_ISREG(s.st_mode))
            or (not directory and s.st_nlink != 1)):
        raise PermissionError('Unsafe private storage: ' + str(path))
    # macOS ACL entries can grant rights beyond mode bits.
    if os.uname().sysname == 'Darwin':
        import subprocess
        if len(subprocess.check_output(['/bin/ls', '-lde', str(path)]).splitlines()) != 1:
            raise PermissionError('Unexpected ACL')


def protected_ancestors(path):
    for parent in Path(path).absolute().parents:
        s = parent.lstat()
        if not stat.S_ISDIR(s.st_mode) or s.st_uid not in (0, os.geteuid()) or s.st_mode & 0o022:
            raise PermissionError('Unprotected ancestor: ' + str(parent))
        if os.uname().sysname == 'Darwin':
            import subprocess
            if len(subprocess.check_output(['/bin/ls', '-lde', str(parent)]).splitlines()) != 1:
                raise PermissionError('Unexpected ancestor ACL: ' + str(parent))


class Store:
    def __init__(self, directory, initialize=False):
        os.umask(0o077)
        self.root = Path(directory)
        private(self.root, True)
        self.lock = threading.RLock()
        self.lockfd = os.open(self.root / 'writer.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        private(self.root / 'writer.lock')
        fcntl.flock(self.lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.file = self.root / 'state.sqlite'
        if initialize:
            fd = os.open(self.file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        self.check_files()
        self.db = sqlite3.connect('file:' + str(self.file) + '?mode=rw', uri=True,
                                  isolation_level=None, check_same_thread=False, timeout=3)
        for key, value in [('journal_mode', 'DELETE'), ('synchronous', 'EXTRA'),
                           ('fullfsync', 'ON'), ('foreign_keys', 'ON'), ('busy_timeout', '3000')]:
            self.db.execute(f'PRAGMA {key}={value}')
        for key, expected in [('journal_mode', 'delete'), ('synchronous', 3), ('fullfsync', 1), ('foreign_keys', 1)]:
            if self.db.execute('PRAGMA ' + key).fetchone()[0] != expected:
                raise RuntimeError('Unsupported SQLite durability setting')
        if self.db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('Database corrupt; retained for recovery')
        if initialize:
            self.db.executescript('''BEGIN IMMEDIATE;
                CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE requests(id TEXT PRIMARY KEY, digest TEXT NOT NULL, result TEXT NOT NULL);
                CREATE TABLE audit(seq INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT, action TEXT, request TEXT);
                COMMIT;''')
            with self.transaction():
                for key, value in [('schema', '1'), ('store_id', uuid.uuid4().hex), ('epoch', uuid.uuid4().hex)]:
                    self.db.execute('INSERT INTO meta VALUES(?,?)', (key, value))
        if self.meta('schema') != '1':
            raise RuntimeError('Unsupported schema')

    def check_files(self):
        private(self.root, True)
        private(self.file)
        for suffix in ('-journal', '-wal', '-shm'):
            p = Path(str(self.file) + suffix)
            if p.exists() or p.is_symlink():
                private(p)
                if suffix != '-journal':
                    raise RuntimeError('Unexpected WAL sidecar; retained')

    def meta(self, key):
        row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return row[0] if row else None

    @contextlib.contextmanager
    def transaction(self):
        with self.lock:
            self.check_files()
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield
                self.db.execute('COMMIT')
            except BaseException:
                if self.db.in_transaction:
                    self.db.execute('ROLLBACK')
                raise

    def request(self, actor, request, operation):
        rid = request.get('request_id')
        if not isinstance(rid, str) or not 16 <= len(rid) <= 128:
            raise ValueError('request_id required')
        key = str(actor) + ':' + rid
        hashed = digest(request)
        with self.transaction():
            old = self.db.execute('SELECT digest,result FROM requests WHERE id=?', (key,)).fetchone()
            if old:
                if old[0] != hashed:
                    raise ValueError('request_id conflict')
                return loads(old[1])
            result = operation()
            self.db.execute('INSERT INTO requests VALUES(?,?,?)', (key, hashed, dumps(result)))
            self.db.execute('INSERT INTO audit(actor,action,request) VALUES(?,?,?)',
                            (str(actor), request['op'], rid))
            return result

    def close(self):
        self.db.close()
        if self.lockfd is not None:
            os.close(self.lockfd)
            self.lockfd = None

    def __del__(self):
        if getattr(self, "lockfd", None) is not None:
            os.close(self.lockfd)
            self.lockfd = None
