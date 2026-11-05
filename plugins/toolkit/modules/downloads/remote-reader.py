"""Per-download, read-only forced SSH command. No arbitrary shell commands."""
import hashlib
import json
import os
import pathlib
import re
import shlex
import stat
import sys


# Installed beside the reader; source-tree tests add the Toolkit directory.
from private_files import PrivateDirectory
import contextlib


@contextlib.contextmanager
def authorization_state():
    ssh = PrivateDirectory(str(pathlib.Path.home() / '.ssh'))
    try:
        with ssh.lock('.orca-authorized-keys.lock', timeout=10):
            jobs = PrivateDirectory(str(pathlib.Path.home() / '.local/share/orca-parallel-download/jobs'))
            try:
                yield ssh, jobs
            finally:
                jobs.close()
    finally:
        ssh.close()


def job_record(jobs, job_id):
    data, version = jobs.read(job_id + '.json')
    return (None if data is None else json.loads(data)), version


def cleanup(job_id):
    if not re.fullmatch(r'[a-f0-9]{32}', job_id):
        raise ValueError('Invalid job ID')
    with authorization_state() as (keys, jobs):
        job, version = job_record(jobs, job_id)
        if not job or job.get('protocol') != 2 or not isinstance(job.get('authorization'), str) or not re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/=]+', job.get('key', '')):
            raise ValueError('Missing or legacy ownership record; manual cleanup required')
        data, original = keys.read('authorized_keys')
        if data is None:
            raise ValueError('Missing authorized_keys; manual inspection required')
        owned = job['authorization'].encode() + b'\n'
        lines = data.splitlines(keepends=True)
        count = lines.count(owned)
        if count > 1 or (job.get('cleaned') and count):
            raise ValueError('Ambiguous authorization ownership')
        # Exact line equality, never substring/tag matching. Keep a tombstone
        # so a lost reply can be retried without guessing ownership.
        remaining = b''.join(line for line in lines if line != owned)
        if re.search(rb'(?:^|[ \t])' + re.escape(job['key'].encode()) + rb'(?=[ \t\r\n]|$)', remaining, re.MULTILINE):
            raise ValueError('Task public key exists in an unowned line; manual cleanup required')
        if count:
            keys.replace('authorized_keys', remaining, original)
        job['cleaned'] = True
        jobs.replace(job_id + '.json', json.dumps(job).encode(), version)
        keys.check()
        if owned in (keys.read('authorized_keys')[0] or b'').splitlines(keepends=True):
            raise ValueError('Authorization remains; manual cleanup required')
    return 'KEY_REMOVED:' + job_id


def fingerprint(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]


def register(request):
    job_id = request['id']
    if not re.fullmatch(r'[a-f0-9]{32}', job_id):
        raise ValueError('Invalid job ID')
    source = pathlib.Path(request['source']).resolve(strict=True)
    info = source.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('Select a regular file')
    public_key = request['publicKey'].strip()
    if not re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/=]+(?: [a-zA-Z0-9:_-]+)?', public_key):
        raise ValueError('Invalid public key')
    host_key = pathlib.Path('/etc/ssh/ssh_host_ed25519_key.pub').read_text().strip().split()
    digest = hashlib.sha256()
    with source.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    if fingerprint(source.stat()) != fingerprint(info):
        raise ValueError('Source changed while preparing download')
    script = pathlib.Path(__file__).resolve()
    command = 'python3 ' + shlex.quote(str(script)) + ' ' + job_id
    # Normalize the comment to the unique task ID. The complete line is the
    # ownership record, including forced-command and key material.
    public_key = ' '.join(public_key.split()[:2]) + ' orca-download:' + job_id
    line = 'restrict,command="' + command.replace('"', '\\"') + '" ' + public_key
    job = {'protocol': 2, 'source': str(source), 'fingerprint': fingerprint(info),
           'authorization': line, 'key': ' '.join(public_key.split()[:2]), 'cleaned': False}
    with authorization_state() as (keys, jobs):
        previous, version = job_record(jobs, job_id)
        data, original = keys.read('authorized_keys')
        data = data or b''
        if previous is not None and previous != job:
            raise ValueError('Task identity already exists; create a new download')
        owned = line.encode() + b'\n'
        if previous is None:
            if owned in data.splitlines(keepends=True):
                raise ValueError('Authorization has no ownership record')
            jobs.replace(job_id + '.json', json.dumps(job).encode(), version)
        count = data.splitlines(keepends=True).count(owned)
        if count > 1:
            raise ValueError('Duplicate authorization')
        if not count:
            keys.replace('authorized_keys', data + (b'\n' if data and not data.endswith(b'\n') else b'') + owned, original)
    return {'size': info.st_size, 'sha256': digest.hexdigest(), 'source': str(source),
            'hostKey': ' '.join(host_key[:2]), 'fingerprint': fingerprint(info)}


def main():
    job_id = sys.argv[1]
    if not re.fullmatch(r'[a-f0-9]{32}', job_id):
        raise ValueError('Invalid job ID')
    args = shlex.split(os.environ.get('SSH_ORIGINAL_COMMAND', ''))
    if args == ['done']:
        print(cleanup(job_id))
        return
    with authorization_state() as (_, jobs):
        job, _ = job_record(jobs, job_id)
        if not job or job.get('cleaned'):
            raise ValueError('Task unavailable')
    if len(args) != 3 or args[0] != 'read':
        raise ValueError('Only bounded reads are allowed')
    offset, count = int(args[1]), int(args[2])
    source = pathlib.Path(job['source'])
    with source.open('rb') as stream:
        info = os.fstat(stream.fileno())
        if fingerprint(info) != job['fingerprint']:
            raise ValueError('Source changed; create a new download')
        if offset < 0 or count < 1 or count > 8 * 1024 * 1024 or offset + count > info.st_size:
            raise ValueError('Invalid byte range')
        stream.seek(offset)
        while count:
            block = stream.read(min(count, 256 * 1024))
            if not block:
                raise ValueError('Unexpected end of file')
            sys.stdout.buffer.write(block)
            count -= len(block)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(3)
