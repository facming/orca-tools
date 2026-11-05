"""Two real loopback HTTP servers, synthetic credentials, no production services."""
import importlib.util
import json
import pathlib
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

spec = importlib.util.spec_from_file_location('redirect_client', pathlib.Path(__file__).parent / 'plugin/client.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class RedirectTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.location = ''
        self.code = 302
        self.token = 'synthetic-' + 'x' * 48
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length', 0)))
                self.do_GET()

            def do_GET(self):
                owner.calls.append((self.server.server_port, self.path, self.command, self.headers.get('Authorization')))
                if self.path in ('/redirect', '/hop'):
                    self.send_response(owner.code)
                    self.send_header('Location', owner.location if self.path == '/redirect' else owner.b_url + '/ok')
                    body = json.dumps({'error': dict(self.headers)}).encode()
                elif self.path == '/error':
                    self.send_response(500, 'synthetic-header-secret')
                    body = json.dumps({'error': dict(self.headers), 'Cookie': 'synthetic-header-secret'}).encode()
                else:
                    self.send_response(200)
                    body = b'{"ok":true}'
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.servers = [ThreadingHTTPServer(('127.0.0.1', 0), Handler) for _ in range(2)]
        for server in self.servers:
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
        self.a_url, self.b_url = ['http://127.0.0.1:%s' % s.server_port for s in self.servers]
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        config = pathlib.Path(self.folder.name) / 'client.json'
        config.write_text(json.dumps({'url': self.a_url, 'token': self.token}))
        self.client = module.Client(config)

    def test_legacy_default_opener_reproduces_bearer_leak(self):
        self.location = self.b_url + '/ok'
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(urllib.request.Request(self.a_url + '/redirect', headers={'Authorization': 'Bearer ' + self.token})) as response:
            self.assertEqual(response.status, 200)
        self.assertEqual(self.calls[-1][0], self.servers[1].server_port)
        self.assertEqual(self.calls[-1][3], 'Bearer ' + self.token)

    def test_all_redirects_fail_closed_get_post_stream_multihop(self):
        for code in (301, 302, 303, 307, 308):
            for location in (self.b_url + '/ok', self.b_url.replace('127.0.0.1', 'localhost') + '/ok',
                             self.a_url + '/ok', self.a_url + '/hop', '/ok',
                             'https://127.0.0.1:1/ok', 'file:///unread', 'ftp://127.0.0.1/ok'):
                for body in (None, {'operation': 'synthetic'}):
                    for stream in (False, True):
                        with self.subTest(code=code, location=location, post=body is not None, stream=stream):
                            self.code, self.location = code, location
                            self.calls.clear()
                            with self.assertRaises(ValueError) as error:
                                self.client.request('/redirect', body, stream=stream)
                            self.assertNotIn(self.token, str(error.exception))
                            self.assertEqual(len(self.calls), 1)
                            self.assertEqual(self.calls[0][0], self.servers[0].server_port)

    def test_normal_auth_and_errors_and_artifact_entrypoints(self):
        for body in (None, {'x': 1}):
            self.assertEqual(self.client.request('/ok', body), {'ok': True})
            with self.client.request('/ok', body, stream=True) as response:
                self.assertEqual(response.read(), b'{"ok":true}')
        self.assertTrue(all(c[3] == 'Bearer ' + self.token for c in self.calls))
        for stream in (False, True):
            with self.assertRaises(ValueError) as error:
                self.client.request('/error', stream=stream)
            for secret in (self.token, 'Authorization', 'synthetic-header-secret'):
                self.assertNotIn(secret, str(error.exception))
        # All artifact paths use this same request/opener, never urlopen.
        source = pathlib.Path(module.__file__).read_text()
        self.assertEqual(source.count('self.opener.open('), 1)
        self.assertNotIn('urlopen(', source)


if __name__ == '__main__':
    unittest.main()
