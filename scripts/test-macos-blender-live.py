"""Real installed Blender in background mode, disposable project and configuration."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'services/local-gpu-bridge'))
from bridge_platform.macos_blender import BlenderControl
device = 'cpu' if '--cpu' in sys.argv else 'metal'
root = Path(tempfile.mkdtemp(prefix='orca-blender-7b13-')).resolve()
project = root/'Project'; project.mkdir()
log = (root/'Blender.log').open('wb')
def launch(args, **kwargs):
    kwargs.update(stdout=log, stderr=log)
    return subprocess.Popen([args[0], '--background', *args[1:]], **kwargs)
control = BlenderControl({'data_dir': str(root/'Bridge'), 'native': {'blender_executable': '/Applications/Blender.app', 'blender_device': device, 'projects': [{'id':'live','root':str(project),'enabled':True,'allow_scripts':False}]}}, popen=launch)
def call(operation, **arguments):
    job = control.queue(dict(engine='blender', project_id='live', operation=operation, arguments=arguments, authorized=True))
    deadline = time.monotonic()+180
    while time.monotonic()<deadline:
        value = control.job_status(job['id'])
        if value['status'] in ('succeeded','failed','cancelled'):
            print(json.dumps(value), flush=True)
            assert value['status']=='succeeded', value
            return value
        time.sleep(.1)
    raise AssertionError('Deadline; inspect '+str(root/'Blender.log'))
try:
    print('Evidence: '+str(root), flush=True)
    control.probe(); print('Version: '+control.detected_version, flush=True)
    call('scene_info'); call('create_primitive', kind='cube', name='OrcaLiveCube')
    call('set_transform', name='OrcaLiveCube', location=[1,0,0])
    call('export_asset', path='live.obj'); assert (project/'live.obj').is_file()
    call('import_asset', path='live.obj')
    call('save', path='live.blend'); assert (project/'live.blend').is_file()
    call('open', path='live.blend')
    result = call('render', engine='cycles', width=64, height=64, samples=1)
    assert result['result']['hardware_render'] is (device == 'metal')
    with control.open_artifact(result['id'], 0) as stream:
        assert stream.read(8)==b'\x89PNG\r\n\x1a\n'
    print('PASS real Blender scene/object/OBJ export/import/blend save/open/'+device+' render/hashed PNG', flush=True)
finally:
    control.close(); log.close()
    print('Preserved disposable evidence: '+str(root), flush=True)
