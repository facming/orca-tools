"""Opt-in Unity Editor API transport. Trusted project code is NOT sandboxed."""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import plistlib
import re
import secrets
import subprocess
import threading
import time
import uuid

from .macos import private_dir, write_private
from .macos_godot import (GodotControl, Unsupported, canonical, inside, open_inside,
                         executable, child_env, JOB, TERMINAL, LIMIT, png, relative)
from .macos_blender import filtered, publish

EXTENSION = Path(__file__).resolve().parents[1] / 'OrcaUnityBridge.cs'
VERSION = re.compile(r'6000\.0\.\d+f\d+')
# Exact patch matching prevents an implicit project upgrade. Other series need review.
FIELDS = {
    'editor.status': {}, 'scene.get': {}, 'hierarchy.get': {},
    'gameobject.get': {'id': 'integer'}, 'gameobject.create': {'name': 'string'},
    'gameobject.delete': {'id': 'integer'},
    'transform.set': {'id': 'integer', 'position': 'array', 'rotation': 'array', 'scale': 'array'},
    'component.add': {'id': 'integer', 'component': 'string'},
    'component.set': {'id': 'integer', 'component': 'string', 'property': 'string', 'value': 'number'},
    'inspector.get': {'id': 'integer'}, 'scene.save': {'path': 'string'},
    'play.start': {}, 'play.stop': {}, 'gameview.capture': {}, 'console.get': {},
    'assets.list': {}, 'asset.import': {'path': 'string'},
}
REQUIRED = {name: list(fields) for name, fields in FIELDS.items()}
REQUIRED['transform.set'] = ['id']
COMPONENTS = {'Camera': {'field of view': (1, 179), 'orthographic size': (.01, 10000)},
              'Light': {'intensity': (0, 10000), 'range': (.01, 10000)},
              'BoxCollider': {'is trigger': (0, 1)}}


def catalog():
    return [{'name': n, 'description': 'Managed Unity Editor API: ' + n,
             'inputSchema': {'type': 'object', 'properties': {
                 k: dict(type=t, **({'items': {'type': 'number'}, 'minItems': 3, 'maxItems': 3} if t == 'array' else {}))
                 for k, t in fields.items()}, 'required': REQUIRED[n], 'additionalProperties': False}}
            for n, fields in FIELDS.items()]


def editor(value):
    exe = executable(value, 'unity')
    app = exe.parent.parent.parent
    if app.suffix != '.app' or exe.name != 'Unity':
        raise ValueError('Select Unity.app or its Contents/MacOS/Unity executable.')
    with open_inside(app, 'Contents/Info.plist') as stream:
        meta = plistlib.load(stream)
    if 'unity' not in meta.get('CFBundleIdentifier', '').lower() or meta.get('CFBundleExecutable') != 'Unity':
        raise ValueError('Select a Unity Editor bundle.')
    versions = {v for v in (meta.get('CFBundleShortVersionString'), meta.get('CFBundleVersion')) if isinstance(v, str) and VERSION.fullmatch(v)}
    if len(versions) != 1:
        raise Unsupported('unsupported: unambiguous stable Unity 6000.0 Editor metadata is required.')
    version = versions.pop()
    minimum = meta.get('LSMinimumSystemVersion', '11.0')
    if not re.fullmatch(r'\d+(?:\.\d+){0,2}', minimum):
        raise Unsupported('unsupported: unknown minimum macOS version.')
    return exe, version, minimum


def discover(search_dirs=None, selected=None):
    """Metadata only; Hub is never launched and choices are never auto-selected."""
    roots = [Path(p) for p in search_dirs] if search_dirs is not None else [Path('/Applications'), Path.home() / 'Applications']
    candidates = [Path(selected)] if selected else []
    hubs = []
    for root in roots:
        hub = root / 'Unity Hub.app'
        try:
            with open_inside(canonical(hub), 'Contents/Info.plist') as stream:
                if 'unity' in plistlib.load(stream).get('CFBundleIdentifier', '').lower():
                    hubs.append(str(hub))
        except (ValueError, OSError):
            pass
        candidates += list(root.glob('Unity/Hub/Editor/*/Unity.app')) + list(root.glob('Unity.app'))
    editors = []
    for item in candidates:
        try:
            exe, version, _ = editor(item)
            row = {'path': str(exe), 'version': version}
            if row not in editors:
                editors.append(row)
        except (ValueError, OSError):
            pass
    return {'hubs': hubs, 'editors': editors}


def asset_path(root, value, suffixes=None, new=False):
    value = relative(value)
    parts = value.split('/')
    if len(parts) < 2 or parts[0] != 'Assets' or any(p.startswith(('.', 'OrcaBridge-')) or p in ('Library', 'Temp', 'Obj', 'Editor') for p in parts):
        raise ValueError('Only non-code Assets paths are exposed.')
    path = root / value
    canonical(path.parent)
    if not path.parent.resolve().is_relative_to(root) or path.is_symlink():
        raise ValueError('Asset path escaped project.')
    if suffixes and path.suffix.lower() not in suffixes:
        raise Unsupported('unsupported: asset type.')
    if new:
        if path.exists() or path.with_name(path.name + '.meta').exists():
            raise ValueError('Refusing to overwrite an existing asset or metadata.')
    else:
        with open_inside(root, value):
            pass
    return path


class UnityControl(GodotControl):
    engine = 'unity'

    def __init__(self, config, run=None, popen=None):
        super().__init__(config, run, popen)
        self.session = None
        for job in self.jobs.values():
            if 'outcome unknown' in job.get('error', ''):
                job['outcome_unknown'] = True
                self.save(job)

    def probe(self):
        if platform.system() != 'Darwin' or platform.machine() != 'arm64':
            raise Unsupported('unsupported: Unity requires native macOS arm64.')
        if not self.settings.get('unity_executable'):
            raise Unsupported('unsupported: select an existing Unity Editor locally.')
        exe, version, minimum = editor(self.settings['unity_executable'])
        def numbers(s):
            return tuple((list(map(int, s.split('.'))) + [0, 0])[:3])
        if numbers(platform.mac_ver()[0]) < max(numbers(minimum), (11, 0, 0)):
            raise Unsupported('unsupported: this macOS version is below the Editor requirement.')
        return exe, version

    def project(self, ident):
        p = next((p for p in self.settings.get('projects', []) if p['id'] == ident and p.get('enabled') is True), None)
        if not p:
            raise ValueError('Project must be explicitly enabled locally.')
        root = canonical(p['root'])
        info = root.stat()
        if (root == Path.home().resolve() or any(part.startswith('.') for part in root.parts) or
                any(root == Path(p) or root.is_relative_to(p) for p in ('/System', '/Library', '/usr', '/bin', '/sbin', '/etc', '/private/etc', '/private/var/root'))):
            raise ValueError('Home, hidden and system directories cannot be approved as Unity projects.')
        if len(root.parts) < 3 or self.identities.get(ident) != (str(root), info.st_dev, info.st_ino):
            raise ValueError('Project identity changed; local review required.')
        root = inside(root, p['unity_subdir']) if p.get('unity_subdir') else root
        for name in ('Assets', 'Packages', 'ProjectSettings'):
            if not inside(root, name).is_dir():
                raise ValueError('Incomplete Unity project.')
        with open_inside(root, 'ProjectSettings/ProjectVersion.txt') as stream:
            text = stream.read(4096).decode('utf-8')
        match = re.search(r'^m_EditorVersion: (\S+)\s*$', text, re.M)
        if not match or match[1] != self.probe()[1]:
            raise Unsupported('unsupported: Editor must exactly match ProjectVersion.txt; no upgrades.')
        return p, root

    def authorize(self, project_id, authorized):
        p, root = self.project(project_id)
        if authorized is not True or p.get('allow_scripts') is not True:
            raise ValueError('Local allow_scripts and authorized=true required. Trusted C# runs as the user, WITHOUT a sandbox.')
        return root

    def validate(self, body):
        if not isinstance(body, dict) or set(body) - {'engine', 'operation', 'arguments', 'project_id', 'authorized', 'request_id'}:
            raise ValueError('Unexpected task fields.')
        op, args = body.get('operation'), body.get('arguments', {})
        if body.get('engine') != 'unity' or op not in {*FIELDS, 'start'}:
            raise Unsupported('unsupported: Unity operation is not exposed.')
        root = self.authorize(body.get('project_id'), body.get('authorized'))
        fields = {'timeout_seconds': 'integer'} if op == 'start' else {**FIELDS[op], 'timeout_seconds': 'integer'}
        if not isinstance(args, dict) or set(args) - set(fields) or (op != 'start' and set(REQUIRED[op]) - set(args)):
            raise ValueError('Unexpected or missing Unity arguments.')
        for key, value in args.items():
            kind = fields[key]
            valid = type(value) is int if kind == 'integer' else (type(value) in (float, int) and math.isfinite(value)) if kind == 'number' else isinstance(value, str) if kind == 'string' else isinstance(value, list) and len(value) == 3 and all(type(v) in (int, float) and math.isfinite(v) and abs(v) <= 100000 for v in value)
            if not valid:
                raise ValueError('Invalid typed Unity argument.')
        if not 1 <= args.get('timeout_seconds', 300 if op == 'start' else 60) <= 3600:
            raise ValueError('Timeout must be 1..3600 seconds.')
        if 'name' in args and (not 1 <= len(args['name']) <= 128 or any(ord(c) < 32 for c in args['name'])):
            raise ValueError('Invalid object name.')
        if 'component' in args:
            props = COMPONENTS.get(args['component'])
            if props is None:
                raise Unsupported('unsupported: only Camera, Light and BoxCollider are exposed.')
            if op == 'component.set':
                bounds = props.get(args['property'])
                if not bounds or not bounds[0] <= args['value'] <= bounds[1]:
                    raise Unsupported('unsupported: component property or value.')
        if op == 'scene.save':
            asset_path(root, args['path'], {'.unity'}, new=True)
        if op == 'asset.import':
            asset_path(root, args['path'], {'.png', '.jpg', '.jpeg', '.obj'})
        with open_inside(root, 'Packages/manifest.json') as stream:
            dependencies = json.load(stream).get('dependencies', {})
        if not isinstance(dependencies, dict):
            raise ValueError('Invalid package manifest.')
        for package in ('com.unity.render-pipelines.universal', 'com.unity.render-pipelines.high-definition'):
            if package in dependencies and (not isinstance(dependencies[package], str) or not re.fullmatch(r'17\.0\.\d+', dependencies[package])):
                raise Unsupported('unsupported: Unity 6000.0 requires reviewed URP/HDRP 17.0.x; packages are never changed.')
        # Fixed extension must already have been installed locally by the user.
        with open_inside(root, 'Assets/Editor/OrcaUnityBridge.cs') as stream:
            if stream.read() != EXTENSION.read_bytes():
                raise Unsupported('unsupported: reviewed Editor extension is missing or differs.')
        count = 0
        for folder, dirs, files in os.walk(root, followlinks=False):
            for name in dirs + files:
                count += 1
                if count > 100000 or (Path(folder) / name).is_symlink():
                    raise ValueError('Project contains symlinks or exceeds scan limit.')
        return root

    def health(self):
        s = self.session
        if not s or s['process'].poll() is not None:
            return None
        try:
            with open_inside(s['folder'], 'health.json') as stream:
                health = json.load(stream)
        except (ValueError, OSError, TypeError):
            # Atomic heartbeat replacement can briefly unlink an opened inode.
            # A previously verified heartbeat remains valid for at most five seconds.
            health = s.get('last_health', {})
        try:
            if (health.get('session') != s['secret'] or health.get('pid') != s['process'].pid or
                    health.get('project') != str(s['project']) or health.get('version') != s['version'] or
                    not 0 <= time.time() - health.get('timestamp', 0) <= 5):
                return None
            s['last_health'] = health
            return health
        except (ValueError, TypeError, AttributeError):
            return None

    def status(self, api_healthy=True):
        checks = dict(installed=False, path=False, permissions=False, version=False, architecture=False,
                      bridge_api=api_healthy, editor_api=False, metal=False, graphics=False)
        state = 'unsupported'
        with self.lock:
            try:
                self.probe()
                checks.update(installed=True, path=True, permissions=True, version=True, architecture=True)
                state = 'available' if api_healthy else 'installed'
                health = self.health()
                if self.session and self.session['process'].poll() is None:
                    state = 'installed'
                    if health:
                        checks.update(editor_api=True, metal=health.get('metal') is True, graphics=health.get('compatible') is True)
                        state = 'running' if api_healthy and checks['metal'] and checks['graphics'] else 'unsupported'
            except Unsupported:
                pass
            except Exception:
                state = 'error'
        return {'engine': 'unity', 'state': state, 'checks': checks, 'sandbox': False, 'network_isolation': False,
                'script_authorization_required': True, 'metal_status': 'verified_by_editor' if checks['metal'] else 'unverified'}

    def projects(self):
        return [{**p, 'project_types': ['unity']} for p in super().projects()]

    def start(self, project_id, authorized=None, timeout_seconds=300):
        return self.queue(dict(engine='unity', project_id=project_id, operation='start', authorized=authorized,
                               arguments={'timeout_seconds': timeout_seconds}))

    def tools(self, project_id, authorized=None):
        self.authorize(project_id, authorized)
        with self.lock:
            self.require_session(project_id)
            return {'tools': catalog()}

    def call(self, project_id, name, arguments=None, authorized=None):
        return self.queue(dict(engine='unity', project_id=project_id, operation=name,
                               arguments={} if arguments is None else arguments, authorized=authorized))

    def require_session(self, project_id):
        if not self.session or self.session['project_id'] != project_id or not self.health():
            raise ValueError('Managed Editor API is not healthy; no external Editor adoption.')
        return self.session

    def queue(self, body):
        body = copy.deepcopy(body)
        with self.lock:
            if self.closed:
                raise ValueError('Unity service stopped.')
            self.validate(body)
            ident = body.get('request_id') or uuid.uuid4().hex
            if not isinstance(ident, str) or not JOB.fullmatch(ident):
                raise ValueError('Invalid task ID.')
            fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
            if ident in self.jobs:
                if self.jobs[ident]['fingerprint'] != fingerprint:
                    raise ValueError('Task ID conflict.')
                return self.job_status(ident)
            if body['operation'] == 'start':
                if any(j['status'] not in TERMINAL for j in self.jobs.values()) or (self.session and self.session['process'].poll() is None):
                    raise ValueError('Stop the managed Editor before starting another session.')
            else:
                self.require_session(body['project_id'])
                if any(j['operation'] != 'start' and j['status'] not in TERMINAL for j in self.jobs.values()):
                    raise ValueError('A Unity edit is still pending; edits are not retried.')
            job = dict(id=ident, engine='unity', project_id=body['project_id'], operation=body['operation'],
                       status='queued', created_at=time.time(), artifacts=[], logs=['Authorized Unity task queued.'], fingerprint=fingerprint)
            self.jobs[ident] = job
            self.save(job)
            worker = threading.Thread(target=self.execute, args=(job, body), daemon=True)
            self.threads.append(worker)
            worker.start()
            return self.job_status(ident)

    def launch_editor(self, job, root):
        exe, version = self.probe()
        # Unity itself owns this lock. Never delete it or try to attach to its PID.
        if (root / 'Temp/UnityLockfile').exists():
            raise ValueError('Project may be open in an external Editor; local review required.')
        folder = private_dir(self.root / job['id'] / 'session')
        secret = secrets.token_hex(32)
        env = child_env(folder)
        env.update(ORCA_UNITY_SESSION=secret, ORCA_UNITY_SPOOL=str(folder), ORCA_UNITY_PROJECT=str(root))
        command = [str(exe), '-projectPath', str(root), '-force-metal', '-logFile', '/dev/null', '-job-worker-count', '4']
        proc = self.popen(command, cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, shell=False, start_new_session=True)
        self.session = dict(process=proc, folder=folder, secret=secret, project=root, project_id=job['project_id'], version=version, job_id=job['id'])
        self.active[job['id']] = proc
        job.update(pid=proc.pid, started_at=time.time(), project_identity=list(self.identities[job['project_id']])[1:])

    def create_scene_stage(self, root, ident):
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in [*root.parts[1:], 'Assets']:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            name = 'OrcaBridge-' + ident
            # The Editor creates matching folder metadata during normal import.
            try:
                os.stat(name + '.meta', dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ValueError('Staging metadata already exists.')
            os.mkdir(name, mode=0o700, dir_fd=fd)
        finally:
            os.close(fd)

    def execute(self, job, body):
        try:
            root = self.validate(body)
            with self.lock:
                if self.closed or job['status'] in TERMINAL:
                    return
                if job['operation'] == 'start':
                    self.launch_editor(job, root)
                session = self.session if job['operation'] == 'start' else self.require_session(job['project_id'])
                job['status'] = 'running'
                self.save(job)
                if job['operation'] != 'start':
                    if job['operation'] == 'scene.save':
                        # Exclusive, descriptor-pinned staging inside Assets: SaveScene requires a project-relative path.
                        self.create_scene_stage(root, job['id'])
                    request = dict(body.get('arguments', {}), operation=job['operation'], session=session['secret'], authorized=True, id_request=job['id'])
                    temp = session['folder'] / (job['id'] + '.tmp')
                    write_private(temp, json.dumps(request).encode())
                    os.rename(temp, session['folder'] / (job['id'] + '.request.json'))
            deadline = time.monotonic() + body.get('arguments', {}).get('timeout_seconds', 60)
            while True:
                with self.lock:
                    if job['status'] in TERMINAL:
                        return
                    code = session['process'].poll()
                    if code is not None:
                        job['exit_code'] = code
                        if job['operation'] == 'start':
                            self.active.pop(job['id'], None)
                        if job['operation'] == 'start' and job.get('ready') and code == 0:
                            job['status'] = 'succeeded'
                            return
                        raise ValueError('Editor exited.')
                    if job['operation'] == 'start':
                        health = self.health()
                        if not health and job.get('ready'):
                            raise ValueError('Editor heartbeat expired.')
                        if health:
                            if not health.get('metal') or not health.get('compatible'):
                                job['error_code'] = 'unsupported_graphics'
                                raise Unsupported('unsupported graphics configuration')
                            job['ready'] = True
                            self.save(job)
                    else:
                        response = session['folder'] / (job['id'] + '.response.json')
                        if response.exists():
                            with open_inside(session['folder'], response.name) as stream:
                                value = json.load(stream)
                            if value.get('session') != session['secret'] or value.get('id_request') != job['id']:
                                raise ValueError('Invalid response identity.')
                            if value.get('ok') is not True:
                                job['error_code'] = 'unsupported' if value.get('unsupported') else 'editor_rejected'
                                raise ValueError('Editor rejected operation.')
                            job['result'] = filtered(value.get('result', {}))
                            if job['operation'] == 'gameview.capture':
                                with open_inside(session['folder'], job['id'] + '.png') as stream:
                                    data = stream.read(LIMIT + 1)
                                png(data)
                                write_private(self.root / job['id'] / 'capture.png', data)
                                job['artifacts'] = [dict(index=0, filename='capture.png', bytes=len(data), sha256=hashlib.sha256(data).hexdigest())]
                            if job['operation'] == 'scene.save':
                                with open_inside(root, 'Assets/OrcaBridge-' + job['id'] + '/scene.unity') as stream:
                                    data = stream.read(LIMIT + 1)
                                asset_path(root, body['arguments']['path'], {'.unity'}, new=True)
                                publish(root, body['arguments']['path'], data)
                                write_private(self.root / job['id'] / 'scene.unity', data)
                                job['artifacts'] = [dict(index=0, filename='scene.unity', bytes=len(data), sha256=hashlib.sha256(data).hexdigest())]
                            job['status'] = 'succeeded'
                            return
                    if time.monotonic() >= deadline:
                        job.update(timed_out=True, outcome_unknown=True)
                        raise TimeoutError()
                time.sleep(.05)
        except Exception:
            with self.lock:
                if job['status'] not in TERMINAL:
                    self.end_session()
                if job['status'] != 'cancelled':
                    job.update(status='failed', error='Unity task failed; inspect local Editor, version, graphics and permissions. Edits are never retried.')
        finally:
            with self.lock:
                self.save(job)

    def end_session(self):
        if self.session:
            start = self.session['job_id']
            process = self.session['process']
            self.terminate(start)
            code = process.poll()
            self.session = None
            for job in self.jobs.values():
                if job['status'] not in TERMINAL:
                    job.update(status='failed', outcome_unknown=True, exit_code=code, error='Managed Editor session ended; inspect project before retrying.')
                    self.save(job)

    def cancel(self, ident):
        with self.lock:
            job = self.job_status(ident)
            if job['status'] not in TERMINAL:
                self.end_session()
                self.jobs[ident]['status'] = 'cancelled'
                self.save(self.jobs[ident])
            return self.job_status(ident)

    def stop(self, project_id, authorized=None):
        self.authorize(project_id, authorized)
        with self.lock:
            if self.session and self.session['project_id'] != project_id:
                raise ValueError('Not the managed project.')
            self.end_session()
            return {'stopped': True, 'external_processes_untouched': True}

    def close(self):
        with self.lock:
            self.closed = True
            self.end_session()
        for thread in self.threads:
            thread.join(timeout=5)
