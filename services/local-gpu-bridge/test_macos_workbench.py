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
    adapter.inspect.return_value = {'status': 'succeeded', 'artifacts': [{'filename': 'fixture.png', 'subfolder': '', 'type': 'output'}]}
    import io
    from test_macos_godot import image
    adapter.artifact.side_effect = lambda _: io.BytesIO(image())
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

    def test_selected_engine_probe_and_session_only_configuration(self):
        import plistlib, struct, subprocess
        app = self.root / 'Godot.app'
        (app/'Contents/MacOS').mkdir(parents=True)
        (app/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':'org.godotengine.godot', 'CFBundleExecutable':'Godot'}))
        exe = app/'Contents/MacOS/Godot'
        exe.write_bytes(struct.pack('<IIIIIIII', 0xfeedfacf, 0x0100000c, 0, 2, 0, 0, 0, 0)); exe.chmod(0o700)
        control = self.bridge.native.godot
        control.run = Mock(side_effect=lambda args, **kw: subprocess.CompletedProcess(args, 0, 'Godot --editor --import --headless --recovery-mode' if '--help' in args else '4.5.1.stable.official', ''))
        command = dict(action='engine-check', engine='godot', path=str(app), authorized=True)
        self.assertEqual(self.call(command)[1]['state'], 'available')
        self.assertNotIn('godot_executable', self.bridge.config['native'])
        self.assertEqual(self.call(dict(command, path=str(self.root/'missing.app')))[1]['state'], 'not_installed')
        exe.chmod(0o777)
        self.assertEqual(self.call(command)[1]['state'], 'incompatible')
        exe.chmod(0o700)
        control.run.side_effect = subprocess.TimeoutExpired('Godot', 5)
        self.assertEqual(self.call(command)[1]['state'], 'runtime_failed')
        self.assertEqual(self.call(dict(command, authorized=False))[0], 400)

    def test_edit_rejects_disguised_binary_and_links(self):
        base = dict(action='edit', engine='godot', project_id='fixture')
        self.assertEqual(self.call(dict(base, path='main.gd'))[0], 200)
        (self.project / 'binary.txt').write_bytes(b'hello\x00binary')
        (self.project / 'bad.txt').write_bytes(b'\xff')
        (self.project / 'linked.txt').symlink_to(self.project / 'main.gd')
        os.link(self.project / 'main.gd', self.project / 'hard.txt')
        for name in ('', 'asset.glb', 'binary.txt', 'bad.txt', 'linked.txt', 'hard.txt', '../main.gd'):
            self.assertEqual(self.call(dict(base, path=name))[0], 400)

    def test_workflow_versions_conflict_delete_restore_and_unknown_submission(self):
        workflow = json.loads((Path(__file__).parent / 'example-workflow.json').read_text())
        self.assertEqual(self.call(dict(action='workflow-import', workflow=workflow))[0], 200)
        old = self.call(dict(action='workflows'))[1]['workflows'][0]['revision']
        workflow['description'] = 'Updated reviewed graph'
        command = dict(action='workflow-update', workflow=workflow, expected_revision=old)
        self.assertEqual(self.call(command)[0], 200)
        self.assertEqual(self.call(command)[0], 400)
        current = self.call(dict(action='workflows'))[1]['workflows'][0]['revision']
        self.assertNotEqual(old, current)
        self.assertEqual(self.call(dict(action='workflow-delete', workflow_id=workflow['id'], expected_revision=current))[0], 200)
        history = self.call(dict(action='workflow-versions', workflow_id=workflow['id']))[1]
        self.assertEqual(len(history['versions']), 2)
        self.assertIsNone(history['current_revision'])
        self.assertEqual(self.call(dict(action='workflow-restore', workflow_id=workflow['id'], revision=old, expected_revision=None))[0], 200)
        self.bridge.adapters['comfyui.workflow'].submit.side_effect = ValueError('lost acknowledgement')
        submit = dict(action='workflow-submit', workflow_id=workflow['id'], parameters={}, request_id='c'*32)
        self.assertEqual(self.call(submit)[0], 400)
        self.assertEqual(self.call(submit)[1]['status'], 'unknown')
        self.bridge.adapters['comfyui.workflow'].submit.assert_called_once()

    def test_resource_copy_preserves_source_and_refuses_overwrite_or_escape(self):
        source = self.root / 'download.obj'
        source.write_text('v 0 0 0\n')
        command = dict(action='resource-import', engine='godot', project_id='fixture', path='new.obj', source=str(source))
        self.assertEqual(self.call(command)[0], 200)
        self.assertEqual((self.project / 'new.obj').read_bytes(), source.read_bytes())
        self.assertEqual(self.call(command)[0], 400)
        for name in ('../escape.obj', 'script.gd', '/tmp/escape.obj'):
            self.assertEqual(self.call(dict(command, path=name))[0], 400)
        link = self.root / 'linked.obj'
        link.symlink_to(source)
        self.assertEqual(self.call(dict(command, source=str(link), path='link.obj'))[0], 400)

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
        self.assertEqual(self.call({'action': 'artifact', 'engine': 'comfyui', 'id': job['id'], 'index': 0})[1]['mime'], 'image/png')
        exported = dict(action='artifact-export', engine='comfyui', id=job['id'], index=0, target_engine='godot', project_id='fixture', path='result.png')
        self.assertEqual(self.call(exported)[0], 200)
        self.assertEqual(self.call(exported)[0], 400)
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
