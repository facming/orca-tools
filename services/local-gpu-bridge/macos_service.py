"""macOS service/CLI, restricted to a trusted, managed ComfyUI installation."""
import sys
from pathlib import Path
# LaunchAgent uses -I: add only this installed, owner-private application directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import argparse
import hmac
import http.server
import json
import logging
import logging.handlers
import os
import secrets
import signal
import threading
from bridge_platform.macos import MacOSPlatform, ComfyProcess, environment, private_path
from bridge import Bridge, Server, make_handler, acquire_instance_lock


def load_config(file, adapter):
    private_path(adapter.root, True)
    config = json.loads(private_path(file).read_text())
    token = private_path(adapter.root / 'token').read_text().strip()
    if len(token) != 64 or any(c not in '0123456789abcdef' for c in token):
        raise ValueError('Invalid bridge token file.')
    if config.get('platform') != 'darwin' or config.get('data_dir') != str(adapter.root):
        raise ValueError('Invalid macOS bridge configuration.')
    if config.get('native') or config.get('tripo'):
        raise ValueError('Native engines are unsupported on macOS.')
    if config.get('comfy_url') != 'http://127.0.0.1:8188' or config.get('port') != 18772 or config.get('admin_port') != 18773:
        raise ValueError('Unexpected macOS service endpoints.')
    config['token'] = token
    return config


def health(bridge, process):
    checks = {'installed': False, 'launch_agent': False, 'tailscale': False, 'token': False, 'peers': False, 'comfyui': False}
    try:
        fresh = load_config(bridge.platform.root / 'config.json', bridge.platform)
        # Runtime config changes require an explicit service restart.
        if fresh != bridge.config:
            raise ValueError()
        private_path(bridge.platform.root / 'launcher.json')
        checks['installed'] = True
        checks['token'] = True
        checks['launch_agent'] = bridge.platform.launchctl('status')
        bridge.platform.validate_network(fresh)
        checks['tailscale'] = checks['peers'] = True
        checks['comfyui'] = process.healthy_owned() and bridge.adapters['comfyui.workflow'].status()['online'] is True
    except Exception:
        pass
    return {'platform': 'darwin', 'capability': 'comfyui', 'ready': all(checks.values()), 'checks': checks, **process.backend}


def start_admin(bridge, process, port=18773):
    session = secrets.token_hex(32)
    class Handler(http.server.BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(5)
        def log_message(self, *args):
            pass
        def reply(self, code, value, html=False):
            data = value.encode() if html else json.dumps(value).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'text/html; charset=utf-8' if html else 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'nonce-" + session + "'; connect-src 'self'; frame-ancestors 'none'")
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(data)
        def handle_request(self):
            expected = '127.0.0.1:' + str(self.server.server_port)
            if self.headers.get('Host') != expected or self.headers.get('Origin') not in (None, 'http://' + expected):
                return self.reply(403, {'error': 'Access denied.'})
            if self.command == 'GET' and self.path == '/':
                html = '''<!doctype html><meta charset="utf-8"><title>GPU Workflows</title>
<h1>ComfyUI Bridge</h1><p>macOS supports managed ComfyUI only. Custom nodes and API nodes are disabled.</p>
<button id="refresh">Refresh</button><button id="start">Start ComfyUI</button><button id="stop">Stop managed ComfyUI</button><pre id="state"></pre>
<script nonce="''' + session + '''">const token="''' + session + '''";
async function show(action){const r=await fetch('/api/'+action,{method:['start','stop'].includes(action)?'POST':'GET',headers:{'X-Admin-Token':token,'Content-Type':'application/json'},body:['start','stop'].includes(action)?'{}':undefined});document.getElementById('state').textContent=JSON.stringify(await r.json(),null,2);}
for(const action of ['start','stop'])document.getElementById(action).onclick=()=>show(action);document.getElementById('refresh').onclick=()=>show('health');show('health');</script>'''
                html = html.replace('const token="' + session + '"', "const token='" + session + "'")
                return self.reply(200, html, True)
            bearer = hmac.compare_digest(self.headers.get('Authorization', '').encode(), ('Bearer ' + bridge.config['token']).encode())
            local = hmac.compare_digest(self.headers.get('X-Admin-Token', '').encode(), session.encode())
            if not (bearer or local):
                return self.reply(403, {'error': 'Access denied.'})
            try:
                if self.command == 'GET' and self.path == '/api/health':
                    return self.reply(200, health(bridge, process))
                if self.command == 'GET' and self.path == '/api/workflows':
                    return self.reply(200, {'workflows': bridge.catalog()})
                if self.command == 'POST' and self.path in ('/api/start', '/api/stop'):
                    if self.headers.get('Origin') != 'http://' + expected or self.headers.get('Transfer-Encoding') or self.headers.get('Content-Length') != '2' or self.rfile.read(2) != b'{}':
                        return self.reply(403, {'error': 'Access denied.'})
                    result = process.start() if self.path == '/api/start' else process.stop()
                    logging.info('Managed ComfyUI lifecycle operation completed')
                    return self.reply(200, result)
                return self.reply(404, {'error': 'Unsupported management route.'})
            except Exception:
                logging.error('Managed ComfyUI operation failed; check environment, port and dependencies')
                return self.reply(400, {'error': 'ComfyUI operation failed. Check environment, port and dependencies.'})
        do_GET = do_POST = handle_request
    server = Server(('127.0.0.1', port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class ManagedAdapter:
    """Do not use an unrelated ComfyUI instance listening at the selected port."""
    def __init__(self, process, delegate):
        self.process, self.delegate = process, delegate
    def __getattr__(self, name):
        function = getattr(self.delegate, name)
        def call(*args, **kwargs):
            if not self.process.healthy_owned():
                raise ValueError('Managed ComfyUI is offline. Start it through the local manager.')
            return function(*args, **kwargs)
        return call


class FixedDiagnostics(logging.Filter):
    def filter(self, record):
        if record.exc_info:
            record.msg, record.args, record.exc_info, record.exc_text = 'Local operation failed; inspect service configuration and dependencies.', (), None, None
        return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    try:
        environment()
        os.umask(0o077)
        adapter = MacOSPlatform()
        config = load_config(args.config, adapter)
        adapter.validate_network(config)
        acquire_instance_lock(adapter.root)
        private_path(adapter.logs, True)
        handler = logging.handlers.RotatingFileHandler(adapter.logs / 'bridge.log', maxBytes=1024*1024, backupCount=2)
        # Never persist exception details, user inputs or third-party output on Mac.
        handler.addFilter(FixedDiagnostics())
        logging.basicConfig(level=logging.INFO, handlers=[handler])
        process = ComfyProcess(config, adapter)
        from bridge import ComfyAdapter
        bridge = Bridge(config, ManagedAdapter(process, ComfyAdapter(config['comfy_url'])), adapter)
        server = Server((config['listen'], config['port']), make_handler(bridge))
        admin = start_admin(bridge, process)
        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
        try:
            # Startup never starts ComfyUI without a local explicit Start action.
            server.serve_forever()
        finally:
            server.server_close()
            admin.shutdown()
            admin.server_close()
            process.stop()
    except KeyboardInterrupt:
        pass
    except Exception:
        print('macOS Bridge could not start. Check private configuration, LaunchAgent and Tailscale status.', file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
