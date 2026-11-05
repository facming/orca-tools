"""Allowlisted macOS native HTTP routes; Windows routing is unchanged."""
import re
from .macos_godot import Unsupported


def handle_native(handler, bridge, route, security=None):
    native = bridge.native
    try:
        if native is None:
            raise Unsupported('unsupported: configure Godot locally first.')
        method = handler.command
        if method == 'GET' and route == '/v1/native/status':
            return handler.send_json(200, native.status())
        if method == 'GET' and route == '/v1/native/projects':
            return handler.send_json(200, {'projects': native.projects()})
        if method == 'POST' and route == '/v1/native/jobs':
            return handler.send_json(201, native.queue(handler.body()))
        if method == 'POST' and route == '/v1/native/godot-stop':
            value = handler.body()
            if set(value) != {'project_id'}:
                raise ValueError('Invalid stop fields.')
            return handler.send_json(200, native.stop(value['project_id']))
        match = re.fullmatch(r'/v1/native/jobs/([a-f0-9]{32})(/(cancel|logs))?', route)
        if match:
            ident, action = match[1], match[3]
            if method == 'GET' and action in (None, 'logs'):
                return handler.send_json(200, native.logs(ident) if action else native.job_status(ident))
            if method == 'POST' and action == 'cancel':
                if handler.body():
                    raise ValueError('Cancel takes an empty body.')
                return handler.send_json(200, native.cancel(ident))
        match = re.fullmatch(r'/v1/native/artifacts/([a-f0-9]{32})/(\d{1,4})', route)
        if method == 'GET' and match:
            with native.open_artifact(match[1], int(match[2])) as stream:
                data = stream.read()
            if len(data) > bridge.config.get('max_artifact_bytes', 32 * 1024 * 1024):
                raise ValueError('Artifact exceeds transfer limit.')
            if security and security.revoked.is_set():
                handler.close_connection = True
                return
            handler.reply_started = True
            handler.send_response(200)
            handler.send_header('Content-Type', 'application/octet-stream')
            handler.send_header('Content-Length', str(len(data)))
            handler.send_header('Cache-Control', 'no-store')
            handler.send_header('X-Content-Type-Options', 'nosniff')
            handler.end_headers()
            for offset in range(0, len(data), 1024 * 1024):
                if security and security.revoked.is_set():
                    handler.close_connection = True
                    return
                handler.wfile.write(data[offset:offset + 1024 * 1024])
            return
        raise Unsupported('unsupported: this native operation is not exposed on macOS.')
    except Unsupported as error:
        return handler.send_json(404, {'error': str(error), 'code': 'unsupported'})
    except Exception:
        if handler.reply_started:
            handler.close_connection = True
            return
        return handler.send_json(400, {'error': 'Godot request rejected. Check local approval, paths and task arguments.'})
