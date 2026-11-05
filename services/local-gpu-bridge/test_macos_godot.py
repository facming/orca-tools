"""No real Godot, projects, configuration, network peers or application scripts."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import signal
import struct
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import uuid
import zlib

from bridge import Bridge, Server, make_handler
from bridge_platform.macos import MacOSPlatform, private_dir, write_private
from bridge_platform.macos_godot import (GodotControl, Unsupported, discover, executable, png,
                                        open_inside, validate_settings)

spec = importlib.util.spec_from_file_location('godot_mcp', Path(__file__).parent / 'plugin/client.py')
mcp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mcp)


def image(width=64, height=64):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress((b'\0' + b'\xff\0\0' * width) * height)) + chunk(b'IEND', b'')


class Child:
    pid = 765432  # Never sent to an OS API: killpg is mocked for every test.
    def __init__(self, code=0):
        self.code = code
        self.waits = 0
    def poll(self):
        return self.code
    def wait(self, timeout=None):
        self.waits += 1
        self.code = -15
        return self.code


@unittest.skipUnless(os.name == 'posix', 'macOS adapter fixtures require POSIX paths and permissions')
class GodotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.data = private_dir(self.root / 'bridge')
        self.app = self.root / 'Installed tools/Custom Godot.app'
        (self.app / 'Contents/MacOS').mkdir(parents=True)
        (self.app / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': 'org.godotengine.godot', 'CFBundleExecutable': 'Godot'}))
        self.exe = self.app / 'Contents/MacOS/Godot'
        self.exe.write_bytes(struct.pack('<IIIIIIII', 0xfeedfacf, 0x0100000c, 0, 2, 0, 0, 0, 0))
        self.exe.chmod(0o700)
        self.project = self.root / '项目 with spaces'
        self.project.mkdir()
        (self.project / 'project.godot').write_text('config_version=5\n')
        (self.project / 'scenes').mkdir()
        (self.project / 'scenes/main.tscn').write_text('[gd_scene format=3]\n')
        (self.project / 'test.gd').write_text('# synthetic, never executed\n')
        self.config = {'data_dir': str(self.data), 'native': {'godot_executable': str(self.app), 'projects': [
            {'id': 'fixture', 'root': str(self.project), 'enabled': True, 'allow_scripts': True}]}}
        self.run = Mock(return_value=subprocess.CompletedProcess([], 0, '4.5.1.stable.official.fixture\n', ''))
        self.run.side_effect = lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, 'Godot --editor --import --headless --recovery-mode', '') if '--help' in argv else self.run.return_value
        self.calls = []
        self.child = None
        def spawn(command, **kwargs):
            self.calls.append((command, kwargs))
            child = self.child or Child()
            if '--' in command:
                after = command[command.index('--')+1:]
                if after and after[0].endswith('capture.json'):
                    request = json.loads(Path(after[0]).read_text())
                    Path(request['output']).write_bytes(image(request['width'], request['height']))
            return child
        self.spawn = Mock(side_effect=spawn)
        self.kill = self.enterContext(patch('bridge_platform.macos_godot.os.killpg'))
        self.enterContext(patch('bridge_platform.macos_godot.platform.system', return_value='Darwin'))
        self.enterContext(patch('bridge_platform.macos_godot.platform.machine', return_value='arm64'))
        self.control = GodotControl(self.config, self.run, self.spawn)
        self.addCleanup(self.control.close)

    def body(self, operation='run', **args):
        return {'project_id': 'fixture', 'engine': 'godot', 'operation': operation, 'authorized': True,
                'arguments': args, 'request_id': uuid.uuid4().hex}

    def finish(self, body):
        queued = self.control.queue(body)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            value = self.control.job_status(queued['id'])
            if value['status'] in ('failed', 'succeeded', 'cancelled'):
                return value
            time.sleep(.01)
        self.fail('Mock task did not finish')

    def started(self, ident):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            with self.control.lock:
                if ident in self.control.active:
                    return
            time.sleep(.01)
        self.fail('Mock process did not start')

    def test_discovery_metadata_only_custom_location_and_explicit_binary(self):
        self.assertEqual(discover([self.app.parent]), [str(self.exe)])
        self.assertEqual(discover([], str(self.exe)), [str(self.exe)])
        self.assertEqual(discover([self.root / 'missing']), [])
        self.run.assert_not_called()
        self.spawn.assert_not_called()

    def test_arm64_and_universal_header(self):
        self.assertEqual(executable(str(self.app)), self.exe)
        self.exe.write_bytes(struct.pack('>IIIIIII', 0xcafebabe, 1, 0x0100000c, 0, 4096, 32, 12))
        self.assertEqual(executable(str(self.exe)), self.exe)
        self.exe.write_bytes(struct.pack('<IIIIIIII', 0xfeedfacf, 0x01000007, 0, 2, 0, 0, 0, 0))
        with self.assertRaises(Unsupported): executable(str(self.exe))
        self.assertEqual(self.control.status()['state'], 'unsupported')
        self.run.assert_not_called()

    def test_reject_scripts_permissions_relative_missing_and_link_executables(self):
        for data in (b'#!/bin/sh\n', b'ELF', b'MZ'):
            self.exe.write_bytes(data)
            with self.assertRaises(ValueError): executable(str(self.exe))
        self.exe.chmod(0o600)
        with self.assertRaises(ValueError): executable(str(self.exe))
        with self.assertRaises(ValueError): executable('relative')
        link = self.root / 'godot'
        link.symlink_to(self.exe)
        with self.assertRaises(ValueError): executable(str(link))
        self.run.assert_not_called()

    def test_version_compatibility_and_probe_environment(self):
        for version in ('3.6.stable', '4.3.stable', '4.4.stable', '4.5.dev.fixture', '5.0.stable', 'arbitrary executable'):
            self.run.return_value.stdout = version
            self.assertEqual(self.control.status()['state'], 'unsupported')
        self.run.return_value.stdout = '4.5.stable.official.fixture'
        self.assertEqual(self.control.status()['state'], 'available')
        call = self.run.call_args_list[-2]
        self.assertEqual(call.args[0], [str(self.exe), '--version'])
        self.assertFalse(call.kwargs['shell'])
        self.assertEqual(set(call.kwargs['env']), {'HOME', 'PATH', 'TMPDIR', 'LANG'})
        self.assertEqual(self.control.status(False)['state'], 'installed')
        self.run.side_effect = subprocess.TimeoutExpired(['mock'], 5)
        self.assertEqual(self.control.status()['state'], 'error')

    def test_no_config_no_execution(self):
        self.control.settings = {}
        self.assertEqual(self.control.status()['state'], 'unsupported')
        self.run.assert_not_called()
        self.kill.assert_not_called()

    def test_unsupported_host(self):
        with patch('bridge_platform.macos_godot.platform.machine', return_value='x86_64'):
            self.assertEqual(self.control.status()['state'], 'unsupported')
        self.run.assert_not_called()

    def test_authorization_and_strict_configuration(self):
        for key in ('authorized',):
            body = self.body();body.pop(key)
            with self.assertRaises(ValueError): self.control.queue(body)
        self.control.settings['projects'][0]['allow_scripts'] = False
        with self.assertRaises(ValueError): self.control.queue(self.body())
        for settings in ({'blender_executable': '/mock'}, {'godot_executable': 'relative'}, {'projects': [{'id': '../bad'}]}):
            with self.assertRaises(ValueError): validate_settings(settings)
        self.spawn.assert_not_called()

    def test_path_traversal_absolute_and_prefix(self):
        for value in ('../elsewhere.gd', '/etc/mock.gd', 'scenes/../../test.gd', 'scenes//a.gd', 'C:\\test.gd', './test.gd'):
            with self.assertRaises((ValueError, OSError)): self.control.queue(self.body('script', script=value))
        self.spawn.assert_not_called()

    def test_project_symlink_escape_and_identity_swap(self):
        outside = self.root / 'outside';outside.mkdir()
        (outside / 'file.gd').write_text('# synthetic')
        link = self.project / 'link';link.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError): self.control.queue(self.body('script', script='link/file.gd'))
        with self.assertRaises(ValueError): self.control.queue(self.body())
        link.unlink()
        self.project.rename(self.root / 'old')
        self.project.mkdir();(self.project / 'project.godot').write_text('fixture')
        with self.assertRaises(ValueError): self.control.queue(self.body())
        self.spawn.assert_not_called()

    def test_headless_import_run_and_environment_isolation(self):
        with patch.dict(os.environ, {'TOKEN': 'synthetic-secret', 'DYLD_INSERT_LIBRARIES': 'mock', 'PATH': 'untrusted'}):
            job = self.finish(self.body(headless=True, user_args=['--script', 'user-data-only']))
        self.assertEqual(job['status'], 'succeeded')
        self.assertEqual(len(self.calls), 2)
        self.assertIn('--recovery-mode', self.calls[0][0])
        self.assertIn('--import', self.calls[0][0])
        argv, options = self.calls[-1]
        self.assertIn('--headless', argv)
        self.assertEqual(argv[argv.index('--')+1:], ['--script', 'user-data-only'])
        self.assertEqual(options['cwd'], self.project)
        self.assertEqual(options['env']['PATH'], '/usr/bin:/bin')
        self.assertEqual(set(options['env']), {'PATH', 'HOME', 'TMPDIR', 'LANG'})
        self.assertFalse(options['shell']);self.assertTrue(options['start_new_session'])
        self.assertEqual(options['stdout'], subprocess.DEVNULL)
        stored = json.loads((self.control.root / job['id'] / 'job.json').read_text())
        for key in ('pid', 'started_at', 'project_identity', 'authorized'): self.assertIn(key, stored)
        self.assertNotIn('pid', job)

    def test_open_script_and_import_commands(self):
        for operation, args in [('open', {}), ('script', {'script': 'test.gd'}), ('import', {})]:
            self.calls.clear()
            self.assertEqual(self.finish(self.body(operation, **args))['status'], 'succeeded')
            argv = self.calls[-1][0]
            self.assertIn('--editor' if operation != 'script' else '--script', argv)
            if operation == 'script':
                self.assertIn('res://test.gd', argv)
                self.assertIn('--headless', argv)

    def test_nonzero_exit_and_redacted_logs(self):
        self.child = Child(9)
        job = self.finish(self.body())
        self.assertEqual(job['status'], 'failed');self.assertEqual(job['exit_code'], 9)
        self.assertIn('logs', self.control.logs(job['id']))
        self.spawn.side_effect = RuntimeError('synthetic-secret /Users/private-fixture 203.0.113.8')
        job = self.finish(self.body())
        value = json.dumps([job, self.control.logs(job['id'])])
        for secret in ('synthetic-secret', '/Users/', '203.0.113.8'): self.assertNotIn(secret, value)

    def test_timeout_terminates_owned_process(self):
        self.child = Child(None)
        job = self.finish(self.body(timeout_seconds=1))
        self.assertEqual(job['status'], 'failed')
        self.assertTrue(job['timed_out'])
        self.kill.assert_called_once_with(self.child.pid, signal.SIGTERM)
        self.assertEqual(self.child.waits, 1)

    def test_cancel_running_and_stop_do_not_adopt_external_process(self):
        self.assertEqual(self.control.stop('fixture')['jobs'], [])
        self.kill.assert_not_called()
        self.child = Child(None)
        job = self.control.queue(self.body('open'))
        self.started(job['id'])
        self.assertEqual(self.control.status()['state'], 'running')
        stopped = self.control.stop('fixture')
        self.assertEqual(stopped['jobs'][0]['status'], 'cancelled')
        self.kill.assert_called_once_with(self.child.pid, signal.SIGTERM)
        self.control.cancel(job['id']);self.assertEqual(self.kill.call_count, 1)

    def test_queued_cancel_and_service_shutdown(self):
        with patch.object(self.control, 'execute'):
            job = self.control.queue(self.body())
            self.assertEqual(self.control.cancel(job['id'])['status'], 'cancelled')
        self.spawn.assert_not_called();self.kill.assert_not_called()
        self.control.close()
        with self.assertRaises(ValueError): self.control.queue(self.body())

    def test_request_id_replay_and_conflict(self):
        body = self.body()
        job = self.finish(body)
        self.assertEqual(self.control.queue(body)['id'], job['id'])
        body['arguments'] = {'headless': True}
        with self.assertRaises(ValueError): self.control.queue(body)
        self.assertEqual(len(self.calls), 2)

    def test_restart_marks_unknown_without_signalling_or_resubmitting(self):
        with patch.object(self.control, 'execute'):
            job = self.control.queue(self.body())
        self.control.jobs[job['id']].update(pid=123456, status='running')
        self.control.save(self.control.jobs[job['id']])
        restored = GodotControl(self.config, self.run, self.spawn)
        self.addCleanup(restored.close)
        self.assertEqual(restored.job_status(job['id'])['status'], 'failed')
        self.assertIn('unknown', restored.job_status(job['id'])['error'])
        self.kill.assert_not_called();self.spawn.assert_not_called()

    def test_capture_and_artifact_integrity(self):
        job = self.finish(self.body('capture', width=64, height=64))
        self.assertEqual(job['status'], 'succeeded')
        with self.control.open_artifact(job['id'], 0) as stream:
            self.assertEqual(stream.read(), image())
        argv = self.calls[-1][0]
        self.assertNotIn('--headless', argv);self.assertNotIn('--position', argv)
        artifact = self.control.root / job['id'] / 'artifact-0.png'
        artifact.write_bytes(b'changed')
        with self.assertRaises(ValueError): self.control.open_artifact(job['id'], 0)
        artifact.unlink();artifact.symlink_to(self.exe)
        with self.assertRaises((OSError, ValueError)): self.control.open_artifact(job['id'], 0)

    def test_png_corruption_bounds_and_no_hardlink_or_symlink_reads(self):
        png(image(), 64, 64)
        for data in (b'bad', image()[:-1], image() + b'extra', image()[:30] + b'bad' + image()[33:]):
            with self.assertRaises((ValueError, zlib.error)): png(data)
        with self.assertRaises(ValueError): png(image(), 128, 128)
        outside = self.root / 'image.png';outside.write_bytes(image())
        os.link(outside, self.project / 'hard.png')
        with self.assertRaises(ValueError): open_inside(self.project, 'hard.png')
        (self.project / 'link').symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError): open_inside(self.project, 'link/image.png')

    def test_outputs_must_be_new_declared_pngs(self):
        (self.project / 'existing.png').write_bytes(image())
        for outputs in (['../image.png'], ['existing.png'], ['file.json']):
            with self.assertRaises(ValueError): self.control.queue(self.body('script', script='test.gd', output_paths=outputs))
        self.assertEqual(self.finish(self.body('script', script='test.gd', output_paths=['missing.png']))['status'], 'failed')

    def test_api_mcp_projects_capture_download_logs_stop_unsupported(self):
        config = {**self.config, 'token': 'c' * 64, 'allowed_peers': ['127.0.0.1'],
                  'comfy_url': 'http://127.0.0.1:1', 'workflow_dirs': []}
        adapter = MacOSPlatform(self.root)
        with patch.object(adapter, 'native_controls', return_value=(self.control, None)), patch.object(adapter, 'authorize_peer', return_value=True):
            bridge = Bridge(config, platform_adapter=adapter)
            server = Server(('127.0.0.1', 0), make_handler(bridge))
            thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
            self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
            client_file = self.root / 'client.fixture.json'
            client_file.write_text(json.dumps({'url': 'http://127.0.0.1:' + str(server.server_port), 'token': config['token']}))
            client = mcp.Client(client_file)
            self.assertEqual(client.call('local_projects', {})['projects'][0]['id'], 'fixture')
            job = client.call('capture_scene', {'project_id': 'fixture', 'authorized': True, 'width': 64, 'height': 64})
            for _ in range(100):
                value = client.call('native_job_status', {'job_id': job['id']})
                if value['status'] in ('succeeded', 'failed'): break
                time.sleep(.01)
            self.assertEqual(value['status'], 'succeeded')
            receipt = client.call('fetch_native_artifact', {'job_id': job['id'], 'index': 0, 'destination_dir': str(self.root / 'download')})
            self.assertEqual(Path(receipt['path']).read_bytes(), image())
            self.assertFalse(client.call('godot_logs', {'job_id': job['id']})['raw_output_available'])
            self.assertEqual(client.call('godot_stop', {'project_id': 'fixture'})['state'], 'stopped')
            for route in ('/v1/native/git', '/v1/native/read', '/v1/unity/start', '/v1/tripo/call'):
                with self.assertRaisesRegex(ValueError, 'unsupported'): client.request(route, {})
            with self.assertRaises(ValueError): client.call('capture_scene', {'project_id': 'fixture'})
            self.assertEqual(client.call('list_workflows', {}), {'workflows': []})

    def test_mcp_legacy_payloads_and_new_operations(self):
        client = object.__new__(mcp.Client)
        client.request = Mock(return_value={})
        client.call('capture_scene', {'project_id': 'fixture', 'request_id': 'a'*32})
        self.assertEqual(client.request.call_args.args, ('/v1/native/jobs', {
            'project_id': 'fixture', 'engine': 'godot', 'operation': 'capture', 'arguments': {}, 'request_id': 'a'*32}))
        client.call('godot_operation', {'project_id': 'fixture', 'operation': 'run', 'authorized': True})
        self.assertTrue(client.request.call_args.args[1]['authorized'])
        client.call('godot_cancel', {'job_id': 'a'*32})
        self.assertEqual(client.request.call_args.args, ('/v1/native/jobs/'+'a'*32+'/cancel', {}))
        with self.assertRaises(ValueError): client.call('godot_logs', {'job_id': '../bad'})
        names = {x['name'] for x in mcp.TOOLS}
        self.assertNotIn('godot_operation', names)

    def test_script_produces_validated_new_png(self):
        original = self.spawn.side_effect
        def spawn(command, **kwargs):
            if '--script' in command:
                (self.project / 'new.png').write_bytes(image())
            return original(command, **kwargs)
        self.spawn.side_effect = spawn
        job = self.finish(self.body('script', script='test.gd', output_paths=['new.png']))
        self.assertEqual(job['status'], 'succeeded')
        self.assertEqual(job['artifacts'][0]['bytes'], len(image()))

    def test_termination_escalation_and_error_recovery(self):
        self.child = Child(None)
        job = self.control.queue(self.body('open'))
        self.started(job['id'])
        self.child.wait = Mock(side_effect=[subprocess.TimeoutExpired(['mock'], 3), -9])
        self.child.poll = Mock(side_effect=[None, None, -9, -9])
        self.control.cancel(job['id'])
        self.assertEqual([x.args[1] for x in self.kill.call_args_list], [signal.SIGTERM, signal.SIGKILL])
        self.child = None
        self.assertEqual(self.finish(self.body('open'))['status'], 'succeeded')

    def test_service_health_godot_independent_and_network_required(self):
        from macos_service import health
        bridge = Mock()
        bridge.native = self.control
        bridge.config = self.config
        bridge.platform.root = self.data
        bridge.platform.launchctl.return_value = True
        bridge.adapters = {'comfyui.workflow': Mock()}
        process = Mock(backend={})
        process.healthy_owned.return_value = False
        with patch('macos_service.load_config', return_value=self.config), patch('macos_service.private_path'):
            result = health(bridge, process)
            self.assertFalse(result['ready'])
            self.assertEqual(result['godot']['state'], 'available')
            bridge.platform.validate_network.side_effect = ValueError('synthetic network failure')
            self.assertEqual(health(bridge, process)['godot']['state'], 'installed')

    def test_installer_bundles_adapter_without_probing_or_installing_godot(self):
        spec = importlib.util.spec_from_file_location('godot_installer', Path(__file__).resolve().parents[2] / 'scripts/install-local-gpu-macos.py')
        installer = importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
        self.assertIn('native_godot.gd', installer.FILES)
        settings = {'comfy_root': 'fixture', 'listen': '100.64.10.1', 'allowed_peers': ['100.64.10.2'],
                    'tailscale': str(self.exe), 'native': self.config['native'],
                    'input_directory': str(self.project), 'output_directory': str(self.project), 'workflow_dirs': [str(self.project)]}
        with patch.object(installer, 'validate_comfy', return_value=self.project), patch.object(installer, 'discover_python', return_value='mock'):
            config = installer.plan(settings, MacOSPlatform(self.root))
        self.assertEqual(config['native'], self.config['native'])
        self.run.assert_not_called();self.spawn.assert_not_called()

    def test_export_template_is_not_available(self):
        self.run.side_effect = lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, 'Godot --headless', '') if '--help' in argv else self.run.return_value
        self.assertEqual(self.control.status()['state'], 'unsupported')

    def test_godot_store_failure_does_not_disable_comfy_factory(self):
        with patch('bridge_platform.macos_godot.GodotControl', side_effect=ValueError('synthetic private failure')):
            native, tripo = MacOSPlatform(self.root).native_controls(self.config, lambda: None)
        self.assertEqual(native.status()['state'], 'error')
        self.assertIsNone(tripo)
        native.close()

    def test_every_native_route_rechecks_auth_on_keepalive(self):
        import http.client
        config = {**self.config, 'token': 'c'*64, 'allowed_peers': ['127.0.0.1'], 'workflow_dirs': []}
        adapter = MacOSPlatform(self.root)
        with patch.object(adapter, 'native_controls', return_value=(self.control, None)):
            bridge = Bridge(config, platform_adapter=adapter)
        allowed = [True]
        security = Mock()
        security.revoked = threading.Event()
        security.authorize.side_effect = lambda authorization, peer: allowed[0] and authorization == 'Bearer ' + config['token']
        server = Server(('127.0.0.1', 0), make_handler(bridge, security))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        for route, method in [('/v1/native/status', 'GET'), ('/v1/native/projects', 'GET'),
                              ('/v1/native/jobs', 'POST'), ('/v1/native/godot-stop', 'POST'),
                              ('/v1/native/jobs/'+'a'*32+'/logs', 'GET'),
                              ('/v1/native/jobs/'+'a'*32+'/cancel', 'POST'),
                              ('/v1/native/artifacts/'+'a'*32+'/0', 'GET')]:
            for mode in ('token', 'peer'):
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
                allowed[0] = True
                connection.request('GET', '/v1/native/projects', headers={'Authorization': 'Bearer '+config['token']})
                response = connection.getresponse();self.assertEqual(response.status, 200);response.read()
                allowed[0] = mode != 'peer'
                connection.request(method, route, body='{}' if method == 'POST' else None, headers={
                    'Authorization': 'Bearer '+('wrong' if mode == 'token' else config['token']), 'Content-Type': 'application/json'})
                response = connection.getresponse();self.assertEqual(response.status, 403);response.read();connection.close()
        self.spawn.assert_not_called()

    def test_service_finally_closes_godot_and_comfy(self):
        import macos_service
        adapter = Mock()
        native = Mock()
        bridge = Mock(native=native)
        with patch.object(macos_service, 'RuntimeSecurity') as security, patch.object(macos_service, 'ComfyProcess') as comfy, patch.object(macos_service, 'Bridge', return_value=bridge), patch.object(macos_service, 'ClosingServer'), patch.object(macos_service, 'start_admin', side_effect=ValueError('fixture bind failure')):
            security.return_value.check.return_value = True
            with self.assertRaises(ValueError): macos_service.serve({'comfy_url':'http://127.0.0.1:8188','listen':'127.0.0.1','port':0}, adapter)
            native.close.assert_called_once()
            comfy.return_value.stop.assert_called_once_with(final=True)

    def test_rebased_cleanup_errors_still_close_both_engines(self):
        import macos_service
        for stage in ('remote', 'admin_shutdown', 'admin_close', 'monitor', 'godot'):
            with self.subTest(stage=stage):
                native, remote, admin, monitor = Mock(), Mock(), Mock(), Mock()
                failures = {'remote': remote.server_close, 'admin_shutdown': admin.shutdown,
                            'admin_close': admin.server_close, 'monitor': monitor.join, 'godot': native.close}
                failures[stage].side_effect = OSError('synthetic cleanup failure')
                bridge = Mock(native=native)
                with patch.object(macos_service, 'RuntimeSecurity') as security, patch.object(macos_service, 'ComfyProcess') as comfy, patch.object(macos_service, 'Bridge', return_value=bridge), patch.object(macos_service, 'ClosingServer', return_value=remote), patch.object(macos_service, 'start_admin', return_value=admin), patch.object(macos_service.threading, 'Thread', return_value=monitor):
                    security.return_value.check.return_value = True
                    with self.assertRaises(OSError):
                        macos_service.serve({'comfy_url':'http://127.0.0.1:8188','listen':'127.0.0.1','port':0}, Mock())
                    security.return_value.revoked.set.assert_called_once()
                    remote.server_close.assert_called_once()
                    admin.shutdown.assert_called_once()
                    admin.server_close.assert_called_once()
                    monitor.join.assert_called_once()
                    native.close.assert_called_once()
                    comfy.return_value.stop.assert_called_once_with(final=True)

    def test_native_artifacts_honor_revocation_during_open_and_read(self):
        import io
        import socket
        config = {**self.config, 'token': 'c'*64, 'allowed_peers': ['127.0.0.1'], 'workflow_dirs': []}
        adapter = MacOSPlatform(self.root)
        with patch.object(adapter, 'native_controls', return_value=(self.control, None)):
            bridge = Bridge(config, platform_adapter=adapter)
        security = Mock()
        security.revoked = threading.Event()
        security.authorize.side_effect = lambda *_: not security.revoked.is_set()
        server = Server(('127.0.0.1', 0), make_handler(bridge, security))
        thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        for stage in ('open', 'read'):
            with self.subTest(stage=stage):
                security.revoked.clear()
                class Artifact(io.BytesIO):
                    def __enter__(stream):
                        if stage == 'open': security.revoked.set()
                        return stream
                    def read(stream, *args):
                        if stage == 'read': security.revoked.set()
                        return super().read(*args)
                with patch.object(self.control, 'open_artifact', return_value=Artifact(b'synthetic-private-png')):
                    connection = socket.create_connection(('127.0.0.1', server.server_port), timeout=2)
                    try:
                        connection.sendall(('GET /v1/native/artifacts/'+'a'*32+'/0 HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n').encode())
                        data = b''
                        while chunk := connection.recv(4096): data += chunk
                    finally:
                        connection.close()
                self.assertNotIn(b'200 OK', data)
                self.assertNotIn(b'synthetic-private-png', data)
