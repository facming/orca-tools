"""Local Host workbench over the existing approved projects and task engines."""
import json
import os
import stat
from pathlib import Path
from .macos_godot import ID, JOB, relative, open_inside
from .macos import private_dir, write_private

FIELDS = {
    'projects': set(), 'files': {'engine', 'project_id', 'path'},
    'read': {'engine', 'project_id', 'path'}, 'location': {'engine', 'project_id', 'path'},
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
    if action == 'projects':
        return {'projects': native.projects()}
    if action in ('files', 'read', 'location'):
        if engine == 'comfyui' or not isinstance(command['project_id'], str) or not ID.fullmatch(command['project_id']):
            raise ValueError('Select an approved native project.')
        control = getattr(native, engine)
        _, root = control.project(command['project_id'])
        value = relative(command['path'], empty=action != 'read')
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
            if action == 'location':
                if Path(value).suffix.lower() not in TEXT:
                    raise ValueError('Select a text source file for the editor.')
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
            write_private(target, json.dumps(workflow).encode())
        return {'imported': workflow['id']}
    if action == 'workflow-submit':
        return dict(bridge.submit({k: v for k, v in command.items() if k != 'action'}), engine='comfyui')
    return process.start() if action == 'comfy-start' else process.stop()
