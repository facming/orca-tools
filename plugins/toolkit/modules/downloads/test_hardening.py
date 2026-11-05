"""Isolated HOME; actual flock and descriptor operations, no SSH or VPS."""
import concurrent.futures
import importlib.util
import json
import os
import pathlib
import sys
import subprocess
import io
import contextlib
import tempfile
import threading
import unittest
from unittest.mock import patch

TOOLKIT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOLKIT))
import private_files as files
spec = importlib.util.spec_from_file_location('reader', pathlib.Path(__file__).with_name('remote-reader.py'))
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)


class HardeningTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='orca-security-')
        self.addCleanup(temp.cleanup)
        self.home = pathlib.Path(temp.name)
        self.patch = patch.object(pathlib.Path, 'home', return_value=self.home)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        original = pathlib.Path.read_text
        def public(file, *a, **kw):
            if str(file) == '/etc/ssh/ssh_host_ed25519_key.pub':
                return 'ssh-ed25519 U1lOVEhFVElD fixture'
            return original(file, *a, **kw)
        p = patch.object(pathlib.Path, 'read_text', public)
        p.start()
        self.addCleanup(p.stop)
        self.source = self.home / 'source'
        self.source.write_bytes(b'synthetic')
        self.keys = self.home / '.ssh/authorized_keys'
        self.a, self.b = 'a' * 32, 'b' * 32

    def register(self, ident):
        return reader.register({'id': ident, 'source': str(self.source), 'publicKey': 'ssh-ed25519 ' + ('QUFBQQ==' if ident == self.a else 'QkJCQg==') + ' ' + ident})

    def simultaneous(self, *actions):
        barrier = threading.Barrier(len(actions))
        def run(action):
            barrier.wait()
            return action()
        with concurrent.futures.ThreadPoolExecutor(len(actions)) as pool:
            return list(pool.map(run, actions))

    def test_two_registers_two_cleanups_repeat_and_exact_ownership(self):
        self.simultaneous(lambda: self.register(self.a), lambda: self.register(self.b))
        self.assertEqual(self.keys.read_text().count('restrict,command='), 2)
        manual = 'manual key with orca-download:' + self.a + '\n'
        with self.keys.open('a') as stream:
            stream.write(manual)
        self.simultaneous(lambda: reader.cleanup(self.a), lambda: reader.cleanup(self.b))
        self.assertEqual(self.keys.read_text(), manual)
        self.assertEqual(reader.cleanup(self.a), 'KEY_REMOVED:' + self.a)

    def test_register_cleanup_concurrently(self):
        self.register(self.a)
        self.simultaneous(lambda: reader.cleanup(self.a), lambda: self.register(self.b))
        text = self.keys.read_text()
        self.assertNotIn(self.a, text)
        self.assertIn(self.b, text)

    def test_manual_edit_after_read_is_detected(self):
        self.register(self.a)
        replace = files.PrivateDirectory.replace
        def edit(directory, name, data, expected):
            if name == 'authorized_keys':
                with self.keys.open('a') as stream:
                    stream.write('MANUAL KEY\n')
            return replace(directory, name, data, expected)
        with patch.object(files.PrivateDirectory, 'replace', edit):
            with self.assertRaisesRegex(ValueError, 'External'):
                reader.cleanup(self.a)
        self.assertIn('MANUAL KEY', self.keys.read_text())
        self.assertIn(self.a, self.keys.read_text())

    def test_register_waits_while_cleanup_has_snapshot(self):
        self.register(self.a)
        read, release = threading.Event(), threading.Event()
        replace = files.PrivateDirectory.replace
        def pause(directory, name, data, expected):
            if name == 'authorized_keys' and threading.current_thread().name == 'cleanup':
                read.set()
                self.assertTrue(release.wait(3))
            return replace(directory, name, data, expected)
        errors = []
        def call(fn):
            try: fn()
            except Exception as exc: errors.append(exc)
        with patch.object(files.PrivateDirectory, 'replace', pause):
            one = threading.Thread(name='cleanup', target=call, args=(lambda: reader.cleanup(self.a),))
            one.start()
            self.assertTrue(read.wait(3))
            two = threading.Thread(target=call, args=(lambda: self.register(self.b),))
            two.start()
            release.set()
            one.join(5); two.join(5)
        self.assertFalse(errors)
        self.assertNotIn(self.a, self.keys.read_text())
        self.assertIn(self.b, self.keys.read_text())

    def test_failed_replace_retains_authorization_and_task(self):
        self.register(self.a)
        original = self.keys.read_bytes()
        with patch.object(files.os, 'replace', side_effect=OSError('synthetic atomic failure')):
            with self.assertRaises(OSError): reader.cleanup(self.a)
        self.assertEqual(self.keys.read_bytes(), original)
        self.assertIn(self.a, str(list((self.home / '.local/share/orca-parallel-download/jobs').iterdir())))
        self.assertEqual(reader.cleanup(self.a), 'KEY_REMOVED:' + self.a)

    def test_symlinks_and_hardlinks(self):
        self.register(self.a)
        victim = self.home / 'victim'
        victim.write_text('KEEP')
        for name in ('authorized_keys', '.orca-authorized-keys.lock'):
            target = self.keys.parent / name
            backup = target.read_bytes()
            target.unlink();target.symlink_to(victim)
            with self.assertRaises((OSError, ValueError)): reader.cleanup(self.a)
            self.assertEqual(victim.read_text(), 'KEEP')
            target.unlink();target.write_bytes(backup);target.chmod(0o600)
        os.link(self.keys, self.home / 'hardlink')
        with self.assertRaises(ValueError): reader.cleanup(self.a)

    def test_parent_symlink_and_duplicate_key_fail_closed(self):
        victim = self.home / 'victim-dir';victim.mkdir(mode=0o700)
        (self.home / '.ssh').symlink_to(victim)
        with self.assertRaises((ValueError, OSError)): self.register(self.a)
        self.assertEqual(list(victim.iterdir()), [])
        (self.home / '.ssh').unlink()
        self.register(self.a)
        with self.keys.open('a') as stream: stream.write('ssh-ed25519 QUFBQQ== manual-copy\n')
        with self.assertRaisesRegex(ValueError, 'unowned'): reader.cleanup(self.a)
        self.assertIn('manual-copy', self.keys.read_text())
        self.assertIn(self.a, self.keys.read_text())

    def test_lock_replacement_is_not_success(self):
        directory = files.PrivateDirectory(str(self.home))
        try:
            with self.assertRaises((OSError, ValueError)):
                with directory.lock('.lock'):
                    (self.home / '.lock').unlink()
                    (self.home / '.lock').write_bytes(b'replaced')
        finally:
            directory.close()

    def test_lock_contention_timeout_and_release(self):
        directory = files.PrivateDirectory(str(self.home))
        self.addCleanup(directory.close)
        with directory.lock('.lock'):
            with self.assertRaisesRegex(ValueError, 'timed out'):
                with directory.lock('.lock', timeout=.05): pass
        with directory.lock('.lock', timeout=.05): pass

    def test_temp_collision_interrupted_write_and_identity_change(self):
        directory = files.PrivateDirectory(str(self.home))
        self.addCleanup(directory.close)
        directory.replace('config', b'old', None)
        version = directory.read('config')[1]
        victim = self.home / 'victim';victim.write_text('KEEP')
        temp = self.home / ('.config.' + 'c' * 32 + '.tmp')
        temp.symlink_to(victim)
        with patch.object(files.secrets, 'token_hex', return_value='c' * 32):
            with self.assertRaises(FileExistsError): directory.replace('config', b'new', version)
        self.assertTrue(temp.is_symlink());self.assertEqual(victim.read_text(), 'KEEP')
        temp.unlink();temp.write_bytes(b'EXISTING')
        with patch.object(files.secrets, 'token_hex', return_value='c' * 32):
            with self.assertRaises(FileExistsError): directory.replace('config', b'new', version)
        self.assertEqual(temp.read_bytes(), b'EXISTING')
        with patch.object(files.os, 'fsync', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError): directory.replace('config', b'new', version)
        self.assertEqual(directory.read('config')[0], b'old')
        (self.home / 'config').unlink();(self.home / 'config').write_bytes(b'old');(self.home / 'config').chmod(0o600)
        with self.assertRaisesRegex(ValueError, 'External'): directory.replace('config', b'new', version)
        directory.replace('config', b'recovered', directory.read('config')[1])
        self.assertEqual(directory.read('config')[0], b'recovered')

    def test_legacy_cleanup_reproduces_lost_manual_key(self):
        baseline = subprocess.check_output(['git', 'show', 'fe0a0157c1e0d686fe06fe9eed6846abc715cd06:plugins/toolkit/modules/downloads/remote-reader.py'], text=True)
        legacy = {'__name__': 'legacy_fixture'}
        exec(baseline, legacy)
        self.register(self.a)
        job_path = self.home / '.local/share/orca-parallel-download/jobs' / (self.a + '.json')
        job = json.loads(job_path.read_text());job['tag'] = 'orca-download:' + self.a
        job_path.write_text(json.dumps(job))
        original = pathlib.Path.read_text
        def after_read(file, *a, **kw):
            value = original(file, *a, **kw)
            if file == self.keys:
                with file.open('a') as stream: stream.write('MANUAL KEY\n')
            return value
        output = io.StringIO()
        with patch.object(pathlib.Path, 'read_text', after_read), patch.object(sys, 'argv', ['reader', self.a]), patch.dict(os.environ, SSH_ORIGINAL_COMMAND='done'), contextlib.redirect_stdout(output):
            legacy['main']()
        self.assertEqual(output.getvalue().strip(), 'KEY_REMOVED')
        self.assertNotIn('MANUAL KEY', self.keys.read_text())

    def test_real_fallback_and_ssh_done_processes_share_protocol(self):
        self.register(self.a)
        transport = pathlib.Path(__file__).with_name('transport.cjs')
        bootstrap = subprocess.check_output(['node', '-e', 'process.stdout.write(require(process.argv[1]).remoteModule())', str(transport)], text=True)
        fallback = bootstrap + '\nprint(m.cleanup("' + self.a + '"))'
        env = dict(os.environ, HOME=str(self.home), USERPROFILE=str(self.home), SSH_ORIGINAL_COMMAND='done', PYTHONPATH=str(TOOLKIT))
        directory = files.PrivateDirectory(str(self.keys.parent))
        try:
            with directory.lock('.orca-authorized-keys.lock'):
                one = subprocess.Popen([sys.executable, str(pathlib.Path(reader.__file__)), self.a], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                two = subprocess.Popen([sys.executable, '-c', fallback], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for process in (one, two):
                stdout, stderr = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 0, stderr.decode())
                self.assertEqual(stdout.strip(), ('KEY_REMOVED:' + self.a).encode())
        finally:
            directory.close()
        self.assertNotIn(self.a, self.keys.read_text())

    def test_unsupported_filesystem_fails_closed(self):
        self.register(self.a)
        with patch.object(files, 'require_local_filesystem', side_effect=ValueError('Unsupported filesystem')):
            with self.assertRaises(ValueError): reader.cleanup(self.a)
        self.assertIn(self.a, self.keys.read_text())


if __name__ == '__main__':
    unittest.main()
