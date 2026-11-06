"""Local Host workbench over the existing approved projects and task engines."""
import base64
import hashlib
import json
import os
import stat
from pathlib import Path
from .macos_godot import ID, JOB, relative, open_inside
from .macos import private_dir, write_private

FIELDS = {
    'engine-check': {'engine', 'path', 'authorized'},
    'artifact': {'engine', 'id', 'index'},
    'artifact-export': {'engine', 'id', 'index', 'target_engine', 'project_id', 'path'},
    'resource-import': {'engine', 'project_id', 'path', 'source'},
    'workflow-versions': {'workflow_id'},
    'workflow-update': {'workflow', 'expected_revision'},
    'workflow-delete': {'workflow_id', 'expected_revision'},
    'workflow-restore': {'workflow_id', 'revision', 'expected_revision'},
    'projects': set(), 'files': {'engine', 'project_id', 'path'},
    'edit': {'engine', 'project_id', 'path'}, 'read': {'engine', 'project_id', 'path'}, 'location': {'engine', 'project_id', 'path'},
    'submit': {'engine', 'project_id', 'task', 'arguments', 'request_id', 'authorized'},
    'stop': {'engine', 'project_id', 'authorized'}, 'jobs': set(), 'inspect': {'engine', 'id'}, 'cancel': {'engine', 'id'},
    'workflows': set(), 'workflow-import': {'workflow'},
    'workflow-submit': {'workflow_id', 'parameters', 'request_id'},
    'comfy-start': set(), 'comfy-stop': set(),
}
TEXT = {'.txt', '.md', '.json', '.gd', '.tscn', '.tres', '.godot', '.cs', '.py', '.shader', '.yaml', '.yml', '.toml', '.cfg'}


def directory(root, value):
    parts = relative(value, empty=True).split('/') if value else []
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in [*Path(root).parts[1:], *parts]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def dispatch(bridge, process, command):
    if not isinstance(command, dict) or command.get('action') not in FIELDS:
        raise ValueError('Unknown workbench action.')
    action = command['action']
    if set(command) != FIELDS[action] | {'action'}:
        raise ValueError('Unexpected or missing workbench fields.')
    engine = command.get('engine')
    if engine is not None and engine not in ('blender', 'godot', 'unity', 'comfyui'):
        raise ValueError('Unknown engine.')
    native = bridge.native
    if action == 'engine-check':
        if engine not in ('blender', 'godot') or command['authorized'] is not True:
            raise ValueError('Authorize an existing Blender or Godot installation probe.')
        control = getattr(native, engine)
        value = command['path']
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValueError('Select an absolute application path.')
        result = {'engine': engine, 'state': 'not_installed', 'checks': {'installed': False, 'path': False, 'permissions': False, 'architecture': False, 'version': False}}
        if not Path(value).exists():
            return result
        from .macos_godot import executable, Unsupported
        with native.lock, control.lock:
            if any(j['status'] not in ('succeeded', 'failed', 'cancelled') for j in control.jobs.values()):
                raise ValueError('Stop active engine tasks before changing installation.')
            control.settings = dict(control.settings)
            old = control.settings.get(engine + '_executable')
            try:
                result['checks']['installed'] = True
                # Bundle identity is mandatory for this UI; no generic executable launcher.
                if Path(value).suffix.lower() != '.app':
                    raise ValueError('Select the engine .app bundle.')
                exe = executable(value, engine)
                result['checks'].update(path=True, permissions=True, architecture=True)
                control.settings[engine + '_executable'] = str(exe)
                control.probe()
                result.update(state='available', version=control.detected_version, architecture='arm64', selected_path=value, scope='current Bridge session')
                result['checks']['version'] = True
            except Unsupported as error:
                result.update(state='incompatible', message=str(error))
            except (ValueError, OSError) as error:
                result.update(state='incompatible', message=str(error))
            except Exception:
                result.update(state='runtime_failed', message='Engine version/API probe failed. Check permissions and local application health.')
            finally:
                if result['state'] != 'available':
                    if old is None: control.settings.pop(engine + '_executable', None)
                    else: control.settings[engine + '_executable'] = old
        return result
    if action.startswith('workflow-') and action in ('workflow-update', 'workflow-delete', 'workflow-restore', 'workflow-versions'):
        return workflow_change(bridge, command)
    if action in ('artifact', 'artifact-export', 'resource-import'):
        from .macos_godot import canonical, LIMIT, png
        from .macos_blender import publish
        if action == 'resource-import':
            source = Path(command['source'])
            with open_inside(canonical(source.parent), source.name) as stream:
                data = stream.read(LIMIT + 1)
            filename = source.name
        else:
            ident, index = command['id'], command['index']
            if not isinstance(ident, str) or not JOB.fullmatch(ident) or type(index) is not int or not 0 <= index <= 9999:
                raise ValueError('Invalid artifact selection.')
            if engine == 'comfyui':
                job = bridge.inspect(ident)
                if job['status'] != 'succeeded' or index >= len(job['artifacts']):
                    raise ValueError('Artifact unavailable.')
                item = bridge.jobs[ident]['artifacts'][index]
                with bridge.adapters[bridge.jobs[ident]['kind']].artifact(item) as stream:
                    data = stream.read(LIMIT + 1)
                filename = item['filename']
            else:
                if native.owner(ident) is not getattr(native, engine):
                    raise ValueError('Task belongs to another engine.')
                filename = native.job_status(ident)['artifacts'][index]['filename']
                with native.open_artifact(ident, index) as stream:
                    data = stream.read(LIMIT + 1)
        if len(data) > LIMIT:
            raise ValueError('Resource exceeds 32 MB workbench limit; use existing artifact transfer tools.')
        if action == 'artifact':
            if Path(filename).suffix.lower() != '.png' or len(data) > 1024 * 1024:
                raise ValueError('Inline preview supports PNG up to 1 MB. Export this artifact to an approved project.')
            png(data)
            return {'filename': filename, 'mime': 'image/png', 'base64': base64.b64encode(data).decode()}
        target = command.get('target_engine', engine)
        if target not in ('unity', 'godot', 'blender'):
            raise ValueError('Select a native destination project.')
        _, root = getattr(native, target).project(command['project_id'])
        value = relative(command['path'])
        allowed = {'.png', '.jpg', '.jpeg', '.obj', '.glb', '.vrm', '.blend', '.unity', '.wav', '.mp3', '.mp4'}
        if Path(value).suffix.lower() not in allowed or Path(value).suffix.lower() != Path(filename).suffix.lower():
            raise ValueError('Keep a supported non-script resource extension.')
        if target == 'unity':
            from .macos_unity import asset_path
            asset_path(root, value, allowed, new=True)
        publish(root, value, data)
        return {'exported': value, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    if action == 'projects':
        engines = native.status()['engines']
        for row in engines:
            row['availability'] = 'not_installed' if not row['checks'].get('installed') else {'unsupported': 'incompatible', 'error': 'runtime_failed'}.get(row['state'], row['state'])
        return {'projects': native.projects(), 'engines': engines}
    if action in ('files', 'read', 'edit', 'location'):
        if engine == 'comfyui' or not isinstance(command['project_id'], str) or not ID.fullmatch(command['project_id']):
            raise ValueError('Select an approved native project.')
        control = getattr(native, engine)
        _, root = control.project(command['project_id'])
        value = relative(command['path'], empty=action in ('files', 'location'))
        if action == 'files' or (action == 'location' and value == ''):
            fd = directory(root, value)
            try:
                if action == 'location':
                    return {'path': str(root), 'directory': True}
                entries = []
                with os.scandir(fd) as items:
                    for item in items:
                        if len(entries) >= 2000:
                            raise ValueError('Directory has more than 2000 entries. Select a smaller folder.')
                        info = item.stat(follow_symlinks=False)
                        if item.name.startswith('.') or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)) or info.st_nlink > 1 and stat.S_ISREG(info.st_mode):
                            continue
                        entries.append({'name': item.name, 'directory': item.is_dir(follow_symlinks=False), 'path': '/'.join(filter(None, (value, item.name)))})
                return {'path': value, 'entries': sorted(entries, key=lambda e: (not e['directory'], e['name'].lower()))}
            finally:
                os.close(fd)
        with open_inside(root, value) as stream:
            if action in ('edit', 'location'):
                if Path(value).suffix.lower() not in TEXT:
                    raise ValueError('Select a text source file for the editor.')
                data = stream.read()
                if b'\x00' in data:
                    raise ValueError('Binary content cannot be edited as text.')
                data.decode('utf-8')
                return {'path': str(root / value), 'directory': False}
            if Path(value).suffix.lower() not in TEXT:
                return {'path': value, 'text': None, 'message': 'Binary asset: select it in the task arguments to open or import it.'}
            data = stream.read(256001)
            if len(data) > 256000:
                raise ValueError('Text preview is limited to 256 KB. Open the file in the editor.')
            return {'path': value, 'text': data.decode('utf-8')}
    if action == 'submit':
        if engine == 'comfyui' or command['authorized'] is not True:
            raise ValueError('Explicit project execution authorization is required.')
        return native.queue({k: v for k, v in command.items() if k not in ('action', 'task')} | {'operation': command['task']})
    if action == 'stop':
        if command['authorized'] is not True or engine not in ('godot', 'unity'):
            raise ValueError('Select an authorized Godot or Unity session.')
        control = getattr(native, engine)
        return control.stop(command['project_id'], authorized=True) if engine == 'unity' else control.stop(command['project_id'])
    if action == 'jobs':
        jobs = []
        for name in ('blender', 'godot', 'unity'):
            control = getattr(native, name)
            with control.lock if 'lock' in control.__dict__ else bridge.lock:
                ids = list(control.__dict__.get('jobs', {}))
            jobs.extend(control.job_status(ident) for ident in ids[-100:])
        with bridge.lock:
            for job in list(bridge.jobs.values())[-100:]:
                try:
                    public = bridge.inspect(job['id']) if job['status'] in ('queued', 'running', 'submitting', 'unknown') else bridge.public(job)
                except Exception:
                    public = dict(bridge.public(job), message='Could not refresh backend state. Check ComfyUI and inspect this task before retrying.')
                jobs.append(dict(public, engine='comfyui'))
        return {'jobs': sorted(jobs, key=lambda j: j['created_at'], reverse=True)[:100]}
    if action in ('inspect', 'cancel'):
        if not isinstance(command['id'], str) or not JOB.fullmatch(command['id']):
            raise ValueError('Invalid task ID.')
        ident = command['id']
        if engine == 'comfyui':
            return dict(getattr(bridge, action)(ident), engine='comfyui')
        if native.owner(ident) is not getattr(native, engine):
            raise ValueError('Task belongs to another engine.')
        job = native.cancel(ident) if action == 'cancel' else native.job_status(ident)
        return dict(job, **native.logs(ident))
    if action == 'workflows':
        return {'workflows': bridge.catalog()}
    if action == 'workflow-import':
        from bridge import validate_workflow
        workflow = validate_workflow(command['workflow'])
        folder = private_dir(bridge.root / 'workflows')
        with bridge.lock:
            target = folder / (workflow['id'] + '.json')
            if target.exists() or target.is_symlink():
                raise ValueError('Workflow ID already exists. Import with a new ID.')
            archive_workflow(bridge, workflow)
            write_private(target, json.dumps(workflow).encode())
        return {'imported': workflow['id']}
    if action == 'workflow-submit':
        return dict(bridge.submit({k: v for k, v in command.items() if k != 'action'}), engine='comfyui')
    return process.start() if action == 'comfy-start' else process.stop()


def revision(workflow):
    return hashlib.sha256(json.dumps(workflow, sort_keys=True).encode()).hexdigest()


def archive_workflow(bridge, workflow):
    folder = private_dir(private_dir(bridge.root / 'workflow-history') / workflow['id'])
    target = folder / (revision(workflow) + '.json')
    if not target.exists():
        write_private(target, json.dumps(workflow).encode())


def workflow_change(bridge, command):
    from bridge import validate_workflow
    import re
    action = command['action']
    workflow = validate_workflow(command['workflow']) if action == 'workflow-update' else None
    ident = workflow['id'] if workflow else command['workflow_id']
    if not isinstance(ident, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', ident):
        raise ValueError('Only imported API workflows can be managed here.')
    folder = private_dir(bridge.root / 'workflows')
    with bridge.lock:
        target = folder / (ident + '.json')
        current = None
        if target.exists() or target.is_symlink():
            with open_inside(folder, target.name) as stream:
                current = validate_workflow(json.load(stream))
            archive_workflow(bridge, current)
        history = private_dir(private_dir(bridge.root / 'workflow-history') / ident)
        if action == 'workflow-versions':
            return {'workflow_id': ident, 'current_revision': revision(current) if current else None,
                    'versions': sorted(p.stem for p in history.glob('*.json') if re.fullmatch(r'[a-f0-9]{64}', p.stem))}
        if command['expected_revision'] != (revision(current) if current else None):
            raise ValueError('Workflow changed. Refresh versions before modifying it.')
        if action == 'workflow-delete':
            if current is None: raise ValueError('Workflow is already deleted.')
            target.unlink()
            return {'deleted': ident, 'retained_versions': True}
        if action == 'workflow-restore':
            value = command['revision']
            if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value):
                raise ValueError('Invalid workflow revision.')
            with open_inside(history, value + '.json') as stream:
                workflow = validate_workflow(json.load(stream))
            if revision(workflow) != value or workflow['id'] != ident:
                raise ValueError('Workflow revision integrity failure.')
        elif current is None:
            raise ValueError('Import a new workflow first.')
        archive_workflow(bridge, workflow)
        import uuid
        temp = folder / (uuid.uuid4().hex + '.tmp')
        try:
            write_private(temp, json.dumps(workflow).encode())
            os.replace(temp, target)
        finally:
            if temp.exists(): temp.unlink()
        return {'workflow_id': ident, 'revision': revision(workflow)}
