"""Security lifecycle regressions. Only temporary files and loopback mock servers."""
import http.client
import json
import os
import plistlib
import socket
import subprocess
import sys
import threading
import unittest
from unittest.mock import Mock, patch

import test_macos_bridge as fixtures
import macos_service as service
from bridge import Bridge, make_handler
from bridge_platform.macos import ComfyProcess, require_user, environment


@unittest.skipUnless(sys.platform == 'darwin', 'macOS security fixtures')
class SecurityTests(unittest.TestCase):
    setUp = fixtures.Fixture.setUp
    installed = fixtures.Fixture.installed

    def guard(self):
        config = self.installed()
        return service.RuntimeSecurity(config, self.platform)

    def test_self_metadata_requires_local_tunnel_address(self):
        original = self.platform.run
        for output in ['', 'en0: flags=1\n\tinet ' + fixtures.SELF + '\n',
                       'utun9: flags=1\n\tinet ' + fixtures.OTHER + '\n']:
            with self.subTest(output=output):
                def run(argv, **kwargs):
                    return subprocess.CompletedProcess(argv, 0, output) if argv[0] == '/sbin/ifconfig' else original(argv, **kwargs)
                self.platform.run = run
                with self.assertRaises(ValueError): self.platform.validate_network(self.config)
        self.platform.run = original
        self.config.pop('self_node_id')
        with self.assertRaises(ValueError): self.platform.validate_network(self.config)

    def test_runtime_failure_latches_until_fresh_start(self):
        guard = self.guard()
        self.assertIsNotNone(guard.check())
        self.state['Self']['TailscaleIPs'] = []
        self.assertIsNone(guard.check())
        self.state = fixtures.network()
        self.assertIsNone(guard.check())
        self.assertIsNotNone(service.RuntimeSecurity(guard.config, self.platform).check())

    def test_runtime_config_or_token_permissions_fail_closed(self):
        guard = self.guard()
        config_file = self.platform.root / 'config.json'
        raw = config_file.read_text()
        for target in [config_file, self.platform.root / 'token']:
            target.chmod(0o644)
            self.assertIsNone(service.RuntimeSecurity(guard.config, self.platform).check())
            target.chmod(0o600)
        changed = json.loads(raw); changed['allowed_peers'] = [fixtures.OTHER]
        config_file.write_text(json.dumps(changed))
        self.assertIsNone(guard.check())

    def test_all_remote_routes_check_token_and_peer_on_every_keepalive_request(self):
        guard = self.guard()
        backend = Mock()
        bridge = Bridge(guard.config, backend, self.platform)
        peer = [fixtures.PEER]
        authorize = guard.authorize
        guard.authorize = lambda authorization, address: authorize(authorization, peer[0])
        server = service.ClosingServer(('127.0.0.1', 0), make_handler(bridge, guard))
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(2)))
        routes = [('GET', '/v1/status'), ('GET', '/v1/jobs'), ('POST', '/v1/jobs'),
                  ('GET', '/v1/jobs/' + 'a'*32), ('POST', '/v1/jobs/' + 'a'*32 + '/cancel'),
                  ('GET', '/v1/workflows'), ('GET', '/v1/artifacts/' + 'a'*32 + '/0'),
                  ('POST', '/v1/assets'), ('GET', '/v1/native/status'), ('GET', '/unknown')]
        for bad_peer, bad_token in [(False, True), (True, False)]:
            for method, route in routes:
                with self.subTest(peer=bad_peer, token=bad_token, route=route):
                    peer[0] = fixtures.PEER
                    conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=2)
                    try:
                        conn.request('GET', '/v1/jobs', headers={'Authorization': 'Bearer ' + guard.config['token']})
                        response = conn.getresponse(); self.assertEqual(response.status, 200); response.read()
                        old_socket = conn.sock
                        peer[0] = fixtures.OTHER if bad_peer else fixtures.PEER
                        token = 'wrong-sensitive-token' if bad_token else guard.config['token']
                        conn.request(method, route, body='{}' if method == 'POST' else None,
                                     headers={'Authorization': 'Bearer ' + token, 'X-Forwarded-For': fixtures.PEER,
                                              'Content-Type': 'application/json'})
                        self.assertIs(conn.sock, old_socket)
                        response = conn.getresponse(); body = response.read()
                        self.assertEqual(response.status, 403)
                        self.assertEqual(json.loads(body), {'error': 'Access denied.'})
                        self.assertNotIn(token.encode(), body)
                    finally: conn.close()
        self.assertEqual(backend.mock_calls, [])

    def test_atomic_token_rotation_invalidates_old_token_on_existing_connection(self):
        guard = self.guard()
        authorize = guard.authorize
        guard.authorize = lambda authorization, address: authorize(authorization, fixtures.PEER)
        bridge = Bridge(guard.config, Mock(), self.platform)
        server = service.ClosingServer(('127.0.0.1', 0), make_handler(bridge, guard))
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(2)))
        conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=2)
        self.addCleanup(conn.close)
        old = guard.config['token']
        conn.request('GET', '/v1/jobs', headers={'Authorization': 'Bearer ' + old})
        response = conn.getresponse(); self.assertEqual(response.status, 200); response.read()
        old_socket = conn.sock
        replacement = self.platform.root / 'replacement'
        replacement.write_text('c'*64); replacement.chmod(0o600)
        os.replace(replacement, self.platform.root / 'token')
        conn.request('GET', '/v1/jobs', headers={'Authorization': 'Bearer ' + 'c'*64})
        self.assertIs(conn.sock, old_socket)
        response = conn.getresponse(); self.assertEqual(response.status, 200); response.read()
        conn.request('GET', '/v1/jobs', headers={'Authorization': 'Bearer ' + old})
        self.assertIs(conn.sock, old_socket)
        response = conn.getresponse(); self.assertEqual(response.status, 403); response.read()
        self.assertFalse(guard.revoked.is_set())
        validate = self.platform.validate_network
        def rotate_during_probe(config):
            status = validate(config)
            (self.platform.root / 'token').write_text('e'*64)
            return status
        with patch.object(self.platform, 'validate_network', side_effect=rotate_during_probe):
            self.assertFalse(authorize('Bearer ' + 'c'*64, fixtures.PEER))
        self.assertTrue(authorize('Bearer ' + 'e'*64, fixtures.PEER))

    def test_admin_bearer_rotation(self):
        guard = self.guard()
        bridge = Bridge(guard.config, Mock(), self.platform)
        admin = service.start_admin(bridge, Mock(), 0)
        self.addCleanup(lambda: (admin.shutdown(), admin.server_close()))
        (self.platform.root / 'token').write_text('d'*64)
        with patch.object(bridge, 'catalog', return_value=[]):
            for token, code in [(guard.config['token'], 403), ('d'*64, 200)]:
                conn = http.client.HTTPConnection('127.0.0.1', admin.server_port, timeout=2)
                try:
                    conn.request('GET', '/api/workflows', headers={'Authorization': 'Bearer ' + token})
                    response = conn.getresponse(); self.assertEqual(response.status, code); response.read()
                finally: conn.close()

    def run_lifecycle(self, mutation):
        config = service.load_config(self.platform.root / 'config.json', self.platform)
        servers, requested, errors = [], [], []
        ready = threading.Event()
        accepted = threading.Event()
        original_server = service.ClosingServer
        class ObservedServer(original_server):
            def get_request(self):
                result = super().get_request()
                if self is servers[0]: accepted.set()
                return result
            def serve_forever(self, *args, **kwargs):
                if self is servers[0]: ready.set()
                return super().serve_forever(*args, **kwargs)
        def bind(address, handler):
            requested.append(address)
            server = ObservedServer(('127.0.0.1', 0), handler)
            servers.append(server)
            return server
        process = Mock(process=None)
        self.managed_process = process
        def run():
            try: service.serve(config, self.platform, interval=.02)
            except Exception as error: errors.append(error)
        with patch.object(service, 'ClosingServer', side_effect=bind), patch.object(service, 'ComfyProcess', return_value=process):
            thread = threading.Thread(target=run, daemon=True); thread.start()
            try:
                self.assertTrue(ready.wait(3), errors)
                ports = [server.server_port for server in servers]
                conn = http.client.HTTPConnection('127.0.0.1', ports[0], timeout=2)
                conn.request('GET', '/v1/jobs')
                response = conn.getresponse(); self.assertEqual(response.status, 403); response.read(); conn.close()
                # An idle already accepted socket must be closed during shutdown too.
                accepted.clear()
                idle = socket.create_connection(('127.0.0.1', ports[0]), timeout=2)
                try:
                    self.assertTrue(accepted.wait(2))
                    mutation()
                    thread.join(4)
                    self.assertFalse(thread.is_alive())
                    self.assertEqual(idle.recv(1), b'')
                finally: idle.close()
                self.assertEqual(errors, [])
                self.assertEqual(requested, [(fixtures.SELF, 18772), ('127.0.0.1', 18773)])
                for port in ports:
                    with socket.socket() as sock:
                        sock.settimeout(.5)
                        self.assertNotEqual(sock.connect_ex(('127.0.0.1', port)), 0)
                process.stop.assert_called_once()
            finally:
                self.state['BackendState'] = 'Stopped'
                thread.join(4)

    def test_runtime_address_disappears_closes_ports(self):
        self.installed()
        self.run_lifecycle(lambda: self.state['Self'].update(TailscaleIPs=[]))

    def test_runtime_address_changes_closes_ports(self):
        self.installed()
        self.run_lifecycle(lambda: self.state['Self'].update(TailscaleIPs=[fixtures.OTHER]))

    def test_runtime_tailscale_disconnect_closes_ports(self):
        self.installed()
        self.run_lifecycle(lambda: self.state.update(BackendState='Stopped'))

    def test_runtime_interface_disappears_closes_ports(self):
        self.installed()
        original = self.platform.run
        def disappear():
            self.platform.run = lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, '') if argv[0] == '/sbin/ifconfig' else original(argv, **kwargs)
        self.run_lifecycle(disappear)

    def test_runtime_comfy_listener_failure_closes_ports(self):
        self.installed()
        def invalid_listener():
            child = Mock(); child.poll.return_value = None
            self.managed_process.verify_listener.side_effect = ValueError('unsafe listener')
            self.managed_process.process = child
        self.run_lifecycle(invalid_listener)

    def test_runtime_probe_timeout_closes_ports(self):
        self.installed()
        def timeout():
            self.platform.run = Mock(side_effect=subprocess.TimeoutExpired('fixture', 4))
        self.run_lifecycle(timeout)

    def test_bridge_restart_revalidates_before_binding(self):
        self.installed()
        self.run_lifecycle(lambda: self.state.update(BackendState='Stopped'))
        config = service.load_config(self.platform.root / 'config.json', self.platform)
        with patch.object(service, 'ClosingServer') as bind:
            with self.assertRaises(ValueError): service.serve(config, self.platform)
            bind.assert_not_called()
        self.state = fixtures.network()
        self.run_lifecycle(lambda: self.state['Self'].update(Online=False))

    def test_startup_failure_closes_bound_server_and_child(self):
        config = self.installed()
        server = Mock()
        process = Mock(process=None)
        with patch.object(service, 'ClosingServer', return_value=server), patch.object(service, 'ComfyProcess', return_value=process), patch.object(service, 'start_admin', side_effect=OSError('occupied')):
            with self.assertRaises(OSError): service.serve(config, self.platform)
        server.server_close.assert_called_once()
        process.stop.assert_called_once()

    def test_normal_service_exit_closes_listeners_and_rejects_late_comfy_start(self):
        config = self.installed()
        servers = []
        original = service.ClosingServer
        class ExitingServer(original):
            def serve_forever(self, *args, **kwargs):
                raise KeyboardInterrupt()  # Same unwind used by the SIGTERM handler.
        def bind(address, handler):
            server = (ExitingServer if not servers else original)(('127.0.0.1', 0), handler)
            servers.append(server)
            return server
        process = ComfyProcess(config, self.platform)
        with patch.object(service, 'ClosingServer', side_effect=bind), patch.object(service, 'ComfyProcess', return_value=process):
            with self.assertRaises(KeyboardInterrupt): service.serve(config, self.platform)
        for server in servers:
            with socket.socket() as sock:
                self.assertNotEqual(sock.connect_ex(('127.0.0.1', server.server_port)), 0)
        with patch('subprocess.Popen') as spawn:
            with self.assertRaisesRegex(ValueError, 'stopped'): process.start()
            spawn.assert_not_called()

    def test_launchagent_crash_does_not_autorestart_and_start_rechecks(self):
        self.installed()
        value = plistlib.loads(self.platform.plist.read_bytes())
        self.assertFalse(value['KeepAlive'])
        self.assertNotIn('LaunchOnlyOnce', value)
        self.platform.run = Mock(side_effect=[subprocess.CompletedProcess([], 0, 'state = exited\nlast exit code = 1'),
                                             subprocess.CompletedProcess([], 0, '')])
        self.platform.launchctl('start')
        self.assertEqual(self.platform.run.call_args.args[0][1], 'kickstart')
        # Every invocation of the service entry point reloads and checks security.
        self.platform.run = Mock(side_effect=TimeoutError('sensitive'))
        with patch.object(service, 'ClosingServer') as bind:
            with self.assertRaises(ValueError): service.serve(self.config, self.platform)
            bind.assert_not_called()

    def test_launchagent_escaping_root_rejection_and_uninstall_preservation(self):
        self.installed()
        executable = '/fixture/space & <tag> "quote" $dollar/python'
        value = plistlib.loads(self.platform.launch_plist(executable))
        self.assertEqual(value['ProgramArguments'][0], executable)
        self.assertNotIn('UserName', value)
        with patch('os.geteuid', return_value=0):
            for operation in [require_user, lambda: environment('Darwin', 'arm64', (3, 11)),
                              lambda: self.platform.launchctl('start'), lambda: self.platform.launchctl('stop'),
                              lambda: fixtures.installer.uninstall(self.platform),
                              lambda: fixtures.installer.install(self.config, self.platform)]:
                with self.assertRaises(ValueError): operation()
        marker = self.platform.root / 'user-data'; marker.write_text('keep')
        old = self.platform.plist.read_bytes()
        for changed in [{'ProgramArguments': ['/unrelated/user/program']}, {'KeepAlive': True}, {'UserName': 'root'}]:
            plist = plistlib.loads(old); plist.update(changed)
            self.platform.plist.write_bytes(plistlib.dumps(plist))
            for action in [lambda: fixtures.installer.uninstall(self.platform), lambda: self.platform.launchctl('stop')]:
                with self.assertRaises(ValueError): action()
            self.assertTrue(self.platform.plist.exists())
            self.assertTrue((self.platform.root / 'launcher.json').exists())
        self.platform.plist.write_bytes(old)
        fixtures.installer.uninstall(self.platform)
        self.assertEqual(marker.read_text(), 'keep')
        self.assertTrue((self.platform.root / 'config.json').exists())
        self.assertTrue((self.comfy / 'main.py').exists())

    def test_comfy_listener_wrong_address_or_owner_stops_owned_child(self):
        for address, pid in [('*:8188', 1234), ('0.0.0.0:8188', 1234), ('[::]:8188', 1234),
                             ('8.8.8.8:8188', 1234),
                             ('[::1]:8188', 1234), (fixtures.SELF + ':8188', 1234),
                             ('127.0.0.1:8188', 5678)]:
            with self.subTest(address=address, pid=pid):
                process = ComfyProcess(self.config, self.platform)
                child = Mock(pid=1234); child.poll.return_value = None; process.process = child
                self.platform.run = Mock(return_value=subprocess.CompletedProcess([], 0, f'p{pid}\nn{address}\n'))
                with patch('os.killpg') as kill, patch('comfy_startup.healthy') as healthy:
                    with self.assertRaisesRegex(ValueError, 'listener'): process.healthy_owned()
                    kill.assert_called_once_with(1234, 15)
                    healthy.assert_not_called()

    def test_comfy_guard_checks_every_access(self):
        process = ComfyProcess(self.config, self.platform)
        child = Mock(pid=1234); child.poll.return_value = None; process.process = child
        self.platform.run = Mock(return_value=subprocess.CompletedProcess([], 0, 'p1234\nn127.0.0.1:8188\n'))
        delegate = Mock()
        managed = service.ManagedAdapter(process, delegate)
        with patch('comfy_startup.healthy', return_value=True):
            managed.status(); managed.submit({}, 'fixture')
        self.assertEqual(self.platform.run.call_count, 2)
        self.platform.run.return_value = subprocess.CompletedProcess([], 0, 'p1234\nn*:8188\n')
        with patch('os.killpg'):
            with self.assertRaises(ValueError): managed.artifact({})
        delegate.artifact.assert_not_called()

    def test_comfy_probe_failure_is_closed_and_diagnostics_are_fixed(self):
        for result in [subprocess.CompletedProcess([], 2, '', 'private details'),
                       subprocess.CompletedProcess([], 0, 'unexpected sensitive output')]:
            process = ComfyProcess(self.config, self.platform)
            child = Mock(pid=1234); child.poll.return_value = None; process.process = child
            self.platform.run = Mock(return_value=result)
            with patch('os.killpg'), patch('comfy_startup.healthy') as healthy:
                with self.assertRaises(ValueError) as error: process.healthy_owned()
                self.assertNotIn('sensitive', str(error.exception))
                self.assertNotIn('private', str(error.exception))
                healthy.assert_not_called()

    def test_comfy_bad_endpoint_rejected_before_any_http_or_launch(self):
        for address in ['localhost', '0.0.0.0', fixtures.SELF, '8.8.8.8', '[::1]']:
            config = {**self.config, 'comfy_url': 'http://' + address + ':8188'}
            with patch('subprocess.Popen') as spawn, patch('comfy_startup.healthy') as healthy:
                with self.assertRaises(ValueError): ComfyProcess(config, self.platform).start()
                spawn.assert_not_called(); healthy.assert_not_called()


if __name__ == '__main__': unittest.main()
