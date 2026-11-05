"""Godot MCP consent contract; in-memory MCP, temporary projects, no renderer."""
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

from native_control import NativeControl
from test_bridge import client_module as mcp

CALLS = {
    'capture_scene': {'project_id': 'fixture'},
    'run_project_script': {'project_id': 'fixture', 'script': 'test.gd'},
    'godot_operation': {'project_id': 'fixture', 'operation': 'run'},
}


def exchange(client, requests):
    stdin = SimpleNamespace(buffer=io.BytesIO(b''.join(json.dumps(r).encode() + b'\n' for r in requests)))
    stdout = io.StringIO()
    with patch.object(mcp.sys, 'stdin', stdin), patch.object(mcp.sys, 'stdout', stdout):
        mcp.serve(client, mcp.GODOT_TOOLS)
    return [json.loads(line) for line in stdout.getvalue().splitlines()]


def request(name, args, ident=1):
    return {'jsonrpc': '2.0', 'id': ident, 'method': 'tools/call', 'params': {'name': name, 'arguments': args}}


class GodotSchemaTests(unittest.TestCase):
    def test_advertised_execution_schemas_require_boolean_authorization(self):
        client = Mock()
        response = exchange(client, [{'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}])[0]
        definitions = {tool['name']: tool['inputSchema'] for tool in response['result']['tools']}
        for name, args in CALLS.items():
            with self.subTest(tool=name):
                self.assertEqual(set(definitions[name]['required']), {*args, 'authorized'})
                self.assertEqual(definitions[name]['properties']['authorized'], {'type': 'boolean'})
        client.call.assert_not_called()

    def test_missing_authorization_is_rejected_before_mcp_dispatch(self):
        client = Mock()
        responses = exchange(client, [request(name, args, i) for i, (name, args) in enumerate(CALLS.items(), 1)])
        self.assertEqual(len(responses), 3)
        for response in responses:
            self.assertTrue(response['result']['isError'])
            self.assertIn('Required tool argument is missing', response['result']['content'][0]['text'])
        client.call.assert_not_called()
        client.request.assert_not_called()

    def test_nonboolean_authorization_is_rejected_before_mcp_dispatch(self):
        client = Mock()
        for value in (None, 0, 1, 'true', 'false'):
            for name, args in CALLS.items():
                with self.subTest(tool=name, value=value):
                    response = exchange(client, [request(name, {**args, 'authorized': value})])[0]
                    self.assertTrue(response['result']['isError'])
        client.call.assert_not_called()

    def test_explicit_values_reach_backend_without_auto_authorizing_false(self):
        client = object.__new__(mcp.Client)
        client.request = Mock(return_value={'status': 'queued'})
        for name, args in CALLS.items():
            for value in (True, False):
                with self.subTest(tool=name, value=value):
                    response = exchange(client, [request(name, {**args, 'authorized': value})])[0]
                    self.assertFalse(response['result']['isError'])
                    route, body = client.request.call_args.args
                    self.assertEqual(route, '/v1/native/jobs')
                    self.assertIs(body['authorized'], value)
                    self.assertNotIn('authorized', body.get('arguments', {}))
        # false passes a boolean schema but must be rejected by the macOS
        # backend; test_macos_godot covers this through actual loopback HTTP.

    def test_windows_requests_keep_native_network_and_script_permission_guards(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            project = root / 'project'
            (project / 'client/scenes').mkdir(parents=True)
            (project / 'client/scenes/main.tscn').write_text('[gd_scene format=3]\n')
            (project / 'test.gd').write_text('# synthetic; never executed\n')
            settings = {'id': 'fixture', 'root': str(project), 'enabled': True, 'allow_scripts': True}
            guard = Mock()
            control = NativeControl({'data_dir': str(root / 'bridge'), 'native': {'projects': [settings]}}, guard=guard)
            client = object.__new__(mcp.Client)
            client.request = lambda route, body: control.queue(body)
            with patch('native_control.threading.Thread') as worker:
                # A new explicit authorization field must not bypass Windows
                # network isolation, and must not be treated as a script arg.
                guard.require.side_effect = ValueError('Native network isolation is not ready.')
                for name in ('capture_scene', 'run_project_script'):
                    args = {**CALLS[name], 'authorized': True, 'request_id': uuid.uuid4().hex}
                    response = exchange(client, [request(name, args)])[0]
                    self.assertTrue(response['result']['isError'])
                    self.assertIn('network isolation', response['result']['content'][0]['text'])
                settings['allow_scripts'] = False
                response = exchange(client, [request('run_project_script', {**CALLS['run_project_script'], 'authorized': True})])[0]
                self.assertTrue(response['result']['isError'])
                self.assertIn('script access is disabled', response['result']['content'][0]['text'])
                self.assertEqual(control.jobs, {})
                worker.assert_not_called()
                settings['allow_scripts'] = True
                guard.require.side_effect = None
                guard.require.reset_mock()
                for name in ('capture_scene', 'run_project_script'):
                    response = exchange(client, [request(name, {**CALLS[name], 'authorized': True})])[0]
                    self.assertFalse(response['result']['isError'], response)
                self.assertEqual(len(control.jobs), 2)
                self.assertEqual(guard.require.call_count, 2)
                guard.require.assert_called_with('godot')
                self.assertEqual(worker.return_value.start.call_count, 2)
