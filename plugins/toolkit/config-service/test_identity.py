"""Real Security.framework and kernel-token tests, no production installation.

Ad-hoc hashes identify synthetic native fixtures only. Production UID separation
is a separate invariant; these unprivileged tests never replace signature checks.
"""
import array
import ctypes
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from ipc import Server, config_authorizer, receive_caller, signed_peer_authorizer
from config_store import ConfigStore, DEFAULTS
from caller_policy import FUSE, check_fuses


class PolicyTests(unittest.TestCase):
    def test_no_same_uid_or_root_service(self):
        for uid in (0, 501):
            with patch('os.geteuid', return_value=uid), self.assertRaises(PermissionError):
                config_authorizer(501, 'always')

    def test_electron_injection_fuses(self):
        check_fuses(FUSE + bytes([1, 6]) + b'010011')
        for bits in (b'110011', b'011011', b'010111', b'010001', b'010010'):
            with self.assertRaises(ValueError):
                check_fuses(FUSE + bytes([1, 6]) + bits)
        with self.assertRaises(ValueError):
            check_fuses(FUSE+bytes([1,6])+b'010011'+FUSE+bytes([1,6])+b'110011')
        for data in (b'', FUSE + bytes([2, 6]) + b'010011'):
            with self.assertRaises(ValueError): check_fuses(data)

    def test_missing_multiple_and_nonsocket_descriptors(self):
        for count in (0, 1, 2, 20):
            with tempfile.TemporaryFile() as file:
                left, right = socket.socketpair()
                try:
                    ancillary = [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', [file.fileno()] * count))] if count else []
                    left.sendmsg([b'C'], ancillary)
                    with self.assertRaises((PermissionError, OSError)):
                        receive_caller(right)
                finally:
                    left.close(); right.close()


@unittest.skipUnless(sys.platform == 'darwin', 'Security.framework requires macOS')
class NativeIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='orca-native-')
        cls.root = Path(cls.temp.name).resolve()
        cls.source = Path(__file__).parent
        cls.bridge = cls.root / 'config-bridge'
        cls.caller = cls.root / 'orca-caller'
        cls.impostor = cls.root / 'impostor'
        cls.socket_dir = cls.root / 'run'; cls.socket_dir.mkdir()
        # Same-UID transport fixture calls the unchanged production relay. The
        # production main() UID gate remains compiled and is exercised only by
        # the separate administrator-run cross-UID harness.
        subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-I'+str(cls.source), '-DORCA_SOCKET_DIR="'+str(cls.socket_dir)+'"', str(cls.source/'tests/relay.c'), '-o', str(cls.bridge)], check=True)
        for name in (cls.caller, cls.impostor):
            subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-DBRIDGE="'+str(cls.bridge)+'"', str(cls.source/'tests/caller.c'), '-o', str(name)], check=True)
            subprocess.run(['codesign', '-s', '-', '-i', 'com.stablyai.orca.toolkit', '--force', str(name)], check=True, capture_output=True)
        subprocess.run(['codesign', '-s', '-', '--force', str(cls.bridge)], check=True, capture_output=True)
        dylib = cls.root/'peer.dylib'
        subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-dynamiclib', str(cls.source/'peer_identity.c'), '-framework', 'Security', '-framework', 'CoreFoundation', '-o', str(dylib)], check=True)
        cls.library = ctypes.CDLL(str(dylib))

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    @staticmethod
    def requirement(path):
        import re
        detail = subprocess.run(['codesign', '-d', '--verbose=4', str(path)], capture_output=True, check=True).stderr.decode()
        return 'cdhash H"'+re.search(r'^CDHash=([a-f0-9]+)$', detail, re.M)[1]+'"'

    def setUp(self):
        self.state = tempfile.TemporaryDirectory(dir=self.root)
        self.store = ConfigStore(self.state.name, True)
        self.serve()

    def serve(self):
        self.server = Server(self.socket_dir/(str(os.getuid())+'.sock'), self.store,
                             signed_peer_authorizer(os.getuid(), self.requirement(self.bridge), self.library),
                             signed_peer_authorizer(os.getuid(), self.requirement(self.caller), self.library))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.store.close(); self.state.cleanup()

    def call(self, executable, request):
        result = subprocess.run([str(executable)], input=json.dumps(request)+'\n', capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result)
        return json.loads(result.stdout)

    def test_authorized_orca_migration_read_write_backup_restore(self):
        def request(op, **kw):
            import uuid
            reply=self.call(self.caller, {'op':op, 'request_id':uuid.uuid4().hex, **kw})
            self.assertTrue(reply['ok'], reply);return reply['result']
        preview=request('preview', sources={k:None for k in DEFAULTS})
        request('import', preview_id=preview['preview_id'], hashes=preview['hashes'])
        base=request('read', namespace='settings');backup=request('backup')
        request('patch', namespace='settings', **{k:base[k] for k in ('epoch','store_id','revision')}, patch=[{'op':'set','path':['gpu','enabled'],'value':False}])
        self.server.shutdown();self.server.server_close();self.thread.join();self.store.close()
        self.store=ConfigStore(self.state.name);self.serve()
        self.assertFalse(json.loads(request('read',namespace='settings')['raw_json'])['gpu']['enabled'])
        restored=request('restore', **backup)
        self.assertNotEqual(restored['epoch'], base['epoch'])
        self.assertTrue(json.loads(request('read', namespace='settings')['raw_json'])['gpu']['enabled'])

    def test_signed_bridge_is_not_authority(self):
        reply=self.call(self.bridge, {'op':'status'})  # Python pipe has no caller token
        self.assertFalse(reply['ok'])
        reply=self.call(self.impostor, {'op':'status'})  # same UID, real socketpair, wrong signed binary
        self.assertFalse(reply['ok']);self.assertIn('Unauthorized signed', reply['error'])

    def test_claimed_identity_cannot_override_kernel_token(self):
        reply=self.call(self.impostor, {'op':'preview','request_id':'f'*32,'sources':{k:None for k in DEFAULTS},
                  'pid':os.getpid(),'uid':os.getuid(),'requirement':self.requirement(self.caller),'caller':'Orca Toolkit','audit_token':[0]*8})
        self.assertFalse(reply['ok'])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM previews').fetchone()[0],0)

    def test_node_spawn_sync_channel_has_original_identity(self):
        script="const cp=require('node:child_process');const r=cp.spawnSync(process.argv[1],[],{input:'{\"op\":\"status\"}\\n',encoding:'utf8'});process.stdout.write(r.stdout);"
        reply=subprocess.run(['node','-e',script,str(self.bridge)],capture_output=True,text=True,check=True)
        result=json.loads(reply.stdout)
        self.assertFalse(result['ok'])
        self.assertIn('Unauthorized signed',result['error'])

    def test_toolkit_commands_native_contract(self):
        result = subprocess.run(['node', str(self.source/'tests/commands.cjs'), str(self.caller), str(self.bridge), str(self.impostor)], capture_output=True, text=True, timeout=40)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print(result.stdout.strip())

    def test_toolkit_commands_native_service_offline(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        (self.socket_dir/(str(os.getuid())+'.sock')).unlink()
        result = subprocess.run(['node', str(self.source/'tests/commands.cjs'), str(self.caller), str(self.bridge), str(self.impostor), 'offline'], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print(result.stdout.strip())

    def test_direct_unapproved_client_rejected(self):
        with socket.socket(socket.AF_UNIX) as sock:
            sock.connect(str(self.socket_dir/(str(os.getuid())+'.sock')))
            sock.sendall(b'{"op":"status"}\n')
            self.assertIn(b'Unauthorized signed', sock.recv(4096))

if __name__ == '__main__': unittest.main()
