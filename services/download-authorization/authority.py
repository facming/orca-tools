"""v3 authorization authority. Only this isolated process opens its database.

A durable read lease is retired only after the dispatcher acknowledges flushing
its stdout. Lost connections retain leases: restart never invents DRAINED.
"""
import base64
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time
import uuid
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from secure_store import Store, dumps, loads, private
from ipc import send, receive

DOMAIN = b'orca-download-revocation-v3\0'
ID = re.compile(r'[a-f0-9]{32}')
HEX = re.compile(r'[a-f0-9]{64}')


def key_info(public):
    parts = public.split()
    if len(parts) != 2 or parts[0] != 'ssh-ed25519':
        raise ValueError('Canonical Ed25519 key required')
    blob = base64.b64decode(parts[1], validate=True)
    key = serialization.load_ssh_public_key(public.encode())
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError('Ed25519 required')
    return hashlib.sha256(blob).hexdigest()


def proof_payload(r):
    return dumps({k: v for k, v in r.items() if k != 'proof'}).encode()


class Authority(Store):
    def __init__(self, root, policy, initialize=False):
        super().__init__(root, initialize)
        self.policy = policy
        if initialize:
            self.db.executescript('''BEGIN IMMEDIATE;
            CREATE TABLE tasks(id TEXT PRIMARY KEY, owner INTEGER, public TEXT UNIQUE, fingerprint TEXT UNIQUE,
              source TEXT, digest TEXT, size INTEGER, state TEXT, generation TEXT, expires INTEGER, seq INTEGER);
            CREATE TABLE leases(id TEXT PRIMARY KEY, task TEXT, started INTEGER, pid INTEGER, identity TEXT);
            CREATE TABLE nonces(nonce TEXT PRIMARY KEY, task TEXT);
            COMMIT;''')
            key = Ed25519PrivateKey.generate()
            fd = os.open(self.root / 'receipt-key', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'wb') as f:
                f.write(key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                          serialization.NoEncryption()))
                f.flush(); os.fsync(f.fileno())
            (self.root / 'staging').mkdir(mode=0o700)
        private(self.root / 'receipt-key')
        private(self.root / 'staging', True)
        self.signer = Ed25519PrivateKey.from_private_bytes((self.root / 'receipt-key').read_bytes())
        self.verify_key = self.signer.public_key().public_bytes(serialization.Encoding.PEM,
                                                               serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        self.boot_id = uuid.uuid4().hex
        # Persistent leases are intentionally NOT reset after a process/host restart.

    def task(self, task):
        if not isinstance(task, str) or not ID.fullmatch(task):
            raise ValueError('Invalid task id')
        row = self.db.execute('SELECT * FROM tasks WHERE id=?', (task,)).fetchone()
        if row is None:
            raise ValueError('UNKNOWN task; not a revocation proof')
        return dict(zip(('id','owner','public','fingerprint','source','digest','size','state','generation','expires','seq'), row))

    def identity(self, task):
        return {'protocol': 3, 'server_id': self.policy['server_id'], 'endpoint_id': self.policy['endpoint_id'],
                'account_id': self.policy['account_id'], 'authority_epoch': self.meta('epoch'),
                'task_id': task['id'], 'task_public_key_sha256': task['fingerprint'],
                'auth_generation': task['generation'], 'signing_key_id': self.policy['signing_key_id']}

    def envelope(self, payload):
        raw = dumps(payload).encode()
        return {'payload': base64.b64encode(raw).decode(),
                'signature': base64.b64encode(self.signer.sign(DOMAIN + raw)).decode()}

    def import_source(self, owner, task_id, source):
        if not isinstance(source, str) or not source.startswith('/') or '..' in Path(source).parts:
            raise ValueError('Invalid source')
        roots = self.policy['owners'][str(owner)]
        root = next((p for p in sorted(roots, key=len, reverse=True) if source.startswith(p.rstrip('/') + '/')), None)
        if root is None:
            raise PermissionError('Source outside approved export roots')
        # Open from / component by component, never follow a symbolic link.
        directory = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        try:
            parts = Path(source).parts[1:]
            for component in parts[:-1]:
                fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory); directory = fd
            src = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        finally:
            os.close(directory)
        dest = self.root / 'staging' / (task_id + '-' + uuid.uuid4().hex)
        try:
            before = os.fstat(src)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_uid != owner:
                raise PermissionError('Unsafe export source')
            if before.st_size > self.policy.get('max_export_bytes', 10 * 1024**3):
                raise ValueError('Export quota exceeded')
            h = hashlib.sha256()
            fd = os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'wb') as out:
                while True:
                    block = os.read(src, 1024*1024)
                    if not block:
                        break
                    out.write(block); h.update(block)
                out.flush(); os.fsync(out.fileno()); os.fchmod(out.fileno(), 0o400)
            after = os.fstat(src)
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError('Source changed during staging')
            fd = os.open(dest.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            return dest.name, h.hexdigest(), before.st_size
        finally:
            os.close(src)
        # An uncommitted object is retained for diagnosis; never guessed/deleted on restart.

    def verify_possession(self, r):
        proof = base64.b64decode(r['proof'], validate=True)
        if len(proof) > 4096:
            raise ValueError('Invalid possession proof')
        with tempfile.TemporaryDirectory(dir=self.root) as tmp:
            allowed = Path(tmp) / 'allowed'
            signature = Path(tmp) / 'proof'
            allowed.write_text('task ' + r['public_key'] + '\n')
            signature.write_bytes(proof)
            result = subprocess.run(['/usr/bin/ssh-keygen', '-Y', 'verify', '-f', str(allowed),
                                     '-I', 'task', '-n', 'orca-download-registration-v3', '-s', str(signature)],
                                    input=proof_payload(r), capture_output=True, timeout=5)
            if result.returncode:
                raise PermissionError('Invalid key possession proof')

    def register(self, actor, r):
        task_id = r['task_id']
        if not ID.fullmatch(task_id) or r.get('protocol') != 3:
            raise ValueError('Invalid v3 registration')
        fp = key_info(r['public_key'])
        self.verify_possession(r)
        name, digest, size = self.import_source(actor, task_id, r['source'])
        self.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                        (task_id, actor, r['public_key'], fp, name, digest, size, 'ACTIVE',
                         uuid.uuid4().hex, int(time.time()) + 86400, 0))
        task = self.task(task_id)
        return self.envelope({**self.identity(task), 'action': 'register', 'state': 'ACTIVE',
                              'request_id': r['request_id'], 'size': size, 'sha256': digest})

    def owned(self, actor, r):
        task = self.task(r['task_id'])
        if task['owner'] != actor:
            raise PermissionError('Wrong task owner')
        for key, value in self.identity(task).items():
            if key not in ('signing_key_id',) and r.get(key) != value:
                raise PermissionError('Task binding mismatch: ' + key)
        return task

    def process_identity(self, pid):
        if not pid or not Path('/proc/sys/kernel/random/boot_id').exists():
            return None
        try:
            fields = Path('/proc/' + str(pid) + '/stat').read_text().rsplit(')', 1)[1].split()
            if fields[0] == 'Z':
                return 'dead'
            return Path('/proc/sys/kernel/random/boot_id').read_text().strip() + ':' + fields[19]
        except FileNotFoundError:
            return 'dead'

    def reap_confirmed_exits(self):
        for lease, pid, identity in self.db.execute('SELECT id,pid,identity FROM leases').fetchall():
            if identity is not None:
                current = self.process_identity(pid)
                if current is not None and current != identity:
                    self.db.execute('DELETE FROM leases WHERE id=?', (lease,))

    def revoke(self, actor, r):
        task = self.owned(actor, r)
        self.reap_confirmed_exits()
        nonce = r.get('client_nonce', '')
        if not HEX.fullmatch(nonce):
            raise ValueError('256-bit nonce required')
        self.db.execute('INSERT INTO nonces VALUES(?,?)', (nonce, task['id']))
        if task['state'] == 'ACTIVE':
            self.db.execute("UPDATE tasks SET state='REVOKING' WHERE id=?", (task['id'],))
        if self.db.execute('SELECT 1 FROM leases WHERE task=?', (task['id'],)).fetchone():
            return {'state': 'REVOKING', 'task_id': task['id'], 'read_barrier': 'PENDING'}
        if task['state'] != 'REVOKED':
            seq = self.db.execute('SELECT COALESCE(MAX(seq),0)+1 FROM tasks').fetchone()[0]
            self.db.execute("UPDATE tasks SET state='REVOKED',seq=? WHERE id=?", (seq, task['id']))
        task = self.task(task['id'])
        # Payload is committed with the request and tombstone. Signing occurs ONLY after COMMIT.
        return {'receipt_payload': {**self.identity(task), 'action': 'revoke', 'state': 'REVOKED',
                'request_id': r['request_id'], 'client_nonce': nonce,
                'revocation_commit_seq': str(task['seq']), 'read_barrier': 'DRAINED', 'issued_at': str(int(time.time()))}}

    def handle(self, actor, r):
        with self.lock:
            if actor == self.policy['lookup_uid']:
                if r['op'] != 'lookup':
                    raise PermissionError('Lookup role is read-only')
                fp = key_info(r['public_key'])
                row = self.db.execute("SELECT id FROM tasks WHERE fingerprint=? AND state='ACTIVE' AND expires>?",
                                      (fp, int(time.time()))).fetchone()
                if not row:
                    return {'authorized_key': ''}
                task = self.task(row[0])
                # Fixed program; id/generation generated or strictly validated by authority.
                command = self.policy['dispatcher'] + ' ' + task['id'] + ' ' + task['generation']
                return {'authorized_key': 'restrict,command="' + command + '" ' + task['public']}
            if str(actor) not in self.policy['owners']:
                raise PermissionError('Unauthorized management peer')
            if r['op'] == 'inventory':
                return {'authority_epoch': self.meta('epoch'), 'tasks': [dict(zip(('task_id','state','generation','expires'), row)) for row in self.db.execute('SELECT id,state,generation,expires FROM tasks WHERE owner=?',(actor,))]}
            if r['op'] == 'status':
                task = self.task(r['task_id'])
                if task['owner'] != actor:
                    raise PermissionError('Wrong owner')
                return {**self.identity(task), 'state': task['state'],
                        'leases': self.db.execute('SELECT id,started FROM leases WHERE task=?', (task['id'],)).fetchall()}
            if r['op'] not in ('register', 'revoke'):
                raise ValueError('Unknown operation')
            result = self.request(actor, r, lambda: self.register(actor, r) if r['op'] == 'register' else self.revoke(actor, r))
            if 'receipt_payload' in result:
                return self.envelope(result['receipt_payload'])
            return result

    def stream_read(self, actor, r, incoming, outgoing, peer_pid=None):
        if actor != self.policy['reader_uid']:
            raise PermissionError('Unauthorized reader peer')
        with self.transaction():
            task = self.task(r['task_id'])
            offset, length = r['offset'], r['length']
            if task['state'] != 'ACTIVE' or task['generation'] != r['generation'] or task['expires'] <= time.time():
                raise PermissionError('Read authorization revoked or expired')
            if type(offset) is not int or type(length) is not int or offset < 0 or not 1 <= length <= 8*1024*1024 or offset + length > task['size']:
                raise ValueError('Invalid range')
            path = self.root / 'staging' / task['source']
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o400:
                    raise PermissionError('Staging object unsafe')
                h = hashlib.sha256()
                while True:
                    block = os.read(fd, 1024*1024)
                    if not block:
                        break
                    h.update(block)
                if h.hexdigest() != task['digest']:
                    raise ValueError('Staging object changed')
                data = os.pread(fd, length, offset)
                if len(data) != length:
                    raise ValueError('Short staging read')
            finally:
                os.close(fd)
            identity = self.process_identity(peer_pid)
            if identity == 'dead':
                raise PermissionError('Reader already exited')
            lease = uuid.uuid4().hex
            self.db.execute('INSERT INTO leases VALUES(?,?,?,?,?)', (lease, task['id'], int(time.time()), peer_pid, identity))
        send(outgoing, {'ok': True, 'result': {'lease': lease, 'length': length}})
        outgoing.write(data); outgoing.flush()
        # No finally-release: disconnect, death, or missing ACK leaves a durable uncertain lease.
        ack = receive(incoming)
        if ack != {'ack': lease}:
            raise ValueError('Invalid drain acknowledgment')
        with self.transaction():
            self.db.execute('DELETE FROM leases WHERE id=?', (lease,))
        send(outgoing, {'ok': True, 'result': {'drained': lease}})
