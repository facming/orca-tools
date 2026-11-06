"""Real loopback admin, temporary files/stores; engine execution is synthetic."""
import http.client
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from bridge import Bridge
from bridge_platform.macos import MacOSPlatform, private_dir, write_private
from macos_service import start_admin


def fixture(root):
    project = root / '项目 with spaces'
    project.mkdir()
    (project / 'project.godot').write_text('[application]\nconfig/name="Fixture"\n')
    (project / 'main.gd').write_text('extends SceneTree\nfunc _initialize():\n    quit()\n')
    (project / 'asset.glb').write_bytes(b'fixture binary')
    adapter = Mock()
    adapter.submit.return_value = 'fixture-prompt'
    adapter.inspect.return_value = {'status': 'succeeded', 'artifacts': []}
    platform = MacOSPlatform(root)
    private_dir(platform.root)
    write_private(platform.root / 'token', b'c' * 64)
    bridge = Bridge({'data_dir': str(platform.root), 'native': {'projects': [{'id': 'fixture', 'root': str(project), 'enabled': True, 'allow_scripts': True}]}}, adapter=adapter, platform_adapter=platform)
    bridge.native.godot.execute = lambda *_: None
    process = Mock()
    process.start.return_value = {'state': 'running'}
    process.stop.return_value = {'state': 'stopped'}
    server = start_admin(bridge, process, port=0)
    return bridge, server, project


class Workbench(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.bridge, self.server, self.project = fixture(self.root)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.bridge.native.close)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, command, token='c' * 64):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
        self.addCleanup(conn.close)
        conn.request('POST', '/api/workbench', json.dumps(command), {'Authorization': 'Bearer ' + token, 'Origin': 'http://127.0.0.1:' + str(self.server.server_port), 'Content-Type': 'application/json'})
        response = conn.getresponse()
        return response.status, json.loads(response.read())

    def test_discovery_skips_unrelated_malformed_application_metadata(self):
        from bridge_platform import macos_godot, macos_blender, macos_unity
        for name in ('Broken.app', 'Unity Hub.app', 'Unity.app'):
            contents = self.root / name / 'Contents'
            contents.mkdir(parents=True)
            (contents / 'Info.plist').write_text('<plist><dict>broken')
        self.assertEqual(macos_godot.discover([self.root]), [])
        self.assertEqual(macos_blender.discover([self.root]), [])
        self.assertEqual(macos_unity.discover([self.root]), {'hubs': [], 'editors': []})

    def test_real_files_project_selection_preview_and_editor_location(self):
        code, result = self.call({'action': 'projects'})
        self.assertEqual(code, 200)
        self.assertEqual(result['projects'][0]['id'], 'fixture')
        base = {'engine': 'godot', 'project_id': 'fixture'}
        code, result = self.call(dict(base, action='files', path=''))
        self.assertEqual(code, 200)
        self.assertIn('main.gd', [e['name'] for e in result['entries']])
        self.assertEqual(self.call(dict(base, action='read', path='main.gd'))[1]['text'], 'extends SceneTree\nfunc _initialize():\n    quit()\n')
        self.assertEqual(self.call(dict(base, action='location', path='main.gd'))[1]['path'], str(self.project / 'main.gd'))
        self.assertIsNone(self.call(dict(base, action='read', path='asset.glb'))[1]['text'])
        self.assertEqual(self.call(dict(base, action='location', path='asset.glb'))[0], 400)
        (self.project / 'escape.gd').symlink_to('/etc/passwd')
        for file in ['../secret', '/etc/passwd', 'escape.gd']:
            self.assertEqual(self.call(dict(base, action='read', path=file))[0], 400)
        self.assertEqual(self.call(dict(base, action='read', path='main.gd', root='/etc'))[0], 400)
        self.assertEqual(self.call({'action': 'projects'}, token='d' * 64)[0], 403)

    def test_native_submit_deduplicates_and_cancels_existing_engine_task(self):
        command = {'action': 'submit', 'engine': 'godot', 'project_id': 'fixture', 'task': 'import', 'arguments': {}, 'request_id': 'a' * 32, 'authorized': True}
        code, job = self.call(command)
        self.assertEqual(code, 200)
        self.assertEqual(job['status'], 'queued')
        self.assertEqual(self.call(command)[1]['id'], job['id'])
        self.assertEqual(self.call(dict(command, authorized=False))[0], 400)
        self.assertEqual(self.call({'action': 'cancel', 'engine': 'blender', 'id': job['id']})[0], 400)
        self.assertEqual(self.call({'action': 'cancel', 'engine': 'godot', 'id': job['id']})[1]['status'], 'cancelled')
        self.assertEqual(self.call({'action': 'jobs'})[1]['jobs'][0]['status'], 'cancelled')

    def test_comfy_import_execute_inspect_recover_and_no_overwrite(self):
        workflow = json.loads((Path(__file__).parent / 'example-workflow.json').read_text())
        self.assertEqual(self.call({'action': 'workflow-import', 'workflow': workflow})[0], 200)
        self.assertEqual(self.call({'action': 'workflow-import', 'workflow': workflow})[0], 400)
        code, job = self.call({'action': 'workflow-submit', 'workflow_id': 'connection-test', 'parameters': {}, 'request_id': 'b' * 32})
        self.assertEqual(code, 200)
        self.assertEqual(self.call({'action': 'inspect', 'engine': 'comfyui', 'id': job['id']})[1]['status'], 'succeeded')
        self.assertEqual(self.call({'action': 'comfy-start'})[1]['state'], 'running')
        self.assertEqual(self.call({'action': 'comfy-stop'})[1]['state'], 'stopped')
        self.assertEqual(self.call({'action': 'rpc', 'method': 'shell.exec'})[0], 400)


if __name__ == '__main__':
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == '--serve':
        root = Path(sys.argv[2]).resolve()
        bridge, server, project = fixture(root)
        write_private(bridge.platform.root / 'config.json', json.dumps({'platform': 'darwin', 'data_dir': str(bridge.platform.root)}).encode())
        write_private(bridge.platform.root / 'launcher.json', json.dumps({'platform': 'darwin', 'label': 'org.orca.local-gpu-bridge'}).encode())
        print(json.dumps({'port': server.server_port}), flush=True)
        try:
            sys.stdin.read()
        finally:
            server.shutdown()
            server.server_close()
            bridge.native.close()
    else:
        unittest.main()
