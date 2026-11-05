"""Release-blocking evidence, deliberately exits 1 when final CAS gaps remain.

No production state. Invoke explicitly; this is NOT a passing regression test.
"""
import importlib.util
import json
import pathlib
import tempfile
from unittest.mock import patch
from private_files import PrivateDirectory
import private_files


def local_gap():
    with tempfile.TemporaryDirectory(prefix='orca-unresolved-') as root:
        folder = pathlib.Path(root)
        directory = PrivateDirectory(root)
        try:
            directory.replace('settings.json', b'{"before":true}', None)
            version = directory.read('settings.json')[1]
            replace = private_files.os.replace
            def race(src, dst, **kwargs):
                # An editor not following the advisory protocol writes after
                # the final comparison but before renameat commits.
                (folder / 'settings.json').write_text('{"external":true}')
                return replace(src, dst, **kwargs)
            with patch.object(private_files.os, 'replace', race):
                directory.replace('settings.json', b'{"ours":true}', version)
            return 'external' not in (folder / 'settings.json').read_text()
        finally:
            directory.close()


def remote_gap():
    spec = importlib.util.spec_from_file_location('reader', pathlib.Path(__file__).parent / 'modules/downloads/remote-reader.py')
    reader = importlib.util.module_from_spec(spec);spec.loader.exec_module(reader)
    with tempfile.TemporaryDirectory(prefix='orca-unresolved-') as root, patch.object(pathlib.Path, 'home', return_value=pathlib.Path(root)):
        home = pathlib.Path(root)
        ident = 'd' * 32
        owned = 'restrict,command="synthetic" ssh-ed25519 U1lOVEhFVElD orca-download:' + ident
        with reader.authorization_state() as (keys, jobs):
            keys.replace('authorized_keys', (owned + '\n').encode(), None)
            jobs.replace(ident + '.json', json.dumps({'protocol': 2, 'key': 'ssh-ed25519 U1lOVEhFVElD', 'authorization': owned, 'cleaned': False}).encode(), None)
        replace = private_files.os.replace
        def race(src, dst, **kwargs):
            if dst == 'authorized_keys':
                with (home / '.ssh/authorized_keys').open('a') as stream:
                    stream.write('MANUAL UNRELATED KEY\n')
            return replace(src, dst, **kwargs)
        with patch.object(private_files.os, 'replace', race):
            result = reader.cleanup(ident)
        return result.startswith('KEY_REMOVED:') and 'MANUAL' not in (home / '.ssh/authorized_keys').read_text()


local, remote = local_gap(), remote_gap()
print('H-02 final check/rename race reproduced:', local)
print('H-03 non-cooperating writer lost update reproduced:', remote)
print('BLOCKED: native syscall bindings plus advisory locks are not atomic compare-and-swap.')
raise SystemExit(1 if local or remote else 0)
