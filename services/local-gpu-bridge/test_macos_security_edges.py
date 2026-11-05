"""Race and failure-boundary tests. All system processes/services are mocked."""
import http.client
import io
import json
import os
import plistlib
import re
import socket
import subprocess
import sys
import threading
import unittest
from unittest.mock import MagicMock, Mock, patch

import bridge as bridge_module
from bridge import Bridge, make_handler
import macos_service as service
from bridge_platform.macos import ComfyProcess
import test_macos_bridge as fixtures


@unittest.skipUnless(sys.platform == 'darwin', 'macOS security fixtures')
class SecurityEdges(unittest.TestCase):
    setUp = fixtures.Fixture.setUp
    installed = fixtures.Fixture.installed

    def guard(self):
        return service.RuntimeSecurity(self.installed(), self.platform)

    def test_down_or_ambiguous_tunnel_is_not_a_safe_bind_target(self):
        original = self.platform.run
        for output in [
            'utun9: flags=8010\n\tinet ' + fixtures.SELF + '\n',
            'utun9: flags=8011\n\tinet ' + fixtures.SELF + '\n',
            'utun9: flags=8050\n\tinet ' + fixtures.SELF + '\n',
            'utun9: flags=8051\n\tinet ' + fixtures.SELF + '\nen0: flags=8863\n\tinet ' + fixtures.SELF + '\n',
            'utun9: flags=8051\n\tinet ' + fixtures.SELF + '\nutun8: flags=8051\n\tinet ' + fixtures.SELF + '\n',
            'utun9: flags=8051\nunparsed-interface-header\n\tinet ' + fixtures.SELF + '\n',
        ]:
            with self.subTest(output=output):
                self.platform.run = lambda argv, **kw: subprocess.CompletedProcess(argv, 0, output) if argv[0] == '/sbin/ifconfig' else original(argv, **kw)
                with self.assertRaises(ValueError): self.platform.validate_network(self.config)

    def test_comfy_listener_failure_revokes_before_waiting_for_stop(self):
        guard = self.guard()
        process = ComfyProcess(guard.config, self.platform)
        process.on_security_failure = guard.revoked.set
        child = Mock(pid=1234);child.poll.return_value = None;process.process = child
        self.platform.run = Mock(return_value=subprocess.CompletedProcess([], 0, 'p1234\nn*:8188\n'))
        def wait(**kwargs):
            self.assertTrue(guard.revoked.is_set())
            self.assertFalse(guard.authorize('Bearer '+guard.config['token'], fixtures.PEER))
            return 0
        child.wait.side_effect = wait
        with patch('os.killpg') as kill:
            with self.assertRaisesRegex(ValueError, 'listener'): process.verify_listener()
        child.wait.assert_called_once();kill.assert_called_once_with(1234, 15)

    def test_monitor_probe_does_not_wait_on_startup_lock(self):
        process = ComfyProcess(self.config, self.platform)
        entered, release = threading.Event(), threading.Event()
        def starting():
            with process.lock:
                entered.set();release.wait(3)
        thread = threading.Thread(target=starting);thread.start()
        self.assertTrue(entered.wait(2))
        finished = threading.Event()
        def monitor():
            process.verify_listener(required=False);finished.set()
        checker = threading.Thread(target=monitor);checker.start()
        try:
            self.assertTrue(finished.wait(.5), 'Monitor blocked behind the startup probe')
        finally:
            release.set();thread.join(2);checker.join(2)

    def test_final_stop_during_dependency_probe_prevents_late_spawn(self):
        process = ComfyProcess(self.config, self.platform)
        entered, release = threading.Event(), threading.Event()
        errors = []
        def probe(_):
            entered.set();release.wait(3)
            return {'backend':'mps','cpu_fallback':False}
        def start():
            try: process.start()
            except ValueError as error: errors.append(str(error))
        sock = MagicMock();sock.__enter__.return_value.connect_ex.return_value = 1
        with patch('bridge_platform.macos.probe_python', side_effect=probe), patch('socket.socket', return_value=sock), patch('subprocess.Popen') as spawn:
            worker = threading.Thread(target=start);worker.start()
            stopper = None
            try:
                self.assertTrue(entered.wait(2))
                stopper = threading.Thread(target=lambda: process.stop(final=True));stopper.start()
                self.assertTrue(process.closed.wait(2))
            finally:
                release.set();worker.join(2)
                if stopper: stopper.join(2)
            self.assertFalse(worker.is_alive());self.assertFalse(stopper.is_alive())
            spawn.assert_not_called()
        self.assertEqual(errors, ['ComfyUI startup was stopped.'])

    def admin_fixture(self, guard):
        process = Mock()
        bridge = Bridge(guard.config, Mock(), self.platform)
        admin = service.start_admin(bridge, process, 0, guard)
        self.addCleanup(lambda: (admin.shutdown(), admin.server_close()))
        return admin, bridge, process

    def delayed_admin(self, mutation, use_local=False):
        guard = self.guard()
        admin, _, process = self.admin_fixture(guard)
        session = None
        if use_local:
            conn = http.client.HTTPConnection('127.0.0.1', admin.server_port, timeout=2)
            conn.request('GET', '/');response = conn.getresponse()
            session = re.search(rb"const token='([a-f0-9]+)'", response.read())[1].decode();conn.close()
        checked = threading.Event()
        original = guard.check
        def check():
            result = original();checked.set();return result
        with patch.object(guard, 'check', side_effect=check):
            conn = http.client.HTTPConnection('127.0.0.1', admin.server_port, timeout=2)
            try:
                conn.putrequest('POST', '/api/start')
                conn.putheader('Origin', 'http://127.0.0.1:'+str(admin.server_port))
                conn.putheader('Content-Type', 'application/json');conn.putheader('Content-Length', '2')
                conn.putheader('X-Admin-Token' if use_local else 'Authorization', session if use_local else 'Bearer '+guard.config['token'])
                conn.endheaders()
                self.assertTrue(checked.wait(2))
                mutation(guard)
                conn.send(b'{}');response = conn.getresponse()
                self.assertEqual(response.status, 403)
                self.assertEqual(json.loads(response.read()), {'error':'Access denied.'})
            finally: conn.close()
        process.start.assert_not_called()

    def test_admin_slow_body_cannot_start_after_network_revocation(self):
        self.delayed_admin(lambda guard: self.state.update(BackendState='Stopped'), use_local=True)

    def test_admin_slow_body_rechecks_rotated_bearer(self):
        def rotate(guard):
            path = self.platform.root / 'replacement'
            path.write_text('d'*64);path.chmod(0o600);os.replace(path, self.platform.root / 'token')
        self.delayed_admin(rotate)

    def test_admin_local_session_does_not_bypass_invalid_token_permissions(self):
        self.delayed_admin(lambda guard: (self.platform.root / 'token').chmod(0o644), use_local=True)

    def test_admin_read_rechecks_bearer_after_network_probe(self):
        guard = self.guard()
        admin, bridge, _ = self.admin_fixture(guard)
        original = guard.check
        def check():
            result = original()
            (self.platform.root / 'token').write_text('d'*64)
            return result
        with patch.object(guard, 'check', side_effect=check), patch.object(bridge, 'catalog') as catalog:
            conn = http.client.HTTPConnection('127.0.0.1', admin.server_port, timeout=2)
            try:
                conn.request('GET', '/api/workflows', headers={'Authorization':'Bearer '+guard.config['token']})
                response = conn.getresponse();self.assertEqual(response.status, 403);response.read()
            finally: conn.close()
            catalog.assert_not_called()

    def artifact_exchange(self, revoke_on_open=False):
        guard = self.guard()
        guard.authorize = Mock(return_value=True)
        bridge = Bridge(guard.config, Mock(), self.platform)
        ident = 'a'*32
        bridge.jobs[ident] = {'status':'succeeded','kind':'comfyui.workflow','artifacts':[{}]}
        bridge.inspect = Mock()
        class Stream(io.BytesIO):
            headers = {'Content-Length':'16'}
            def __enter__(stream):
                if revoke_on_open: guard.revoked.set()
                return stream
            def read(stream, size=-1):
                guard.revoked.set()
                return b'private-artifact'
        bridge.adapters['comfyui.workflow'].artifact.return_value = Stream()
        server = service.ClosingServer(('127.0.0.1', 0), make_handler(bridge, guard))
        thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(2)))
        conn = socket.create_connection(('127.0.0.1', server.server_port), timeout=2)
        try:
            conn.sendall(('GET /v1/artifacts/'+ident+'/0 HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n').encode())
            data = b''
            while chunk := conn.recv(4096): data += chunk
        finally: conn.close()
        self.assertNotIn(b'private-artifact', data)
        if revoke_on_open: self.assertNotIn(b'200 OK', data)
        else: self.assertIn(b'200 OK', data)

    def test_artifact_revocation_during_backend_open_sends_no_success(self):
        self.artifact_exchange(True)

    def test_artifact_revocation_during_blocking_read_discards_chunk(self):
        self.artifact_exchange()

    def test_close_failure_still_stops_managed_comfy(self):
        config = self.installed()
        server = Mock();server.server_close.side_effect = OSError('synthetic close failure')
        process = Mock(process=None)
        with patch.object(service, 'ClosingServer', return_value=server), patch.object(service, 'ComfyProcess', return_value=process), patch.object(service, 'start_admin', side_effect=OSError('synthetic startup failure')):
            with self.assertRaises(OSError): service.serve(config, self.platform)
        process.stop.assert_called_once_with(final=True)

    def test_uninstall_preserves_files_replaced_during_bootout(self):
        self.installed()
        original = self.platform.launchctl
        for target in [self.platform.plist, self.platform.root / 'launcher.json']:
            with self.subTest(target=target.name):
                before = target.read_bytes()
                def replace(action):
                    result = original(action)
                    if action == 'stop':
                        temp = target.with_name('replacement')
                        temp.write_bytes(before);temp.chmod(0o600);os.replace(temp, target)
                    return result
                self.loaded = True
                with patch.object(self.platform, 'launchctl', side_effect=replace):
                    with self.assertRaisesRegex(ValueError, 'changed during uninstall'): fixtures.installer.uninstall(self.platform)
                self.assertEqual(target.read_bytes(), before)
                self.assertTrue(self.platform.plist.exists())
                self.assertTrue((self.platform.root / 'launcher.json').exists())
                self.assertTrue((self.platform.root / 'token').exists())


if __name__ == '__main__': unittest.main()
