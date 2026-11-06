"""Real 6000.5.4f1 integration in a NEW disposable project; no user project/config."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'services/local-gpu-bridge'))
from bridge_platform.macos_unity import UnityControl, EXTENSION

root = Path(tempfile.mkdtemp(prefix='orca-unity-7b13-')).resolve()
project = root / 'Project'
for folder in ('Assets/Editor', 'Packages', 'ProjectSettings'):
    (project / folder).mkdir(parents=True)
(project / 'Assets/Editor/OrcaUnityBridge.cs').write_bytes(EXTENSION.read_bytes())
(project / 'ProjectSettings/ProjectVersion.txt').write_text('m_EditorVersion: 6000.5.4f1\n')
(project / 'Packages/manifest.json').write_text(json.dumps({'dependencies': {'com.unity.modules.screencapture': '1.0.0', 'com.unity.modules.physics': '1.0.0', 'com.unity.modules.imageconversion': '1.0.0'}}))
exe = '/Applications/Unity/Hub/Editor/6000.5.4f1/Unity.app'
def launch(args, **kwargs):
    args = list(args) + ['-batchmode']
    args[args.index('-logFile') + 1] = str(root / 'Editor.log')
    return subprocess.Popen(args, **kwargs)
control = UnityControl({'data_dir': str(root / 'Bridge'), 'native': {'unity_executable': exe, 'projects': [{'id': 'live', 'root': str(project), 'enabled': True, 'allow_scripts': True}]}}, popen=launch)
def wait(check, timeout=180):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        result = check()
        if result: return result
        time.sleep(.25)
    raise AssertionError('Deadline; inspect ' + str(root / 'Editor.log'))
def call(operation, **arguments):
    job = control.call('live', operation, arguments, authorized=True)
    result = wait(lambda: (v if (v := control.job_status(job['id']))['status'] in ('succeeded', 'failed', 'cancelled') else None))
    assert result['status'] == 'succeeded', result
    print(json.dumps({'operation': operation, 'result': result}), flush=True)
    return result
try:
    print('Evidence: ' + str(root), flush=True)
    start = control.start('live', True, timeout_seconds=900)
    def ready():
        value = control.job_status(start['id'])
        assert value['status'] not in ('failed', 'cancelled'), value
        return control.health()
    health = wait(ready, 300)
    assert health['metal'] and health['compatible'] and health['protocol'] == 2, health
    print(json.dumps({'health': {k:v for k,v in health.items() if k not in ('session', 'project')}}), flush=True)
    call('editor.status'); call('scene.get'); call('hierarchy.get')
    obj = call('gameobject.create', name='Orca Live Camera')['result']['rows'][0]['id']
    assert isinstance(obj, str) and obj.isdecimal()
    call('gameobject.get', id=obj)
    call('transform.set', id=obj, position=[0, 0, -10])
    call('component.add', id=obj, component='Camera')
    call('component.set', id=obj, component='Camera', property='field of view', value=55)
    call('scene.save', path='Assets/Live.unity')
    (project / 'Assets/triangle.obj').write_text('v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n')
    call('asset.import', path='Assets/triangle.obj'); call('assets.list')
    call('play.start')
    wait(lambda: call('editor.status')['result']['playing'])
    call('gameview.capture')
    call('play.stop')
    wait(lambda: not call('editor.status')['result']['playing'])
    call('gameobject.delete', id=obj); call('console.get')
    print('PASS real Unity 6000.5.4f1 compile/Metal/heartbeat/scene/objects/Play/capture/import', flush=True)
finally:
    control.close()
    print('Preserved disposable evidence: ' + str(root), flush=True)
