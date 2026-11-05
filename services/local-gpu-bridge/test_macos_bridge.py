"""Offline fixtures only: temporary HOME, mock Tailscale/launchctl and loopback ComfyUI."""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request
import uuid

from bridge import Bridge, ComfyAdapter, Server, make_handler, apply_parameters, validate_workflow
from bridge_platform.macos import (MacOSPlatform, ComfyProcess, environment, discover_python, validate_comfy,
    probe_python, private_dir, private_path, write_private, tail_ip, LABEL)
from macos_service import load_config, health, start_admin, ManagedAdapter

SOURCE = Path(__file__).parent
spec = importlib.util.spec_from_file_location('mac_installer', SOURCE.parents[1] / 'scripts/install-local-gpu-macos.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
spec = importlib.util.spec_from_file_location('mock_mcp_client', SOURCE / 'plugin/client.py')
mcp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mcp)
# Synthetic addresses only; never bind them or access real Tailscale.
SELF, PEER, OTHER = '100.64.10.1', '100.64.10.2', '100.64.10.3'


def network():
    return {'BackendState': 'Running', 'Self': {'ID': 'synthetic-self', 'Online': True, 'TailscaleIPs': [SELF]},
            'Peer': {'fixture': {'ID': 'synthetic-peer', 'Online': True, 'TailscaleIPs': [PEER]}}}


@unittest.skipUnless(sys.platform == 'darwin', 'macOS permission and LaunchAgent fixtures')
class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # macOS /var is a symlink; use canonical temporary paths.
        self.home = Path(self.temp.name).resolve()
        self.state = network()
        self.calls = []
        self.loaded = False
        def run(argv, **kw):
            self.calls.append(argv)
            if argv[0] == '/sbin/ifconfig':
                return subprocess.CompletedProcess(argv, 0, 'utun9: flags=8051\n\tinet ' + SELF + ' netmask 0xffffffff\n', '')
            if 'status' in argv:
                return subprocess.CompletedProcess(argv, 0, json.dumps(self.state), '')
            if 'print' in argv:
                return subprocess.CompletedProcess(argv, 0 if self.loaded else 1, 'state = running' if self.loaded else '', '')
            if 'bootstrap' in argv: self.loaded = True
            if 'bootout' in argv: self.loaded = False
            return subprocess.CompletedProcess(argv, 0, '', '')
        self.platform = MacOSPlatform(self.home, run)
        self.comfy = self.home / 'Comfy 测试 root'
        for directory in ['comfy', 'input', 'output', 'user/default/workflows', '.venv/bin']:
            (self.comfy / directory).mkdir(parents=True, exist_ok=True)
        (self.comfy / 'main.py').write_text('# synthetic; never executed\n')
        (self.comfy / 'comfy/cli_args.py').write_text('--disable-all-custom-nodes --disable-api-nodes')
        self.python = self.comfy / '.venv/bin/python3'
        self.python.write_text('# synthetic; never executed\n')
        self.python.chmod(0o700)
        self.settings = {'comfy_root': str(self.comfy), 'listen': SELF, 'allowed_peers': [PEER], 'tailscale': str(self.python)}
        self.config = installer.plan(self.settings, self.platform)
        self.config.update(self_node_id='synthetic-self', peer_ids={PEER: 'synthetic-peer'})

    def installed(self):
        with patch.object(installer, 'probe_python', return_value={'backend': 'mps', 'cpu_fallback': False}):
            installer.install(self.config, self.platform)
        return load_config(self.platform.root / 'config.json', self.platform)

    def test_arm64_python_version_and_rosetta(self):
        self.assertEqual(environment('Darwin', 'arm64', (3, 10))['architecture'], 'arm64')
        for args in [('Linux', 'arm64', (3, 12)), ('Darwin', 'x86_64', (3, 12)), ('Darwin', 'arm64', (3, 9))]:
            with self.assertRaises(ValueError): environment(*args)

    def test_python_discovery_preserves_venv_symlink(self):
        self.assertEqual(discover_python(self.comfy), str(self.python))
        self.python.unlink()
        with self.assertRaises(ValueError): discover_python(self.comfy)
        self.python.symlink_to(sys.executable)
        self.assertEqual(discover_python(self.comfy), str(self.python))

    def test_root_and_custom_node_safety_flags(self):
        self.assertEqual(validate_comfy(self.comfy), self.comfy)
        (self.comfy / 'main.py').unlink()
        with self.assertRaises(ValueError): validate_comfy(self.comfy)
        (self.comfy / 'main.py').touch()
        (self.comfy / 'comfy/cli_args.py').write_text('old version')
        with self.assertRaises(ValueError): validate_comfy(self.comfy)

    def test_mps_and_cpu_probe_isolated_and_no_install(self):
        for mps_available in [True, False]:
            run = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps({'version': [3, 11], 'architecture': 'arm64', 'mps': mps_available})))
            result = probe_python(str(self.python), run)
            self.assertEqual(result['backend'], 'mps' if mps_available else 'cpu')
            self.assertEqual(run.call_args.args[0][1:3], ['-I', '-c'])
            self.assertEqual(run.call_args.kwargs['cwd'], '/')
        with self.assertRaisesRegex(ValueError, 'probe failed'):
            probe_python(str(self.python), Mock(side_effect=RuntimeError('secret fixture-token')))

    def test_install_token_permissions_plist_and_no_comfy_execution(self):
        config = self.installed()
        self.assertRegex(config['token'], r'^[a-f0-9]{64}$')
        raw = (self.platform.root / 'config.json').read_text()
        self.assertNotIn(config['token'], raw)
        for name in ['token', 'config.json', 'launcher.json', 'client.local.json']:
            private_path(self.platform.root / name)
        for folder in [self.platform.root, self.platform.logs, self.platform.cache, self.platform.root / 'app']:
            private_path(folder, True)
        plist = plistlib.loads(self.platform.plist.read_bytes())
        self.assertEqual(plist['Label'], LABEL)
        self.assertEqual(plist['Umask'], 0o077)
        self.assertFalse(plist['KeepAlive'])
        self.assertNotIn(config['token'], json.dumps(plist))
        self.assertNotIn('UserName', plist)
        self.assertTrue(any('-lint' in c for c in self.calls))
        self.assertTrue(any('bootstrap' in c for c in self.calls))
        with self.assertRaises(ValueError): installer.install(self.config, self.platform)

    def test_private_paths_reject_permissions_symlinks_and_overwrite(self):
        folder = private_dir(self.home / 'private')
        file = folder / 'token'
        write_private(file, b'synthetic')
        with self.assertRaises(FileExistsError): write_private(file, b'replacement')
        file.chmod(0o644)
        with self.assertRaises(ValueError): private_path(file)
        file.unlink(); file.symlink_to(self.python)
        with self.assertRaises(ValueError): private_path(file)
        with self.assertRaises(OSError): write_private(file, b'replacement')
        folder.chmod(0o755)
        with self.assertRaises(ValueError): private_dir(folder)
        folder.chmod(0o700)

    def test_tailscale_binding_and_identity_fail_closed(self):
        self.platform.validate_network(self.config)
        self.assertTrue(self.platform.authorize_peer(self.config, PEER))
        self.assertFalse(self.platform.authorize_peer(self.config, OTHER))
        for address in ['0.0.0.0', '127.0.0.1', '192.168.1.2', '8.8.8.8', '::', '2001:db8::1', '100.1.2.3']:
            with self.assertRaises(ValueError): tail_ip(address)
        with self.assertRaises(ValueError): self.platform.validate_network({**self.config, 'listen': OTHER})
        self.state['Self']['ID'] = 'changed'
        self.assertFalse(self.platform.authorize_peer(self.config, PEER))
        self.state = network(); self.state['Peer']['fixture']['ID'] = 'reassigned-ip'
        self.assertFalse(self.platform.authorize_peer(self.config, PEER))

    def test_tailscale_offline_changed_ip_missing_peer_and_timeout(self):
        for mutation in [lambda s: s.update(BackendState='Stopped'), lambda s: s['Self'].update(Online=False),
                         lambda s: s['Self'].update(TailscaleIPs=[OTHER]), lambda s: s['Peer'].clear(),
                         lambda s: s['Peer']['fixture'].update(Online=False)]:
            self.state = network(); mutation(self.state)
            self.assertFalse(self.platform.authorize_peer(self.config, PEER))
        self.platform.run = Mock(side_effect=subprocess.TimeoutExpired('fixture', 4))
        self.assertFalse(self.platform.authorize_peer(self.config, PEER))

    def test_launchctl_lifecycle_uses_current_user_no_shell(self):
        self.installed()
        self.loaded = False
        self.calls.clear()
        for action in ['start', 'status', 'stop']: self.platform.launchctl(action)
        self.assertEqual(self.calls[1][1], 'bootstrap')
        self.assertEqual(self.calls[-1][1], 'bootout')
        self.assertIn('gui/' + str(os.getuid()), self.calls[1])
        self.platform.run = Mock(return_value=subprocess.CompletedProcess([], 1, '', 'sensitive'))
        self.assertFalse(self.platform.launchctl('status'))
        with self.assertRaisesRegex(ValueError, 'LaunchAgent operation failed'): self.platform.launchctl('start')

    def test_loaded_exited_agent_can_restart_after_tailnet_recovers(self):
        self.installed()
        self.platform.run = Mock(side_effect=[subprocess.CompletedProcess([], 0, 'state = exited'), subprocess.CompletedProcess([], 0)])
        self.platform.launchctl('start')
        self.assertEqual(self.platform.run.call_args.args[0][1], 'kickstart')

    def test_uninstall_retains_user_data_and_token(self):
        self.installed()
        installer.uninstall(self.platform)
        self.assertFalse(self.platform.plist.exists())
        self.assertFalse((self.platform.root / 'launcher.json').exists())
        self.assertTrue((self.platform.root / 'token').exists())
        self.assertTrue((self.comfy / 'main.py').exists())
        self.assertTrue((self.comfy / 'user/default/workflows').is_dir())

    def test_health_requires_every_check_and_live_api(self):
        config = self.installed()
        process = Mock(backend={'backend': 'mps', 'cpu_fallback': False})
        process.healthy_owned.return_value = True
        backend = Mock(); backend.status.return_value = {'online': True}
        bridge = Bridge(config, backend, self.platform)
        self.assertTrue(health(bridge, process)['ready'])
        backend.status.return_value = {'online': False}
        self.assertFalse(health(bridge, process)['ready'])
        backend.status.return_value = {'online': True}
        self.state['BackendState'] = 'Stopped'
        self.assertFalse(health(bridge, process)['ready'])
        self.state = network()
        (self.platform.root / 'token').chmod(0o644)
        self.assertFalse(health(bridge, process)['ready'])

    def test_plist_system_lint_and_installed_isolated_imports(self):
        self.installed()
        if sys.platform == 'darwin':
            result = subprocess.run(['/usr/bin/plutil', '-lint', str(self.platform.plist)], capture_output=True)
            self.assertEqual(result.returncode, 0)
        result = subprocess.run([sys.executable, '-I', str(self.platform.root / 'app/macos_service.py'), '--help'], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_config_permissions_token_and_endpoint_changes_rejected(self):
        self.installed()
        file = self.platform.root / 'config.json'
        config = json.loads(file.read_text())
        for change in [{'comfy_url': 'http://0.0.0.0:8188'}, {'native': {'enabled': True}}, {'platform': 'win32'}]:
            file.write_text(json.dumps({**config, **change}))
            with self.assertRaises(ValueError): load_config(file, self.platform)
        file.write_text(json.dumps(config)); file.chmod(0o644)
        with self.assertRaises(ValueError): load_config(file, self.platform)
        file.chmod(0o600)
        (self.platform.root / 'token').write_text('invalid')
        with self.assertRaises(ValueError): load_config(file, self.platform)

    def test_preview_is_read_only_and_rejects_unrestricted_options(self):
        self.assertFalse(self.platform.root.exists())
        self.assertEqual(self.calls, [])
        for key in ['native', 'shell', 'args', 'tripo']:
            with self.assertRaises(ValueError): installer.plan({**self.settings, key: 'untrusted'}, self.platform)
        self.assertFalse(self.platform.plist.exists())

    def test_log_exception_details_are_removed(self):
        import logging
        from macos_service import FixedDiagnostics
        stream = io.StringIO(); handler = logging.StreamHandler(stream); handler.addFilter(FixedDiagnostics())
        logger = logging.getLogger('fixture-redaction'); logger.addHandler(handler); logger.propagate = False
        try:
            try: raise ValueError('fixture-token /fixture/private 100.64.10.1')
            except ValueError: logger.exception('Failure at /fixture/private')
            self.assertNotIn('fixture-token', stream.getvalue())
            self.assertNotIn('/fixture/private', stream.getvalue())
            self.assertNotIn('100.64.10.1', stream.getvalue())
        finally: logger.removeHandler(handler)

    def test_process_cpu_flags_stop_timeout_and_external_ownership(self):
        process = ComfyProcess(self.config, self.platform)
        child = Mock(pid=987654); child.poll.return_value = None
        with patch.object(process, 'verify_listener', return_value=True), patch('comfy_startup.healthy', return_value=True), patch('socket.socket') as sock, patch('bridge_platform.macos.probe_python', return_value={'backend': 'cpu', 'cpu_fallback': True}), patch('subprocess.Popen', return_value=child) as spawn:
            sock.return_value.__enter__.return_value.connect_ex.return_value = 1
            self.assertEqual(process.start(timeout=1)['backend'], 'cpu')
            argv = spawn.call_args.args[0]
            self.assertIn('--cpu', argv)
            self.assertIn('--disable-all-custom-nodes', argv)
            self.assertEqual(argv[argv.index('--listen')+1], '127.0.0.1')
            self.assertTrue(spawn.call_args.kwargs['start_new_session'])
            self.assertNotIn('token', spawn.call_args.kwargs['env'])
        with patch('os.killpg') as kill:
            process.stop(); self.assertEqual(kill.call_args.args[0], child.pid)
        with patch('socket.socket') as sock, patch('subprocess.Popen') as spawn:
            sock.return_value.__enter__.return_value.connect_ex.return_value = 0
            with self.assertRaisesRegex(ValueError, 'external'): process.start()
            spawn.assert_not_called()
        with patch('comfy_startup.healthy', return_value=False), patch('socket.socket') as sock, patch('bridge_platform.macos.probe_python', return_value={'backend': 'mps', 'cpu_fallback': False}), patch('subprocess.Popen', return_value=child), patch('os.killpg') as kill:
            sock.return_value.__enter__.return_value.connect_ex.return_value = 1
            with self.assertRaisesRegex(ValueError, 'timed out'): process.start(timeout=0)
            kill.assert_called()


@unittest.skipUnless(sys.platform == 'darwin', 'macOS Bridge integration fixtures')
class ComfyHTTP(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.pending, self.running, self.history = [], [], {}
        owner = self
        import http.server
        class Comfy(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                if self.path.startswith('/view?'):
                    body = b'synthetic-artifact'
                else:
                    value = {'queue_running': owner.running, 'queue_pending': owner.pending} if self.path == '/queue' else owner.history if self.path.startswith('/history/') else {}
                    body = json.dumps(value).encode()
                self.send_response(200); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if self.path == '/prompt':
                    owner.pending.append([1, 'synthetic-prompt'])
                    value = {'prompt_id': 'synthetic-prompt'}
                else:
                    owner.pending[:] = [p for p in owner.pending if p[1] not in body['delete']]; value = {}
                data = json.dumps(value).encode()
                self.send_response(200); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
        self.comfy = Server(('127.0.0.1', 0), Comfy)
        threading.Thread(target=self.comfy.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (self.comfy.shutdown(), self.comfy.server_close()))
        def run(argv, **kwargs):
            output = 'utun9: flags=8051\n\tinet ' + SELF + ' netmask 0xffffffff\n' if argv[0] == '/sbin/ifconfig' else json.dumps(network())
            return subprocess.CompletedProcess(argv, 0, output)
        self.platform = MacOSPlatform(self.root, Mock(side_effect=run))
        self.config = {'data_dir': str(self.root), 'token': 'b'*64, 'listen': SELF, 'allowed_peers': [PEER], 'peer_ids': {PEER: 'synthetic-peer'}, 'self_node_id': 'synthetic-self', 'tailscale': '/fixture/never-executed'}
        authorize = self.platform.authorize_peer
        self.platform.authorize_peer = lambda config, peer: authorize(config, PEER) if peer == '127.0.0.1' else False
        self.backend = ComfyAdapter('http://127.0.0.1:' + str(self.comfy.server_port))
        self.bridge = Bridge(self.config, self.backend, self.platform)
        (self.root / 'workflows').mkdir()
        self.workflow = json.loads((SOURCE / 'example-workflow.json').read_text())
        (self.root / 'workflows/connection-test.json').write_text(json.dumps(self.workflow))
        self.server = Server(('127.0.0.1', 0), make_handler(self.bridge))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (self.server.shutdown(), self.server.server_close()))
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)
        file = self.root / 'mock-client.json'; file.write_text(json.dumps({'url': self.url, 'token': self.config['token']}))
        self.client = mcp.Client(file)

    def submit(self):
        return self.client.call('run_workflow', {'workflow_id': 'connection-test', 'parameters': {}, 'request_id': uuid.uuid4().hex})

    def test_unchanged_mcp_workflows_submit_query_artifact_and_restart(self):
        self.assertTrue(self.client.call('gpu_status', {})['adapters'][0]['online'])
        self.assertEqual(self.client.call('list_workflows', {})['workflows'][0]['id'], 'connection-test')
        job = self.submit()
        self.assertEqual(self.client.call('job_status', {'job_id': job['id']})['status'], 'queued')
        self.pending.clear()
        self.history['synthetic-prompt'] = {'status': {'completed': True}, 'outputs': {'1': {'images': [{'filename': 'synthetic.png', 'type': 'output'}]}}}
        restored = Bridge(self.config, self.backend, self.platform)
        self.assertEqual(restored.inspect(job['id'])['status'], 'succeeded')
        receipt = self.client.call('fetch_artifact', {'job_id': job['id'], 'index': 0, 'destination_dir': str(self.root / 'artifacts')})
        self.assertEqual(Path(receipt['path']).read_bytes(), b'synthetic-artifact')
        self.assertEqual(len(restored.jobs), 1)

    def test_queued_cancel_running_refusal_and_parameter_validation(self):
        job = self.submit()
        self.running[:] = self.pending; self.pending.clear()
        with self.assertRaises(ValueError): self.client.call('cancel_pending_job', {'job_id': job['id']})
        self.pending[:] = self.running; self.running.clear()
        self.assertEqual(self.client.call('cancel_pending_job', {'job_id': job['id']})['status'], 'cancelled')
        for params in [{'unknown': 1}, {'width': True}, {'width': -1}]:
            with self.assertRaises(ValueError): apply_parameters(self.workflow, params)
        with self.assertRaises(ValueError): validate_workflow({'id': 'bad', 'prompt': {'nodes': []}})

    def test_wrong_token_peer_native_routes_and_redaction(self):
        self.client.token = 'wrong-secret'
        with self.assertRaises(ValueError) as error: self.client.call('gpu_status', {})
        self.assertNotIn('wrong-secret', str(error.exception))
        self.client.token = self.config['token']
        for route in ['/v1/native/jobs', '/v1/tripo/status', '/v1/vroid/tools', '/v1/unity/start']:
            with self.assertRaises(ValueError): self.client.request(route)
        self.config['allowed_peers'] = [OTHER]
        with self.assertRaises(ValueError): self.client.call('gpu_status', {})
        self.config['allowed_peers'] = [PEER]
        with patch.object(self.bridge, 'catalog', side_effect=ValueError('fixture-secret ' + self.config['token'])):
            with self.assertRaises(ValueError) as error: self.client.call('list_workflows', {})
            self.assertNotIn(self.config['token'], str(error.exception))
            self.assertNotIn('fixture-secret', str(error.exception))

    def test_bridge_comfy_offline_and_tailnet_loss(self):
        with patch.object(self.backend, 'request', side_effect=ValueError('offline')):
            self.assertFalse(self.client.call('gpu_status', {})['adapters'][0]['online'])
            with self.assertRaises(ValueError): self.submit()
        self.platform.run.side_effect = TimeoutError('private details')
        with self.assertRaises(ValueError): self.client.call('gpu_status', {})
        with patch.object(self.client.opener, 'open', side_effect=urllib.error.URLError('offline')):
            with self.assertRaises(ValueError): self.client.call('gpu_status', {})

    def test_admin_csrf_no_native_and_session_not_bearer(self):
        process = Mock(backend={'backend': 'cpu', 'cpu_fallback': True})
        process.start.return_value = {'state': 'ready'}
        admin = start_admin(self.bridge, process, 0)
        self.addCleanup(lambda: (admin.shutdown(), admin.server_close()))
        url = 'http://127.0.0.1:' + str(admin.server_port)
        with urllib.request.urlopen(url) as response: html = response.read().decode()
        self.assertNotIn(self.config['token'], html)
        session = re.search("const token='([a-f0-9]{64})'", html)[1]
        headers = {'X-Admin-Token': session, 'Origin': url, 'Content-Type': 'application/json'}
        with urllib.request.urlopen(urllib.request.Request(url + '/api/start', data=b'{}', headers=headers)) as response:
            self.assertEqual(response.status, 200)
        for route, origin in [('/api/start', 'https://foreign.invalid'), ('/api/native/setup', url)]:
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(urllib.request.Request(url + route, data=b'{}', headers={**headers, 'Origin': origin}))
            error.exception.close()

    def test_url_redirects_and_managed_ownership_guard(self):
        for url in ['http://0.0.0.0:8188', 'http://127.0.0.1:8188/?x=1', 'http://user:secret@127.0.0.1:8188']:
            with self.assertRaises(ValueError): ComfyAdapter(url)
        from bridge import NoRedirect
        with self.assertRaises(ValueError): NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.invalid')
        proc = Mock(); proc.healthy_owned.return_value = False
        with self.assertRaises(ValueError): ManagedAdapter(proc, self.backend).submit({}, 'fixture')


if __name__ == '__main__': unittest.main(verbosity=2)
