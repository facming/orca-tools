"""Opt-in Godot on macOS. Trusted user code, NOT a filesystem/network sandbox."""
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import plistlib
import re
import signal
import stat
import struct
import subprocess
import threading
import time
import uuid
import zlib

from .macos import private_dir, private_path, write_private

ID = re.compile(r'^[a-zA-Z0-9_-]{1,64}$')
JOB = re.compile(r'^[a-f0-9]{32}$')
LIMIT = 32 * 1024 * 1024
TERMINAL = {'succeeded', 'failed', 'cancelled'}


class Unsupported(ValueError):
    pass


def relative(value, empty=False):
    if empty and value == '':
        return ''
    if not isinstance(value, str) or not value or len(value) > 1024 or any(ord(c) < 32 for c in value):
        raise ValueError('Invalid project-relative path.')
    if '\\' in value or ':' in value or any(p in ('', '.', '..') for p in value.split('/')):
        raise ValueError('Invalid project-relative path.')
    return value


def canonical(value):
    p = Path(value)
    if not p.is_absolute() or '..' in p.parts or any(x.is_symlink() for x in (p, *p.parents)):
        raise ValueError('An absolute path without symbolic links is required.')
    return p.resolve(strict=True)


def inside(root, value):
    p = root / relative(value)
    if any(x.is_symlink() for x in (p, *p.parents)) or not p.resolve(strict=True).is_relative_to(root):
        raise ValueError('Project path escaped its approved directory.')
    return p


def open_inside(root, value):
    """Walk using directory descriptors; never follow a swapped ancestor link."""
    parts = relative(value).split('/')
    root = Path(root)
    if not root.is_absolute() or '..' in root.parts:
        raise ValueError('Invalid artifact root.')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in [*root.parts[1:], *parts[:-1]]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        leaf = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        info = os.fstat(leaf)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > LIMIT:
            os.close(leaf)
            raise ValueError('Artifact must be a bounded regular file without hard links.')
        return os.fdopen(leaf, 'rb')
    finally:
        os.close(fd)


def executable(value, engine="godot"):
    p = canonical(value)
    if p.suffix.lower() == '.app':
        with inside(p, 'Contents/Info.plist').open('rb') as stream:
            meta = plistlib.load(stream)
        name = meta.get('CFBundleExecutable', '')
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', name) or engine not in meta.get('CFBundleIdentifier', '').lower():
            raise ValueError('Select a ' + engine.title() + ' application bundle.')
        p = inside(p, 'Contents/MacOS/' + name)
    info = p.stat()
    if not stat.S_ISREG(info.st_mode) or not os.access(p, os.X_OK) or info.st_mode & 0o022:
        raise ValueError(engine.title() + ' executable permissions are unsafe.')
    # Inspect Mach-O before executing a user-selected file. Scripts/ELF/PE rejected.
    with p.open('rb') as stream:
        data = stream.read(4096)
    architectures = set()
    if data[:4] == b'\xcf\xfa\xed\xfe' and len(data) >= 32:
        cpu, _, kind = struct.unpack_from('<III', data, 4)
        if kind == 2:
            architectures.add(cpu)
    elif data[:4] in (b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf') and len(data) >= 8:
        count = struct.unpack_from('>I', data, 4)[0]
        step = 32 if data[3] == 0xbf else 20
        if not 1 <= count <= 16 or len(data) < 8 + count * step:
            raise ValueError('Invalid universal executable.')
        architectures = {struct.unpack_from('>I', data, 8 + i * step)[0] for i in range(count)}
    if 0x0100000c not in architectures:
        raise Unsupported(engine.title() + ' requires a native arm64 Mach-O executable; Intel/Rosetta is unsupported.')
    return p


def discover(search_dirs=None, selected=None):
    """Metadata-only candidates; discovery never probes or authorizes execution."""
    candidates = [Path(selected)] if selected else []
    for directory in search_dirs if search_dirs is not None else (Path('/Applications'), Path.home() / 'Applications'):
        folder = Path(directory)
        if folder.is_dir():
            candidates.extend(sorted(folder.glob('*.app')))
    result = []
    for candidate in candidates:
        try:
            p = executable(candidate)
            if str(p) not in result:
                result.append(str(p))
        except (ValueError, OSError, plistlib.InvalidFileException):
            pass
    return result


def child_env(home):
    # No inherited PATH, DYLD_*, PYTHON*, credentials, locale or login identity.
    return {'PATH': '/usr/bin:/bin', 'HOME': str(home), 'TMPDIR': str(home), 'LANG': 'C.UTF-8'}


def validate_settings(settings):
    if not isinstance(settings, dict) or set(settings) - {'godot_executable', 'blender_executable', 'blender_device', 'unity_executable', 'projects'}:
        raise ValueError('Only native executable, device and project settings are supported on macOS.')
    if settings.get('godot_executable') is not None and (not isinstance(settings['godot_executable'], str) or not Path(settings['godot_executable']).is_absolute()):
        raise ValueError('Godot must be explicitly configured with an absolute path.')
    if settings.get('blender_device', 'metal') not in ('metal', 'cpu'):
        raise ValueError('Blender device must be metal or cpu.')
    if 'blender_executable' in settings and (not isinstance(settings['blender_executable'], str) or not Path(settings['blender_executable']).is_absolute()):
        raise ValueError('Blender needs an absolute selected path.')
    if 'unity_executable' in settings and (not isinstance(settings['unity_executable'], str) or not Path(settings['unity_executable']).is_absolute()):
        raise ValueError('Unity needs an absolute selected path.')
    projects = settings.get('projects', [])
    if not isinstance(projects, list) or len(projects) > 100:
        raise ValueError('Invalid approved projects.')
    seen = set()
    for p in projects:
        if not isinstance(p, dict) or set(p) - {'id', 'name', 'root', 'godot_subdir', 'unity_subdir', 'enabled', 'allow_scripts'}:
            raise ValueError('Unexpected Godot project settings.')
        if not isinstance(p.get('id'), str) or not ID.fullmatch(p['id']) or p['id'] in seen:
            raise ValueError('Invalid or duplicate project ID.')
        seen.add(p['id'])
        if not isinstance(p.get('root'), str) or not Path(p['root']).is_absolute():
            raise ValueError('Project root must be explicitly configured.')
        relative(p.get('godot_subdir', ''), empty=True)
        relative(p.get('unity_subdir', ''), empty=True)
        for key in ('enabled', 'allow_scripts'):
            if key in p and type(p[key]) is not bool:
                raise ValueError('Project permissions must be boolean.')


def png(data, width=None, height=None):
    """Validate PNG framing, checksums, dimensions and bounded decoded pixels."""
    if not data.startswith(b'\x89PNG\r\n\x1a\n') or len(data) > LIMIT:
        raise ValueError('Invalid PNG artifact.')
    pos, compressed, dimensions, ended = 8, bytearray(), None, False
    while pos + 12 <= len(data):
        size = struct.unpack_from('>I', data, pos)[0]
        kind = data[pos+4:pos+8]
        payload = data[pos+8:pos+8+size]
        if pos + size + 12 > len(data) or zlib.crc32(kind + payload) != struct.unpack_from('>I', data, pos+8+size)[0]:
            raise ValueError('Corrupt PNG artifact.')
        if dimensions is None:
            if kind != b'IHDR' or size != 13:
                raise ValueError('Missing PNG header.')
            w, h, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', payload)
            if not 1 <= w <= 4096 or not 1 <= h <= 4096 or depth != 8 or color not in (2, 6) or compression or filtering or interlace:
                raise ValueError('Unsupported PNG format.')
            if (width and w != width) or (height and h != height):
                raise ValueError('Screenshot dimensions do not match request.')
            dimensions = (w, h, 3 if color == 2 else 4)
        elif kind == b'IDAT':
            compressed.extend(payload)
        elif kind == b'IEND':
            ended = size == 0 and pos + 12 == len(data)
            break
        elif kind == b'IHDR':
            raise ValueError('Duplicate PNG header.')
        pos += 12 + size
    if not ended or not compressed or dimensions is None:
        raise ValueError('Incomplete PNG artifact.')
    w, h, channels = dimensions
    expected = h * (1 + w * channels)
    decoder = zlib.decompressobj()
    pixels = decoder.decompress(bytes(compressed), expected + 1)
    if len(pixels) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise ValueError('Invalid PNG pixels.')
    if any(pixels[i] > 4 for i in range(0, expected, 1 + w * channels)):
        raise ValueError('Invalid PNG filter.')


class GodotControl:
    engine = "godot"
    def __init__(self, config, run=None, popen=None):
        self.settings = config.get('native') or {}
        validate_settings(self.settings)
        self.root = private_dir(Path(config['data_dir']) / self.engine)
        self.run, self.popen = run or subprocess.run, popen or subprocess.Popen
        self.lock = threading.RLock()
        self.jobs, self.active, self.threads = {}, {}, []
        self.closed = False
        self.identities = {}
        for p in self.settings.get('projects', []):
            try:
                root = canonical(p['root'])
                info = root.stat()
                self.identities[p['id']] = (str(root), info.st_dev, info.st_ino)
            except (OSError, ValueError):
                pass
        # Historical PIDs are metadata only. Never signal/adopt them after restart.
        for file in self.root.glob('*/job.json'):
            if not JOB.fullmatch(file.parent.name):
                continue
            value = json.loads(private_path(file).read_text())
            if value['id'] != file.parent.name:
                raise ValueError('Invalid stored Godot task.')
            self.jobs[value['id']] = value
            if value['status'] not in TERMINAL:
                value.update(status='failed', error='Bridge restarted; execution outcome unknown. No process adopted.')
                self.save(value)

    def save(self, job):
        folder = private_dir(self.root / job['id'])
        temp = folder / (uuid.uuid4().hex + '.tmp')
        write_private(temp, json.dumps(job).encode())
        target = folder / 'job.json'
        if target.exists():
            private_path(target)
        os.replace(temp, target)

    def probe(self):
        if platform.system() != 'Darwin' or platform.machine() != 'arm64':
            raise Unsupported('Only native macOS arm64 is supported.')
        value = self.settings.get('godot_executable')
        if not value:
            raise Unsupported('Configure an existing trusted Godot executable first.')
        p = executable(value)
        result = self.run([str(p), '--version'], cwd=self.root, env=child_env(self.root), stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=5, check=True, shell=False)
        version = result.stdout.strip()
        if not re.fullmatch(r'4\.(?:[5-9]|[1-9][0-9]+)(?:\.[0-9]+)?\.stable(?:\.[A-Za-z0-9_-]+)*', version):
            raise Unsupported('Godot stable 4.5 or newer within major 4 is required.')
        help_result = self.run([str(p), '--help'], cwd=self.root, env=child_env(self.root), stdin=subprocess.DEVNULL,
                               capture_output=True, text=True, timeout=5, check=True, shell=False)
        if not all(flag in help_result.stdout for flag in ('Godot', '--editor', '--import', '--headless', '--recovery-mode')):
            raise Unsupported('Godot editor with headless import and recovery mode is required.')
        return p

    def status(self, api_healthy=True):
        checks = dict(installed=False, path=False, permissions=False, version=False, architecture=False, bridge_api=api_healthy)
        state = 'unsupported'
        if self.settings.get('godot_executable'):
            try:
                checks['installed'] = Path(self.settings['godot_executable']).exists()
                executable(self.settings['godot_executable'])
                checks.update(path=True, permissions=True, architecture=True)
                self.probe()
                checks['version'] = True
                state = 'available' if api_healthy else 'installed'
                with self.lock:
                    if api_healthy and any(x.poll() is None for x in self.active.values()):
                        state = 'running'
            except Unsupported:
                state = 'unsupported'
            except Exception:
                state = 'error'
        return {'engine': 'godot', 'state': state, 'checks': checks, 'network_isolation': False,
                'script_authorization_required': True, 'sandbox': False}

    def projects(self):
        result = []
        for p in self.settings.get('projects', []):
            try:
                self.project(p['id'])
                result.append({'id': p['id'], 'name': p['id'], 'enabled': True, 'allow_scripts': p.get('allow_scripts') is True,
                               'project_types': ['godot']})
            except (ValueError, OSError):
                continue
        return result

    def project(self, ident):
        p = next((p for p in self.settings.get('projects', []) if p['id'] == ident and p.get('enabled') is True), None)
        if not p:
            raise ValueError('Project is not explicitly enabled.')
        root = canonical(p['root'])
        info = root.stat()
        if self.identities.get(ident) != (str(root), info.st_dev, info.st_ino):
            raise ValueError('Project identity changed; restart and review local configuration.')
        sub = p.get('godot_subdir', '')
        client = inside(root, sub) if sub else root
        if not client.is_dir() or not inside(client, 'project.godot').is_file():
            raise ValueError('Approved directory is not a Godot project.')
        return p, client

    def validate(self, body):
        if set(body) - {'project_id', 'engine', 'operation', 'arguments', 'request_id', 'authorized'}:
            raise ValueError('Unexpected task fields.')
        if body.get('engine') != 'godot' or body.get('operation') not in ('open', 'run', 'import', 'script', 'capture'):
            raise Unsupported('unsupported: this macOS operation is not exposed.')
        p, root = self.project(body.get('project_id'))
        if body.get('authorized') is not True or p.get('allow_scripts') is not True:
            raise ValueError('Explicit user authorization is required: project scripts run with login-user permissions, without a sandbox.')
        args = body.get('arguments', {})
        common = {'timeout_seconds'}
        fields = {'open': set(), 'import': set(), 'run': {'headless', 'scene', 'user_args'},
                  'script': {'headless', 'script', 'project_subdir', 'user_args', 'output_paths'},
                  'capture': {'scene', 'width', 'height', 'wait_seconds', 'renderer', 'user_args'}}
        if not isinstance(args, dict) or set(args) - common - fields[body['operation']]:
            raise ValueError('Unexpected Godot arguments.')
        timeout = args.get('timeout_seconds', 120)
        if type(timeout) is not int or not 1 <= timeout <= 3600:
            raise ValueError('Timeout must be 1 to 3600 seconds.')
        if 'headless' in args and type(args['headless']) is not bool:
            raise ValueError('headless must be boolean.')
        user = args.get('user_args', [])
        if not isinstance(user, list) or len(user) > 64 or any(not isinstance(x, str) or len(x) > 1024 or any(ord(c) < 32 for c in x) for x in user):
            raise ValueError('Invalid project arguments.')
        if 'project_subdir' in args and args['project_subdir'] != p.get('godot_subdir', ''):
            raise Unsupported('unsupported: project_subdir must match the locally approved Godot directory.')
        for key, suffix in (('scene', '.tscn'), ('script', '.gd')):
            if key in args:
                file = inside(root, args[key])
                if file.suffix != suffix or not file.is_file():
                    raise ValueError('Invalid project source.')
        if body['operation'] == 'script' and 'script' not in args:
            raise ValueError('Select a project GDScript.')
        if body['operation'] == 'capture':
            if 'scene' not in args:
                inside(root, 'scenes/main.tscn')
            for key, default in (('width', 1280), ('height', 720)):
                if type(args.get(key, default)) is not int or not 64 <= args.get(key, default) <= 4096:
                    raise ValueError('Capture dimensions must be 64 to 4096.')
            delay = args.get('wait_seconds', 3)
            if type(delay) not in (int, float) or not math.isfinite(delay) or not .1 <= delay <= 60:
                raise ValueError('Invalid capture delay.')
            if args.get('renderer', 'gl_compatibility') not in ('forward_plus', 'mobile', 'gl_compatibility'):
                raise ValueError('Unsupported renderer.')
        outputs = args.get('output_paths', [])
        if not isinstance(outputs, list) or len(outputs) > 16 or len(set(outputs)) != len(outputs):
            raise ValueError('Select up to 16 distinct PNG output paths.')
        for value in outputs:
            relative(value)
            if Path(value).suffix.lower() != '.png':
                raise Unsupported('unsupported: only validated PNG artifacts are exposed on macOS.')
            if (root / value).exists() or (root / value).is_symlink():
                raise ValueError('Output already exists; refusing to register or overwrite it.')
        # Resource/script links anywhere in the project could escape --path.
        # This is a preflight guard, not containment of authorized arbitrary code.
        count = 0
        for folder, dirs, files in os.walk(root, followlinks=False):
            for name in dirs + files:
                count += 1
                if count > 100000 or (Path(folder) / name).is_symlink():
                    raise ValueError('Project contains symbolic links or exceeds scan limit.')
        return root

    def queue(self, body):
        with self.lock:
            if self.closed:
                raise ValueError('Godot service is stopped.')
            ident = body.get('request_id') or uuid.uuid4().hex
            if not isinstance(ident, str) or not JOB.fullmatch(ident):
                raise ValueError('Invalid request ID.')
            fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
            if ident in self.jobs:
                if self.jobs[ident]['fingerprint'] != fingerprint:
                    raise ValueError('Request ID already belongs to another task.')
                return self.job_status(ident)
            self.validate(body)
            # One owned task per project; external instances are never discovered/adopted.
            if any(j['project_id'] == body['project_id'] and j['status'] not in TERMINAL for j in self.jobs.values()):
                raise ValueError('This project already has a managed task.')
            job = {'id': ident, 'engine': 'godot', 'project_id': body['project_id'], 'operation': body['operation'],
                   'status': 'queued', 'created_at': time.time(), 'artifacts': [], 'fingerprint': fingerprint,
                   'authorized': True, 'logs': ['User-authorized Godot task queued.']}
            self.jobs[ident] = job
            self.save(job)
            worker = threading.Thread(target=self.execute, args=(job, body), daemon=True)
            self.threads.append(worker)
            worker.start()
            return self.job_status(ident)

    def launch(self, job, command, root, deadline):
        folder = self.root / job['id']
        with self.lock:
            if job['status'] == 'cancelled' or self.closed:
                return False
            proc = self.popen(command, cwd=root, env=child_env(folder), stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False, start_new_session=True)
            self.active[job['id']] = proc
            job.update(status='running', pid=proc.pid, started_at=time.time(), project_identity=list(self.identities[job['project_id']])[1:])
            job['logs'].append('Managed Godot process started; raw application output suppressed.')
            self.save(job)
        while True:
            with self.lock:
                if job['status'] == 'cancelled':
                    return False
                code = proc.poll()
                if code is not None:
                    job['exit_code'] = code
                    self.active.pop(job['id'], None)
                    if code:
                        raise ValueError('Godot exited unsuccessfully.')
                    return True
                if time.monotonic() >= deadline:
                    job['timed_out'] = True
                    self.terminate(job['id'])
                    raise ValueError('Godot task timed out.')
            time.sleep(.05)

    def terminate(self, ident):
        # Popen is held in memory; only its still-live session group is signalled.
        proc = self.active.get(ident)
        if proc and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait(timeout=3)
            except ProcessLookupError:
                pass
        if proc:
            self.jobs[ident]['exit_code'] = proc.poll()
        self.active.pop(ident, None)

    def execute(self, job, body):
        try:
            root = self.validate(body)
            exe = str(self.probe())
            args, op = body.get('arguments', {}), job['operation']
            folder = self.root / job['id']
            deadline = time.monotonic() + args.get('timeout_seconds', 120)
            base = [exe, '--path', str(root), '--audio-driver', 'Dummy', '--log-file', '/dev/null']
            if op in ('run', 'script', 'capture', 'import'):
                if not self.launch(job, base + ['--headless', '--editor', '--import', '--recovery-mode'], root, deadline):
                    return
            command = base[:]
            if op == 'open':
                command += ['--editor']
            elif op == 'run':
                if args.get('headless', False):
                    command += ['--headless']
                if args.get('scene'):
                    command += ['res://' + args['scene']]
            elif op == 'script':
                if args.get('headless', True):
                    command += ['--headless']
                command += ['--script', 'res://' + args['script']]
            elif op == 'capture':
                output = folder / 'capture.png'
                request = {'scene': args.get('scene', 'scenes/main.tscn'), 'width': args.get('width', 1280),
                           'height': args.get('height', 720), 'wait_seconds': args.get('wait_seconds', 3), 'output': str(output)}
                write_private(folder / 'capture.json', json.dumps(request).encode())
                command += ['--rendering-method', args.get('renderer', 'gl_compatibility'), '--script',
                            str(Path(__file__).resolve().parents[1] / 'native_godot.gd'), '--', str(folder / 'capture.json')]
            if op != 'import':
                if op != 'capture':
                    command += ['--']
                command += args.get('user_args', [])
                if not self.launch(job, command, root, deadline):
                    return
            artifacts = []
            sources = [(folder, 'capture.png')] if op == 'capture' else [(root, value) for value in args.get('output_paths', [])]
            for index, (source_root, name) in enumerate(sources):
                with open_inside(source_root, name) as stream:
                    data = stream.read(LIMIT + 1)
                png(data, args.get('width', 1280) if op == 'capture' else None, args.get('height', 720) if op == 'capture' else None)
                filename = 'artifact-' + str(index) + '.png'
                write_private(folder / filename, data)
                artifacts.append({'index': index, 'filename': filename, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)})
            with self.lock:
                if job['status'] != 'cancelled':
                    job.update(status='succeeded', artifacts=artifacts, result={'exit_code': job.get('exit_code', 0)})
                    job['logs'].append('Godot task completed; declared artifacts validated.')
        except Exception:
            with self.lock:
                self.terminate(job['id'])
                if job['status'] != 'cancelled':
                    job.update(status='failed', error='Godot task failed or timed out. Verify trusted project, permissions, version and declared PNG outputs.')
                    job['logs'].append('Godot task failed or timed out; private diagnostics suppressed.')
        finally:
            with self.lock:
                self.save(job)

    def job_status(self, ident):
        with self.lock:
            if not isinstance(ident, str) or not JOB.fullmatch(ident) or ident not in self.jobs:
                raise ValueError('Unknown Godot task.')
            return {k: v for k, v in self.jobs[ident].items() if k not in ('fingerprint', 'pid', 'project_identity', 'logs')}

    def logs(self, ident):
        self.job_status(ident)
        with self.lock:
            return {'job_id': ident, 'logs': list(self.jobs[ident]['logs']), 'raw_output_available': False}

    def cancel(self, ident):
        with self.lock:
            self.job_status(ident)
            job = self.jobs[ident]
            if job['status'] not in TERMINAL:
                self.terminate(ident)
                job['status'] = 'cancelled'
                job['logs'].append('Managed Godot task cancelled.')
                self.save(job)
            return self.job_status(ident)

    def stop(self, project_id):
        self.project(project_id)
        with self.lock:
            jobs = [self.cancel(j['id']) for j in self.jobs.values() if j['project_id'] == project_id and j['status'] not in TERMINAL]
            return {'state': 'stopped', 'jobs': jobs, 'external_processes_untouched': True}

    def open_artifact(self, ident, index):
        with self.lock:
            job = self.job_status(ident)
            if job['status'] != 'succeeded' or not 0 <= index < len(job['artifacts']):
                raise ValueError('Artifact unavailable.')
            item = job['artifacts'][index]
            stream = open_inside(canonical(self.root / ident), item['filename'])
            try:
                data = stream.read(LIMIT + 1)
                if len(data) != item['bytes'] or hashlib.sha256(data).hexdigest() != item['sha256']:
                    raise ValueError('Artifact changed after registration.')
                # Serve validated bytes, not a path that can be replaced after verification.
                import io
                return io.BytesIO(data)
            finally:
                stream.close()

    def close(self):
        with self.lock:
            self.closed = True
            for ident in self.jobs:
                if self.jobs[ident]['status'] not in TERMINAL:
                    self.cancel(ident)
        for worker in self.threads:
            worker.join(timeout=10)


class UnavailableGodot:
    """A damaged Godot task store must not disable managed ComfyUI."""
    def status(self, api_healthy=True):
        return {'engine': 'godot', 'state': 'error', 'checks': {}, 'sandbox': False, 'network_isolation': False}

    def close(self):
        pass

    def __getattr__(self, name):
        raise Unsupported('unsupported: Godot storage requires local review; ComfyUI remains independent.')
