"""Tests the forced SSH reader against disposable local fixtures."""
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile

reader = pathlib.Path(__file__).with_name('remote-reader.py')
job_id = 'a' * 32
with tempfile.TemporaryDirectory(prefix='orca-reader-test-') as folder:
    home = pathlib.Path(folder)
    source = home / 'source.bin'
    data = os.urandom(2048)
    source.write_bytes(data)
    info = source.stat()
    jobs = home / '.local/share/orca-parallel-download/jobs'
    jobs.mkdir(parents=True)
    job_path = jobs / (job_id + '.json')
    job_path.write_text(json.dumps({'source': str(source),
        'fingerprint': [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns],
        'tag': 'orca-download:' + job_id}))
    keys = home / '.ssh/authorized_keys'
    keys.parent.mkdir()
    keys.write_text('PRESERVE_OTHER_KEY\nTEST orca-download:' + job_id + '\n')
    def call(command):
        env = dict(os.environ, HOME=folder, USERPROFILE=folder, SSH_ORIGINAL_COMMAND=command)
        return subprocess.run([sys.executable, str(reader), job_id], env=env, capture_output=True)
    result = call('read 19 70')
    assert result.returncode == 0 and result.stdout == data[19:89]
    for command in ['cat /etc/passwd', 'read -1 2', 'read 2047 2', 'read 0 8388609', 'read 0 12; rm -rf /']:
        result = call(command)
        assert result.returncode != 0 and result.stdout == b'', command
    source.write_bytes(data + b'changed')
    assert call('read 0 8').returncode != 0
    assert call('done').returncode == 0
    assert keys.read_text() == 'PRESERVE_OTHER_KEY\n' and not job_path.exists()
print('PASS: exact ranges, command rejection, bounds, changed-source rejection, scoped key cleanup.')

# Registration runs only with an isolated HOME and a synthetic host public key.
import importlib.util
from unittest.mock import patch
spec = importlib.util.spec_from_file_location('reader_fixture', reader)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with tempfile.TemporaryDirectory(prefix='orca-register-test-') as folder:
    home = pathlib.Path(folder)
    (home / '.ssh').mkdir()
    source = home / '中文 file.bin'
    source.write_bytes(b'synthetic data')
    original_read_text = pathlib.Path.read_text
    def read_text(file, *args, **kwargs):
        if str(file) == '/etc/ssh/ssh_host_ed25519_key.pub':
            return 'ssh-ed25519 U1lOVEhFVElD fixture'
        return original_read_text(file, *args, **kwargs)
    with patch.object(pathlib.Path, 'home', return_value=home), patch.object(pathlib.Path, 'read_text', read_text):
        meta = module.register({'id': job_id, 'source': str(source),
                                'publicKey': 'ssh-ed25519 U1lOVEhFVElD orca-download:' + job_id})
    line = (home / '.ssh/authorized_keys').read_text()
    assert line.startswith('restrict,command="python3 ')
    assert 'remote-reader.py' in line and job_id in line
    assert meta['sha256'] == hashlib.sha256(b'synthetic data').hexdigest()
    assert (home / '.ssh/authorized_keys').stat().st_mode & 0o777 == 0o600
    assert (home / ('.local/share/orca-parallel-download/jobs/' + job_id + '.json')).stat().st_mode & 0o777 == 0o600
print('PASS: isolated registration, forced-command restriction, hash and metadata permissions.')
