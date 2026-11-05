"""Opt-in Blender sessions on macOS. Trusted scripts run as the login user, not in a sandbox."""
import copy
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import signal
import subprocess
import threading
import time
import uuid

from .macos import private_dir, write_private
from .macos_godot import (GodotControl, Unsupported, canonical, relative, open_inside,
                         executable as native_executable, child_env, JOB, TERMINAL, LIMIT, png)
from native_blender_validation import validate_blender, BLENDER_FIELDS
from native_blender_output import result as safe_result


def executable(value):
    return native_executable(value, engine='blender')


def discover(search_dirs=None, selected=None):
    """Discover existing choices without executing, installing or selecting them."""
    candidates = [Path(selected)] if selected else []
    for directory in search_dirs if search_dirs is not None else (Path('/Applications'), Path.home() / 'Applications'):
        candidates.extend(sorted(Path(directory).glob('*.app')))
    found = []
    for candidate in candidates:
        try:
            path = str(executable(candidate))
            if path not in found:
                found.append(path)
        except (ValueError, OSError):
            pass
    return found


def project_path(root, name):
    path = root / relative(name)
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.resolve().is_relative_to(root):
        raise ValueError('Project path escaped its approved root.')
    return path


def publish(root, name, data):
    """Exclusive create through pinned directories; reject swapped links and existing files."""
    parts = relative(name).split('/')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in [*root.parts[1:], *parts[:-1]]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        output = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        with os.fdopen(output, 'wb') as stream:
            stream.write(data)
    finally:
        os.close(fd)


def filtered(value):
    value = safe_result(value)
    # No absolute POSIX paths or credential-looking script output crosses JSON.
    if isinstance(value, dict):
        return {filtered(k): filtered(v) for k, v in value.items() if not re.search(r'token|secret|password|credential', k, re.I)}
    if isinstance(value, list):
        return [filtered(v) for v in value]
    if isinstance(value, str):
        value = re.sub(r'(?<!\w)/(?:[^\s\"\']+/)*[^\s\"\']*', '[local path]', value)
        return re.sub(r'(?i)(?:bearer\s+\S+|(?:token|password|secret|api_key)\s*[=:]\s*\S+)', '[redacted]', value)
    return value


class BlenderControl(GodotControl):
    engine = 'blender'

    def __init__(self, config, run=None, popen=None):
        super().__init__(config, run, popen)
        self.sessions = {}
        for job in self.jobs.values():
            if 'outcome unknown' in job.get('error', ''):
                job['outcome_unknown'] = True
                self.save(job)

    def probe(self):
        if platform.system() != 'Darwin' or platform.machine() != 'arm64':
            raise Unsupported('unsupported: Blender requires native macOS arm64.')
        if not self.settings.get('blender_executable'):
            raise Unsupported('unsupported: select an existing trusted Blender locally.')
        exe = executable(self.settings['blender_executable'])
        value = self.run([str(exe), '--version'], cwd=self.root, env=child_env(self.root), stdin=subprocess.DEVNULL,
                         capture_output=True, text=True, timeout=5, check=True, shell=False)
        if not re.search(r'^Blender (?:4\.[2-9]|4\.[1-9][0-9]+|5\.[0-9]+)\.[0-9]+[ \t]*$', value.stdout.splitlines()[0] if value.stdout else ''):
            raise Unsupported('unsupported: stable Blender 4.2+ within major 4 or 5 is required.')
        return exe

    def status(self, api_healthy=True):
        checks = dict(installed=False, path=False, permissions=False, version=False, architecture=False, bridge_api=api_healthy)
        state = 'unsupported'
        if self.settings.get('blender_executable'):
            try:
                checks['installed'] = Path(self.settings['blender_executable']).exists()
                executable(self.settings['blender_executable'])
                checks.update(path=True, permissions=True, architecture=True)
                self.probe()
                checks['version'] = True
                with self.lock:
                    running = any(s['process'].poll() is None for s in self.sessions.values())
                state = ('running' if running else 'available') if api_healthy else 'installed'
            except Unsupported:
                state = 'unsupported'
            except Exception:
                state = 'error'
        return {'engine': 'blender', 'state': state, 'checks': checks, 'sandbox': False, 'network_isolation': False,
                'script_authorization_required': True, 'requested_backend': self.settings.get('blender_device', 'metal'),
                'metal_status': 'checked_per_render'}

    def project(self, ident):
        p = next((p for p in self.settings.get('projects', []) if p['id'] == ident and p.get('enabled') is True), None)
        if not p:
            raise ValueError('Project is not enabled locally.')
        root = canonical(p['root'])
        info = root.stat()
        if not root.is_dir() or len(root.parts) < 3 or self.identities.get(ident) != (str(root), info.st_dev, info.st_ino):
            raise ValueError('Project identity changed; local review required.')
        return p, root

    def projects(self):
        return [{**p, 'project_types': ['blender']} for p in super().projects()]

    def validate(self, body, allow_existing_outputs=False):
        if not isinstance(body, dict) or set(body) - {'engine', 'operation', 'arguments', 'project_id', 'request_id', 'authorized'}:
            raise ValueError('Unexpected task fields.')
        if body.get('engine') != 'blender' or body.get('operation') not in BLENDER_FIELDS:
            raise Unsupported('unsupported: Blender operation is not exposed.')
        p, root = self.project(body.get('project_id'))
        args, op = body.get('arguments', {}), body['operation']
        if not isinstance(args, dict):
            raise ValueError('Arguments must be an object.')
        validate_blender(op, args, root, project_path)
        steps = args['steps'] if op == 'batch' else [{'operation': op, 'arguments': args}]
        destinations = set()
        for step in steps:
            operation, values = step['operation'], step.get('arguments', {})
            if operation in ('execute_code', 'run_script') and (p.get('allow_scripts') is not True or body.get('authorized') is not True):
                raise ValueError('Python requires local allow_scripts and explicit authorized=true; no sandbox.')
            if operation == 'render' and values.get('engine', 'cycles') != 'cycles':
                raise Unsupported('unsupported: macOS exposes Cycles rendering only.')
            if operation in ('open', 'import_asset', 'run_script'):
                allowed = {'.blend'} if operation == 'open' else {'.py'} if operation == 'run_script' else {'.glb', '.obj', '.vrm'}
                path = project_path(root, values['path'])
                if path.suffix.lower() not in allowed:
                    raise Unsupported('unsupported: asset format is not exposed on macOS.')
                with open_inside(root, values['path']):
                    pass
            outputs = list(values.get('output_paths', []))
            if operation in ('save', 'export_asset'):
                if values.get('replace'):
                    raise Unsupported('unsupported: macOS never replaces existing project files.')
                outputs.append(values['path'])
                if Path(values['path']).suffix.lower() not in ({'.blend'} if operation == 'save' else {'.glb', '.obj', '.vrm'}):
                    raise Unsupported('unsupported: export format.')
            for name in outputs:
                file = project_path(root, name)
                if name in destinations or (file.exists() and not allow_existing_outputs) or not file.parent.is_dir():
                    raise ValueError('Output must be new, distinct, and have an existing parent directory.')
                destinations.add(name)
        count = 0
        for folder, dirs, files in os.walk(root, followlinks=False):
            for name in dirs + files:
                count += 1
                if count > 100000 or (Path(folder) / name).is_symlink():
                    raise ValueError('Project contains links or exceeds scan limit.')
        return root

    def queue(self, body):
        body = copy.deepcopy(body)
        with self.lock:
            if self.closed:
                raise ValueError('Blender service stopped.')
            ident = body.get('request_id') or uuid.uuid4().hex
            if not isinstance(ident, str) or not JOB.fullmatch(ident):
                raise ValueError('Invalid request ID.')
            fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
            if ident in self.jobs:
                if self.jobs[ident]['fingerprint'] != fingerprint:
                    raise ValueError('Request ID conflict.')
                self.validate(body, allow_existing_outputs=True)
                return self.job_status(ident)
            self.validate(body)
            if any(j['project_id'] == body['project_id'] and j['status'] not in TERMINAL for j in self.jobs.values()):
                raise ValueError('Project already has an active Blender task.')
            job = dict(id=ident, engine='blender', project_id=body['project_id'], operation=body['operation'],
                       status='queued', created_at=time.time(), artifacts=[], fingerprint=fingerprint, logs=['Blender task queued.'])
            self.jobs[ident] = job
            self.save(job)
            worker = threading.Thread(target=self.execute, args=(job, body), daemon=True)
            self.threads.append(worker)
            worker.start()
            return self.job_status(ident)

    def session(self, job, root):
        ident = job['project_id']
        current = self.sessions.get(ident)
        if current and current['process'].poll() is None:
            return current
        exe = self.probe()
        folder = private_dir(self.root / job['id'] / 'session')
        spool, outputs = private_dir(folder / 'spool'), private_dir(folder / 'outputs')
        settings = dict(project=str(root), spool=str(spool), outputs=str(outputs), macos=True,
                        device=self.settings.get('blender_device', 'metal'))
        write_private(folder / 'settings.json', json.dumps(settings).encode())
        env = child_env(folder)
        env.update(BLENDER_USER_CONFIG=str(private_dir(folder / 'config')), BLENDER_USER_SCRIPTS=str(private_dir(folder / 'scripts')),
                   BLENDER_USER_EXTENSIONS=str(private_dir(folder / 'extensions')))
        command = [str(exe), '--factory-startup', '--disable-autoexec', '--python-exit-code', '1', '--python',
                   str(Path(__file__).resolve().parents[1] / 'native_blender.py'), '--', str(folder / 'settings.json')]
        proc = self.popen(command, cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, shell=False, start_new_session=True)
        current = dict(process=proc, spool=spool, outputs=outputs)
        self.sessions[ident] = current
        return current

    def execute(self, job, body):
        try:
            root = self.validate(body)
            with self.lock:
                if job['status'] == 'cancelled' or self.closed:
                    return
                session = self.session(job, root)
                proc, spool = session['process'], session['spool']
                self.active[job['id']] = proc
                job.update(status='running', pid=proc.pid, started_at=time.time(),
                           project_identity=list(self.identities[job['project_id']])[1:])
                job['logs'].append('Owned Blender session active; raw application output suppressed.')
                self.save(job)
                request = dict(command=body['operation'], arguments=body.get('arguments', {}))
                temp = spool / (job['id'] + '.tmp')
                write_private(temp, json.dumps(request).encode())
                os.rename(temp, spool / (job['id'] + '.request.json'))
            deadline = time.monotonic() + body.get('arguments', {}).get('timeout_seconds', 300)
            while True:
                with self.lock:
                    if job['status'] == 'cancelled':
                        return
                    response = spool / (job['id'] + '.response.json')
                    if response.exists():
                        with open_inside(spool, response.name) as stream:
                            value = json.load(stream)
                        break
                    progress = spool / (job['id'] + '.progress.json')
                    if progress.exists():
                        with open_inside(spool, progress.name) as stream:
                            job['progress'] = filtered(json.load(stream))
                    if proc.poll() is not None:
                        raise ValueError('Blender process exited.')
                    if time.monotonic() >= deadline:
                        job['timed_out'] = True
                        raise TimeoutError()
                time.sleep(.05)
            with self.lock:
                if job['status'] == 'cancelled':
                    return
                result = value.get('result', {})
                job['result'] = filtered(result)
                if not value.get('ok'):
                    job['error_code'] = value.get('code', 'operation_failed')
                    raise ValueError('Blender operation failed.')
                names = list(result.get('artifacts', []))
                if result.get('artifact'):
                    names.append(result['artifact'])
                exports = result.get('_exports', [])
                for index, name in enumerate(dict.fromkeys(names)):
                    with open_inside(session['outputs'], name) as stream:
                        data = stream.read(LIMIT + 1)
                    if Path(name).suffix == '.png':
                        png(data)
                    filename = 'artifact-' + str(index) + Path(name).suffix
                    write_private(self.root / job['id'] / filename, data)
                    job['artifacts'].append(dict(index=index, filename=filename, sha256=hashlib.sha256(data).hexdigest(), bytes=len(data)))
                    for export in exports:
                        if export['artifact'] == name:
                            publish(root, export['path'], data)
                job['result'].pop('_exports', None)
                job['status'] = 'failed' if result.get('executed') is False or result.get('partial_success') else 'succeeded'
                job['logs'].append('Blender task completed; artifacts hashed and registered.')
                self.active.pop(job['id'], None)
        except Exception:
            with self.lock:
                self.terminate(job['id'])
                if job['status'] != 'cancelled':
                    job.update(status='failed', error='Blender task failed or timed out; check trusted inputs, device and declared outputs.')
                    job['logs'].append('Blender failure; private diagnostics suppressed.')
        finally:
            with self.lock:
                self.save(job)

    def job_status(self, ident):
        with self.lock:
            value = super().job_status(ident)
            value['logs'] = list(self.jobs[ident]['logs'][-20:])
            return value

    def open_artifact(self, ident, index):
        import io
        with self.lock:
            job = self.job_status(ident)
            if job['status'] not in ('succeeded', 'failed') or type(index) is not int or not 0 <= index < len(job['artifacts']):
                raise ValueError('Artifact unavailable.')
            item = job['artifacts'][index]
            with open_inside(canonical(self.root / ident), item['filename']) as stream:
                data = stream.read(LIMIT + 1)
            if len(data) != item['bytes'] or hashlib.sha256(data).hexdigest() != item['sha256']:
                raise ValueError('Artifact changed after registration.')
            return io.BytesIO(data)

    def blender_tasks(self, project_id=None):
        with self.lock:
            return {'jobs': [self.job_status(j['id']) for j in sorted(self.jobs.values(), key=lambda j: j['created_at'], reverse=True)[:100]
                             if project_id is None or j['project_id'] == project_id]}

    def close(self):
        try:
            super().close()
        finally:
            with self.lock:
                for session in self.sessions.values():
                    proc = session['process']
                    if proc.poll() is None:
                        try:
                            os.killpg(proc.pid, signal.SIGTERM)
                            try:
                                proc.wait(timeout=3)
                            except subprocess.TimeoutExpired:
                                os.killpg(proc.pid, signal.SIGKILL)
                                proc.wait(timeout=3)
                        except ProcessLookupError:
                            pass
