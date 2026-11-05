"""macOS service/CLI for trusted managed ComfyUI and opt-in Godot."""
import sys
from pathlib import Path
# LaunchAgent uses -I: add only this installed, owner-private application directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import argparse
import copy
import hmac
import http.server
import json
import logging
import logging.handlers
import os
import secrets
import signal
import socket
import threading
from bridge_platform.macos import MacOSPlatform, ComfyProcess, environment, private_path, read_token
from bridge import Bridge, Server, make_handler, acquire_instance_lock


def load_config(file, adapter):
    private_path(adapter.root, True)
    config = json.loads(private_path(file).read_text())
    token = read_token(adapter.root)
    if config.get('platform') != 'darwin' or config.get('data_dir') != str(adapter.root):
        raise ValueError('Invalid macOS bridge configuration.')
    if config.get('tripo'):
        raise ValueError('Tripo is unsupported on macOS.')
    from bridge_platform.macos_godot import validate_settings
    validate_settings(config.get('native') or {})
    if config.get('comfy_url') != 'http://127.0.0.1:8188' or config.get('port') != 18772 or config.get('admin_port') != 18773:
        raise ValueError('Unexpected macOS service endpoints.')
    config['token'] = token
    return config


class RuntimeSecurity:
    """A network/configuration failure is latched until a fresh service start."""
    def __init__(self, config, adapter):
        self.config = copy.deepcopy(config)
        self.adapter = adapter
        self.revoked = threading.Event()

    def check(self):
        if self.revoked.is_set():
            return None
        try:
            fresh = load_config(self.adapter.root / 'config.json', self.adapter)
            # Token rotation is live; every other setting requires a restart.
            if {k: v for k, v in fresh.items() if k != 'token'} != {k: v for k, v in self.config.items() if k != 'token'}:
                raise ValueError()
            status = self.adapter.validate_network(fresh)
            if not self.revoked.is_set():
                return fresh, status
        except Exception:
            self.revoked.set()
        return None

    def authorize(self, authorization, peer):
        # Reject unknown peers/bad credentials before spawning status commands.
        try:
            if self.revoked.is_set() or peer not in self.config['allowed_peers']:
                return False
            if not hmac.compare_digest(authorization.encode(), ('Bearer ' + read_token(self.adapter.root)).encode()):
                return False
        except Exception:
            self.revoked.set()
            return False
        checked = self.check()
        if not checked:
            return False
        config, status = checked
        try:
            # Rotation may have happened while the system probes were running.
            token_ok = hmac.compare_digest(authorization.encode(), ('Bearer ' + read_token(self.adapter.root)).encode())
        except Exception:
            self.revoked.set()
            return False
        peer_ok = peer in config['allowed_peers'] and any(
            p.get('ID') == config['peer_ids'][peer] and p.get('Online') is True and peer in p.get('TailscaleIPs', [])
            for p in status.get('Peer', {}).values())
        return token_ok and peer_ok and not self.revoked.is_set()


class ClosingServer(Server):
    """Close accepted keep-alive/stream sockets as well as the listening port."""
    def __init__(self, *args, **kwargs):
        self.connections = set()
        self.connections_lock = threading.Lock()
        super().__init__(*args, **kwargs)

    def get_request(self):
        request, address = super().get_request()
        with self.connections_lock:
            self.connections.add(request)
        return request, address

    def shutdown_request(self, request):
        with self.connections_lock:
            self.connections.discard(request)
        super().shutdown_request(request)

    def server_close(self):
        super().server_close()
        with self.connections_lock:
            for connection in self.connections:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                connection.close()
            self.connections.clear()


def health(bridge, process):
    checks = {'installed': False, 'launch_agent': False, 'tailscale': False, 'token': False, 'peers': False, 'comfyui': False}
    try:
        fresh = load_config(bridge.platform.root / 'config.json', bridge.platform)
        # Runtime config changes require an explicit service restart.
        if {k: v for k, v in fresh.items() if k != 'token'} != {k: v for k, v in bridge.config.items() if k != 'token'}:
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
    result = {'platform': 'darwin', 'capability': 'comfyui', 'ready': all(checks.values()), 'checks': checks, **process.backend}
    api_healthy = all(v for k, v in checks.items() if k != 'comfyui')
    result['godot'] = bridge.native.status(api_healthy) if bridge.native else {'state': 'unsupported', 'checks': {}}
    result['blender'] = {'state': 'unsupported', 'checks': {}}
    for engine in result['godot'].pop('engines', []):
        result[engine['engine']] = engine
    result['comfyui'] = {'state': 'running' if result['ready'] else 'installed' if checks['installed'] else 'error', 'checks': checks}
    return result


def start_admin(bridge, process, port=18773, security=None):
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
            if security and security.revoked.is_set():
                return self.reply(403, {'error': 'Access denied.'})
            expected = '127.0.0.1:' + str(self.server.server_port)
            if self.headers.get('Host') != expected or self.headers.get('Origin') not in (None, 'http://' + expected):
                return self.reply(403, {'error': 'Access denied.'})
            if self.command == 'GET' and self.path == '/':
                html = '''<!doctype html><meta charset="utf-8"><title>GPU Workflows</title>
<h1>ComfyUI Bridge</h1><p>ComfyUI, Blender and explicitly configured Godot are managed independently. Godot project execution requires user authorization; it is not sandboxed. Custom ComfyUI nodes and API nodes are disabled.</p>
<button id="refresh">Refresh</button><button id="start">Start ComfyUI</button><button id="stop">Stop managed ComfyUI</button><pre id="state"></pre>
<script nonce="''' + session + '''">const token="''' + session + '''";
async function show(action){const r=await fetch('/api/'+action,{method:['start','stop'].includes(action)?'POST':'GET',headers:{'X-Admin-Token':token,'Content-Type':'application/json'},body:['start','stop'].includes(action)?'{}':undefined});document.getElementById('state').textContent=JSON.stringify(await r.json(),null,2);}
for(const action of ['start','stop'])document.getElementById(action).onclick=()=>show(action);document.getElementById('refresh').onclick=()=>show('health');show('health');</script>'''
                html = html.replace('const token="' + session + '"', "const token='" + session + "'")
                return self.reply(200, html, True)
            try:
                bearer = hmac.compare_digest(self.headers.get('Authorization', '').encode(), ('Bearer ' + read_token(bridge.platform.root)).encode())
            except Exception:
                bearer = False
            local = hmac.compare_digest(self.headers.get('X-Admin-Token', '').encode(), session.encode())
            if not (bearer or local):
                return self.reply(403, {'error': 'Access denied.'})
            if security and not security.check():
                return self.reply(403, {'error': 'Access denied.'})
            try:
                if not local and not hmac.compare_digest(self.headers.get('Authorization', '').encode(), ('Bearer ' + read_token(bridge.platform.root)).encode()):
                    return self.reply(403, {'error': 'Access denied.'})
                if self.command == 'GET' and self.path == '/api/health':
                    return self.reply(200, health(bridge, process))
                if self.command == 'GET' and self.path == '/api/workflows':
                    return self.reply(200, {'workflows': bridge.catalog()})
                if self.command == 'POST' and self.path in ('/api/start', '/api/stop'):
                    if self.headers.get('Origin') != 'http://' + expected or self.headers.get('Transfer-Encoding') or self.headers.get('Content-Length') != '2' or self.rfile.read(2) != b'{}':
                        return self.reply(403, {'error': 'Access denied.'})
                    if security and not security.check():
                        return self.reply(403, {'error': 'Access denied.'})
                    # A slow body/network probe may overlap token rotation.
                    # A valid local session remains independent of Bearer auth.
                    if not local and not hmac.compare_digest(self.headers.get('Authorization', '').encode(), ('Bearer ' + read_token(bridge.platform.root)).encode()):
                        return self.reply(403, {'error': 'Access denied.'})
                    result = process.start() if self.path == '/api/start' else process.stop()
                    logging.info('Managed ComfyUI lifecycle operation completed')
                    return self.reply(200, result)
                return self.reply(404, {'error': 'Unsupported management route.'})
            except Exception:
                logging.error('Managed ComfyUI operation failed; check environment, port and dependencies')
                return self.reply(400, {'error': 'ComfyUI operation failed. Check environment, port and dependencies.'})
        do_GET = do_POST = handle_request
    server = ClosingServer(('127.0.0.1', port), Handler)
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


def serve(config, adapter, interval=1):
    security = RuntimeSecurity(config, adapter)
    if not security.check():
        raise ValueError('Bridge startup security checks failed.')
    process = ComfyProcess(config, adapter)
    process.on_security_failure = security.revoked.set
    from bridge import ComfyAdapter
    bridge = Bridge(config, ManagedAdapter(process, ComfyAdapter(config['comfy_url'])), adapter)
    server = admin = monitor = None
    done = threading.Event()
    try:
        server = ClosingServer((config['listen'], config['port']), make_handler(bridge, security))
        # Recheck after bind, before accepting anything. Never retry another IP.
        if not security.check():
            raise ValueError('Bridge startup security checks failed.')
        admin = start_admin(bridge, process, security=security)
        def watch():
            while not done.wait(interval):
                try:
                    valid = security.check() is not None
                    if process.process and process.process.poll() is None:
                        valid = process.verify_listener(required=False) and valid
                except Exception:
                    valid = False
                if not valid:
                    security.revoked.set()
                    logging.warning('Bridge stopped after a runtime security check failed')
                    server.shutdown()
                    return
        monitor = threading.Thread(target=watch, daemon=True, name='bridge-security')
        monitor.start()
        # ComfyUI still requires an explicit local Start action.
        server.serve_forever(poll_interval=.1)
    finally:
        security.revoked.set()
        done.set()
        try:
            if server:
                server.server_close()
        finally:
            try:
                if admin:
                    try:
                        admin.shutdown()
                    finally:
                        admin.server_close()
            finally:
                try:
                    if monitor:
                        monitor.join(timeout=10)
                finally:
                    try:
                        if bridge.native:
                            bridge.native.close()
                    finally:
                        process.stop(final=True)



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
        instance = acquire_instance_lock(adapter.root)
        private_path(adapter.logs, True)
        handler = logging.handlers.RotatingFileHandler(adapter.logs / 'bridge.log', maxBytes=1024*1024, backupCount=2)
        # Never persist exception details, user inputs or third-party output on Mac.
        handler.addFilter(FixedDiagnostics())
        logging.basicConfig(level=logging.INFO, handlers=[handler])
        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
        try:
            serve(config, adapter)
        finally:
            instance.close()
    except KeyboardInterrupt:
        pass
    except Exception:
        print('macOS Bridge could not start. Check private configuration, LaunchAgent and Tailscale status.', file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
