"""Mock Blender / Mock Metal only. Never starts Blender or a real GPU workflow."""
import ast
import copy
import importlib.util
import json
import math
import os
from pathlib import Path
import plistlib
import signal
import struct
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import uuid

from bridge_platform.macos import private_dir, MacOSPlatform
from bridge_platform.macos_blender import BlenderControl, discover, executable, publish, filtered
from bridge_platform.macos_godot import Unsupported
from native_blender_metal import configure, render
from test_macos_godot import Child, image, mcp
from bridge import Bridge, Server, make_handler


@unittest.skipUnless(os.name == 'posix', 'POSIX fixtures')
class BlenderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.data = private_dir(self.root / 'bridge')
        self.project = self.root / '项目 with spaces';self.project.mkdir()
        self.app = self.root / 'apps/Blender.app'
        (self.app / 'Contents/MacOS').mkdir(parents=True)
        (self.app / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': 'org.blenderfoundation.blender', 'CFBundleExecutable': 'Blender'}))
        self.exe = self.app / 'Contents/MacOS/Blender'
        self.exe.write_bytes(struct.pack('<IIIIIIII', 0xfeedfacf, 0x0100000c, 0, 2, 0, 0, 0, 0));self.exe.chmod(0o700)
        self.config = {'data_dir': str(self.data), 'native': {'blender_executable': str(self.app), 'blender_device': 'metal', 'projects': [
            {'id': 'fixture', 'root': str(self.project), 'enabled': True, 'allow_scripts': True}]}}
        self.run = Mock(return_value=subprocess.CompletedProcess([], 0, 'Blender 4.5.3\n', ''))
        self.enterContext(patch('bridge_platform.macos_blender.platform.system', return_value='Darwin'))
        self.enterContext(patch('bridge_platform.macos_blender.platform.machine', return_value='arm64'))
        self.kill = self.enterContext(patch('bridge_platform.macos_blender.os.killpg'))
        self.response = {'ok': True, 'result': {'object_count': 0}}
        self.paused = False
        self.workers = []
        self.children = []
        def spawn(command, **kwargs):
            child = Child(None);self.children.append(child)
            settings = json.loads(Path(command[-1]).read_text())
            spool, outputs = Path(settings['spool']), Path(settings['outputs'])
            def worker():
                while child.poll() is None:
                    for request in spool.glob('*.request.json'):
                        if self.paused:continue
                        ident = request.name.split('.')[0]
                        value = copy.deepcopy(self.response)
                        args = json.loads(request.read_text())
                        if args['command'] in ('render', 'viewport_capture'):
                            name = ident+'.png';(outputs/name).write_bytes(image())
                            value = {'ok': True, 'result': {'artifact': name}}
                        if args['command'] in ('save', 'export_asset'):
                            name = ident+Path(args['arguments']['path']).suffix
                            (outputs/name).write_bytes(b'synthetic asset')
                            value = {'ok': True, 'result': {'artifact': name, '_exports': [{'artifact':name,'path':args['arguments']['path']}]}}
                        temp = spool/(ident+'.tmp');temp.write_text(json.dumps(value));temp.rename(spool/(ident+'.response.json'))
                        request.unlink()
                    time.sleep(.005)
            thread = threading.Thread(target=worker, daemon=True);self.workers.append(thread);thread.start()
            return child
        self.spawn = Mock(side_effect=spawn)
        self.control = BlenderControl(self.config, self.run, self.spawn)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.control.close()
        for child in self.children:child.code=-15
        for thread in self.workers:thread.join(1)

    def body(self, operation='scene_info', **args):
        return {'engine':'blender','project_id':'fixture','operation':operation,'arguments':args,'request_id':uuid.uuid4().hex}

    def finish(self, body):
        job = self.control.queue(body)
        for _ in range(300):
            job = self.control.job_status(job['id'])
            if job['status'] in ('succeeded','failed','cancelled'):return job
            time.sleep(.01)
        self.fail('Mock task did not finish')

    def started(self, ident):
        for _ in range(100):
            if ident in self.control.active:return
            time.sleep(.01)
        self.fail('Mock process did not start')

    def test_discover_app_and_explicit_multiple_versions_without_execution(self):
        self.assertEqual(discover([self.app.parent]), [str(self.exe)])
        self.assertEqual(discover([], str(self.exe)), [str(self.exe)])
        self.assertEqual(discover([], str(self.app)), [str(self.exe)])
        self.run.assert_not_called();self.spawn.assert_not_called()

    def test_architecture_permissions_symlink_and_version(self):
        self.assertEqual(executable(self.app), self.exe)
        self.exe.chmod(0o600)
        with self.assertRaises(ValueError):executable(self.app)
        self.exe.chmod(0o777)
        with self.assertRaises(ValueError):executable(self.exe)
        self.exe.chmod(0o700)
        original = self.exe.read_bytes();self.exe.write_bytes(original[:4]+struct.pack('<I',0x01000007)+original[8:])
        with self.assertRaises(Unsupported):executable(self.app)
        self.exe.write_bytes(original)
        link=self.root/'Blender.app';link.symlink_to(self.app)
        with self.assertRaises(ValueError):executable(link)
        for version, expected in [('Blender 3.6.9','unsupported'),('Blender 4.2.0','available'),('Blender 5.2.1','available'),('Blender 5.2.3 LTS','available'),('Blender 5.2.3 LTS Beta','unsupported'),('Blender 5.2.0 Alpha','unsupported')]:
            self.run.return_value.stdout=version+'\n'
            self.assertEqual(self.control.status()['state'],expected)

    def test_probe_failure_is_not_available_and_cpu_configuration(self):
        self.assertEqual(self.control.status(False)['state'],'installed')
        self.run.side_effect=subprocess.TimeoutExpired(['mock'],5)
        self.assertEqual(self.control.status()['state'],'error')
        self.spawn.assert_not_called()

    def test_project_consent_and_nested_script_authorization(self):
        (self.project/'code.py').write_text('# synthetic')
        for operation,args in [('execute_code',{'code':'result=1'}),('run_script',{'path':'code.py'}),('batch',{'steps':[{'operation':'execute_code','arguments':{'code':'result=1'}}]})]:
            body=self.body(operation,**args)
            for authorized in (None,False,1,'true'):
                body['authorized']=authorized
                with self.assertRaises(ValueError):self.control.queue(body)
            body['authorized']=True
            self.config['native']['projects'][0]['allow_scripts']=False
            with self.assertRaises(ValueError):self.control.queue(body)
            self.config['native']['projects'][0]['allow_scripts']=True
            self.assertEqual(self.finish(body)['status'],'succeeded')
        self.config['native']['projects'][0]['enabled']=False
        with self.assertRaises(ValueError):self.control.queue(self.body())

    def test_paths_traversal_links_identity_and_missing_files(self):
        for path in ('../x.blend','/x.blend','folder/../x.blend','x\\x.blend','missing.blend'):
            with self.assertRaises((ValueError,OSError)):self.control.queue(self.body('open',path=path))
        (self.project/'link.blend').symlink_to(self.exe)
        with self.assertRaises(ValueError):self.control.queue(self.body('open',path='link.blend'))
        (self.project/'link.blend').unlink()
        old=self.root/'old';self.project.rename(old);self.project.mkdir()
        with self.assertRaises(ValueError):self.control.queue(self.body())
        self.spawn.assert_not_called()

    def test_launch_minimal_environment_persistent_session_and_process_isolation(self):
        with patch.dict(os.environ,{'SYNTHETIC_TOKEN':'never inherit','PYTHONPATH':'never inherit'}):
            first=self.finish(self.body())
            second=self.finish(self.body('create_primitive',kind='cube'))
        self.assertEqual(first['status'],'succeeded');self.assertEqual(second['status'],'succeeded')
        self.assertNotEqual(first['id'],second['id']);self.assertEqual(self.spawn.call_count,1)
        argv=self.spawn.call_args.args[0];kw=self.spawn.call_args.kwargs
        self.assertIn('--factory-startup',argv);self.assertIn('--disable-autoexec',argv)
        self.assertFalse(kw['shell']);self.assertTrue(kw['start_new_session']);self.assertEqual(kw['cwd'],self.project)
        self.assertNotIn('SYNTHETIC_TOKEN',kw['env']);self.assertNotIn('PYTHONPATH',kw['env'])
        self.assertTrue(Path(kw['env']['BLENDER_USER_CONFIG']).is_relative_to(self.data))
        self.assertEqual(self.control.status()['state'],'running');self.kill.assert_not_called()
        self.assertEqual(self.control.blender_tasks()['jobs'][0]['id'],second['id'])

    def test_idempotency_and_request_conflict(self):
        body=self.body();first=self.finish(body)
        self.assertEqual(self.control.queue(body)['id'],first['id'])
        body['operation']='create_primitive';body['arguments']={'kind':'cube'}
        with self.assertRaises(ValueError):self.control.queue(body)

    def test_exports_no_overwrite_sha256_and_download_tamper(self):
        import hashlib
        for extension in ('glb','obj','blend'):
            operation='save' if extension=='blend' else 'export_asset'
            body=self.body(operation,path='new.'+extension)
            job=self.finish(body);self.assertEqual(job['status'],'succeeded')
            self.assertEqual(self.control.queue(body)['id'],job['id'])
            item=job['artifacts'][0];data=(self.project/('new.'+extension)).read_bytes()
            self.assertEqual(item['sha256'],hashlib.sha256(data).hexdigest())
            with self.control.open_artifact(job['id'],0) as stream:self.assertEqual(stream.read(),data)
            with self.assertRaises(ValueError):self.control.queue(self.body(operation,path='new.'+extension))
            artifact=self.control.root/job['id']/item['filename'];artifact.write_bytes(b'tampered')
            with self.assertRaises(ValueError):self.control.open_artifact(job['id'],0)
        with self.assertRaises(Unsupported):self.control.queue(self.body('save',path='replace.blend',replace=True))

    def test_exclusive_publish_race_and_symlink_parent(self):
        (self.project/'x.obj').write_bytes(b'original')
        with self.assertRaises(FileExistsError):publish(self.project,'x.obj',b'new')
        self.assertEqual((self.project/'x.obj').read_bytes(),b'original')
        (self.project/'link').symlink_to(self.root)
        with self.assertRaises(OSError):publish(self.project,'link/x.obj',b'new')
        self.assertFalse((self.root/'x.obj').exists())

    def test_glb_obj_import_and_unsupported_formats_vrm(self):
        for suffix in ('glb','obj','vrm'):
            (self.project/('asset.'+suffix)).write_bytes(b'synthetic')
            self.assertEqual(self.finish(self.body('import_asset',path='asset.'+suffix))['status'],'succeeded')
        (self.project/'asset.fbx').write_bytes(b'synthetic')
        with self.assertRaises(Unsupported):self.control.queue(self.body('import_asset',path='asset.fbx'))
        self.response={'ok':False,'code':'unsupported'}
        job=self.finish(self.body('import_asset',path='asset.vrm'))
        self.assertEqual(job['status'],'failed');self.assertEqual(job['error_code'],'unsupported')

    def test_render_viewport_artifacts_and_cycles_arguments(self):
        for operation in ('render','viewport_capture'):
            job=self.finish(self.body(operation,width=64,height=64));self.assertEqual(job['status'],'succeeded')
            with self.control.open_artifact(job['id'],0) as stream:self.assertEqual(stream.read(),image())
        for args in ({'engine':'eevee'},{'samples':513},{'width':63}):
            with self.assertRaises(ValueError):self.control.queue(self.body('render',**args))

    def test_cancel_only_owned_running_task_and_recovery(self):
        self.paused=True;job=self.control.queue(self.body());self.started(job['id'])
        with self.assertRaises(ValueError):self.control.cancel('f'*32)
        self.assertEqual(self.control.cancel(job['id'])['status'],'cancelled')
        self.kill.assert_called_with(self.children[0].pid,signal.SIGTERM)
        self.paused=False
        self.assertEqual(self.finish(self.body())['status'],'succeeded')

    def test_timeout_and_process_exit(self):
        self.paused=True
        with patch('bridge_platform.macos_blender.time.monotonic',side_effect=[0,999]):
            job=self.finish(self.body())
        self.assertEqual(job['status'],'failed');self.assertTrue(job['timed_out'])
        self.paused=True;job=self.control.queue(self.body());self.started(job['id']);self.children[-1].code=7
        for _ in range(100):
            if self.control.job_status(job['id'])['status']=='failed':break
            time.sleep(.01)
        self.assertEqual(self.control.job_status(job['id'])['status'],'failed')

    def test_restart_unknown_does_not_adopt_pid(self):
        ident='a'*32;self.control.save(dict(id=ident,engine='blender',status='running',pid=123456,project_id='fixture',created_at=0,artifacts=[],logs=[]))
        restored=BlenderControl(self.config,self.run,self.spawn);self.addCleanup(restored.close)
        job=restored.job_status(ident);self.assertEqual(job['status'],'failed');self.assertIn('unknown',job['error'])
        restored.cancel(ident);self.kill.assert_not_called();self.spawn.assert_not_called()

    def test_progress_and_safe_logs(self):
        self.paused=True;job=self.control.queue(self.body('batch',steps=[{'operation':'scene_info'}]));self.started(job['id'])
        spool=self.control.sessions['fixture']['spool'];(spool/(job['id']+'.progress.json')).write_text(json.dumps({'completed':0,'total':1}))
        for _ in range(100):
            if 'progress' in self.control.job_status(job['id']):break
            time.sleep(.01)
        self.assertEqual(self.control.job_status(job['id'])['progress']['total'],1)
        self.assertNotIn(str(self.root),json.dumps(self.control.logs(job['id'])))
        self.control.cancel(job['id'])
        value=filtered({'token':'synthetic','messages':'token=synthetic /Users/fixture/private 192.0.2.1'})
        self.assertNotIn('synthetic',json.dumps(value));self.assertNotIn('/Users/',json.dumps(value))

    def test_mcp_http_compatibility_and_isolation(self):
        from bridge_platform.macos_native import NativeControl
        native=NativeControl(self.config);native.blender.close();native.blender=self.control
        self.addCleanup(native.close)
        config={**self.config,'token':'c'*64,'allowed_peers':['127.0.0.1'],'workflow_dirs':[]}
        adapter=MacOSPlatform(self.root)
        with patch.object(adapter,'native_controls',return_value=(native,None)),patch.object(adapter,'authorize_peer',return_value=True):
            bridge=Bridge(config,platform_adapter=adapter)
            server=Server(('127.0.0.1',0),make_handler(bridge));threading.Thread(target=server.serve_forever,daemon=True).start()
            self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
            file=self.root/'client.fixture.json';file.write_text(json.dumps({'url':'http://127.0.0.1:'+str(server.server_port),'token':config['token']}))
            client=mcp.Client(file)
            job=client.call('blender_operation',{'project_id':'fixture','operation':'render','arguments':{'width':64,'height':64}})
            for _ in range(100):
                value=client.call('native_job_status',{'job_id':job['id']})
                if value['status'] in ('succeeded','failed'):break
                time.sleep(.01)
            self.assertEqual(value['status'],'succeeded')
            self.assertFalse(client.call('wait_native_job',{'job_id':job['id']})['waiting'])
            self.assertEqual(client.call('blender_tasks',{})['jobs'][0]['id'],job['id'])
            self.assertEqual(client.call('cancel_blender_task',{'job_id':job['id']})['status'],'succeeded')
            receipt=client.call('fetch_native_artifact',{'job_id':job['id'],'index':0,'destination_dir':str(self.root/'download')})
            self.assertEqual(Path(receipt['path']).read_bytes(),image())
            with self.assertRaises(ValueError):client.call('blender_operation',{'project_id':'fixture','operation':'execute_code','arguments':{'code':'result=1'}})
            self.assertEqual(client.call('list_workflows',{}),{'workflows':[]})
            self.assertFalse(native.godot.active)

    def test_mcp_authorization_forwarded_without_changing_windows_arguments(self):
        client=object.__new__(mcp.Client);client.request=Mock(return_value={})
        client.call('blender_operation',{'project_id':'fixture','operation':'execute_code','arguments':{'code':'result=1'},'authorized':True})
        body=client.request.call_args.args[1];self.assertTrue(body['authorized']);self.assertEqual(body['arguments'],{'code':'result=1'})
        tool=next(t for t in mcp.BLENDER_TOOLS if t['name']=='blender_operation')
        self.assertEqual(tool['inputSchema']['properties']['authorized'],{'type':'boolean'})

    def test_installer_and_isolated_import(self):
        spec=importlib.util.spec_from_file_location('blender_installer',Path(__file__).resolve().parents[2]/'scripts/install-local-gpu-macos.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        for name in ('native_blender.py','native_blender_validation.py','native_blender_metal.py','native_blender_vrm.py'):
            self.assertIn(name,module.FILES)
        self.spawn.assert_not_called()


    def test_engine_failure_and_cleanup_are_independent(self):
        from bridge_platform.macos_native import NativeControl
        with patch('bridge_platform.macos_native.BlenderControl',side_effect=ValueError('synthetic store failure')):
            native=NativeControl(self.config)
        self.assertEqual(native.status()['engines'][1]['state'],'error')
        self.assertEqual(native.status()['engines'][0]['state'],'unsupported')
        native.godot.close=Mock(side_effect=OSError('synthetic close failure'))
        native.blender.close=Mock()
        with self.assertRaises(OSError):native.close()
        native.blender.close.assert_called_once()

    def test_installed_bundle_imports_without_windows_native_module(self):
        import shutil
        spec=importlib.util.spec_from_file_location('fixture_installer',Path(__file__).resolve().parents[2]/'scripts/install-local-gpu-macos.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        app=self.root/'installed';app.mkdir();(app/'bridge_platform').mkdir()
        for name in module.FILES:shutil.copyfile(module.SOURCE/name,app/name)
        for source in (module.SOURCE/'bridge_platform').glob('*.py'):shutil.copyfile(source,app/'bridge_platform'/source.name)
        import sys
        code="import sys;sys.path.insert(0,sys.argv[1]);from bridge_platform.macos_native import NativeControl;assert 'native_process' not in sys.modules;assert 'native_control' not in sys.modules"
        # Only the host Python import probe executes, never the synthetic Blender binary.
        result=subprocess.run([sys.executable,'-I','-c',code,str(app)],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.spawn.assert_not_called()

    def test_new_blender_routes_require_auth_before_dispatch(self):
        import http.client
        config={**self.config,'token':'c'*64,'allowed_peers':['127.0.0.1'],'workflow_dirs':[]}
        native=Mock();adapter=MacOSPlatform(self.root)
        with patch.object(adapter,'native_controls',return_value=(native,None)):
            bridge=Bridge(config,platform_adapter=adapter)
        security=Mock();security.revoked=threading.Event();security.authorize.return_value=False
        server=Server(('127.0.0.1',0),make_handler(bridge,security));threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        for route in ('blender-tasks','blender-cancel','jobs'):
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port)
            connection.request('POST','/v1/native/'+route,body='{}',headers={'Authorization':'Bearer wrong','Content-Type':'application/json'})
            response=connection.getresponse();self.assertEqual(response.status,403);response.read();connection.close()
        native.queue.assert_not_called();native.blender_tasks.assert_not_called();native.cancel_blender.assert_not_called()


class MockMetalTests(unittest.TestCase):
    def bpy(self, devices):
        prefs=NS(compute_device_type='NONE',devices=[NS(type=d,use=False) for d in devices],get_devices=Mock())
        bpy=NS(context=NS(scene=NS(cycles=NS(device='CPU')),preferences=NS(addons={'cycles':NS(preferences=prefs)})),ops=NS(render=NS(render=Mock())))
        return bpy,prefs

    def test_metal_enumeration_only_selected_type_enabled(self):
        bpy,prefs=self.bpy(['CPU','METAL','METAL']);value=configure(bpy)
        self.assertEqual(value['backend'],'METAL');self.assertEqual(value['metal_devices'],2)
        self.assertEqual([d.use for d in prefs.devices],[False,True,True]);self.assertEqual(bpy.context.scene.cycles.device,'GPU')

    def test_no_metal_backend_no_device_and_probe_error_cpu_fallback(self):
        for mode in ('missing','none','exception'):
            bpy,prefs=self.bpy(['CPU'])
            if mode=='missing':bpy.context.preferences.addons={}
            if mode=='exception':prefs.get_devices.side_effect=RuntimeError('mock device failure')
            value=configure(bpy);self.assertEqual(value['backend'],'CPU');self.assertTrue(value['cpu_fallback'])

    def test_explicit_cpu_never_enumerates(self):
        bpy,prefs=self.bpy(['METAL']);value=configure(bpy,'cpu')
        self.assertFalse(value['cpu_fallback']);prefs.get_devices.assert_not_called()

    def test_metal_render_failure_retries_cpu_and_cpu_errors_surface(self):
        bpy,prefs=self.bpy(['METAL']);value=configure(bpy)
        bpy.ops.render.render.side_effect=[RuntimeError('mock metal error'),None]
        render(bpy,value);self.assertEqual(value['fallback_reason'],'metal_render_failed');self.assertEqual(value['backend'],'CPU')
        bpy.ops.render.render.side_effect=RuntimeError('mock CPU error')
        with self.assertRaises(RuntimeError):render(bpy,value)


class SharedWorkerTests(unittest.TestCase):
    """Run shared worker functions with synthetic bpy; no Blender import/process."""
    def setUp(self):
        import pathlib, contextlib, shutil, sys
        from native_blender_output import Capture, result, diagnostic
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.out=self.root/'outputs';self.out.mkdir()
        self.obj=NS(name='Cube',type='MESH',location=[0,0,0],rotation_euler=[0,0,0],scale=[1,1,1],data=NS(materials=[]))
        self.inputs={key:NS(default_value=None) for key in ('Base Color','Roughness','Metallic')}
        self.material=NS(node_tree=NS(nodes=NS(get=lambda _:NS(inputs=self.inputs))))
        self.scene=NS(objects=[self.obj],camera=None,cycles=NS(device='CPU'),render=NS(image_settings=NS(file_format='PNG')))
        self.prefs=NS(compute_device_type='NONE',get_devices=Mock(),devices=[NS(type='METAL',use=False)])
        self.bpy=NS(context=NS(scene=self.scene,object=self.obj,preferences=NS(addons={'cycles':NS(preferences=self.prefs)})),
            data=NS(objects=NS(get=lambda _:self.obj,remove=Mock()),materials=NS(new=Mock(return_value=self.material))),
            ops=NS(mesh=Mock(),wm=Mock(),render=Mock(),import_scene=Mock(),export_scene=Mock()))
        source=ast.parse(Path(__file__).with_name('native_blender.py').read_text())
        functions=ast.Module(body=[n for n in source.body if isinstance(n,ast.FunctionDef) and n.name in ('execute','vec','local_file','object_info')],type_ignores=[])
        self.scope=dict(bpy=self.bpy,json=json,pathlib=pathlib,math=math,os=os,sys=sys,contextlib=contextlib,shutil=shutil,
            settings={'macos':True,'device':'metal'},root=self.root,out=self.out,spool=self.root,
            Capture=Capture,safe_result=result,diagnostic=diagnostic,vrm_capabilities=lambda:{'vrm_import':False,'vrm_export':False},vrm_operate=Mock())
        exec(compile(functions,'mock_worker','exec'),self.scope)

    def execute(self, op, args=None):
        return self.scope['execute'](op,args or {},'a'*32)

    def test_scene_geometry_transform_material_delete(self):
        self.assertEqual(self.execute('scene_info')['object_count'],1)
        self.execute('create_primitive',{'kind':'cube','name':'Box','location':[1,2,3],'rotation':[0,0,1],'scale':[2,2,2]})
        self.bpy.ops.mesh.primitive_cube_add.assert_called_once()
        value=self.execute('set_transform',{'name':'Box','location':[4,5,6]});self.assertEqual(value['location'],[4,5,6])
        self.execute('set_material',{'name':'Box','color':[1,0,0,1],'metallic':.2,'roughness':.5})
        self.assertEqual(self.inputs['Base Color'].default_value,[1,0,0,1]);self.assertEqual(self.obj.data.materials,[self.material])
        self.execute('delete_object',{'name':'Box'});self.bpy.data.objects.remove.assert_called_once_with(self.obj,do_unlink=True)

    def test_glb_obj_vrm_and_save_staging(self):
        self.execute('import_asset',{'path':'asset.glb'});self.bpy.ops.import_scene.gltf.assert_called_once()
        self.execute('import_asset',{'path':'asset.obj'});self.bpy.ops.wm.obj_import.assert_called_once()
        value=self.execute('export_asset',{'path':'new.glb'});self.assertEqual(value['exported'],'new.glb')
        self.assertEqual(self.bpy.ops.export_scene.gltf.call_args.kwargs['filepath'],str(self.out/('a'*32+'.glb')))
        self.execute('export_asset',{'path':'new.obj'});self.assertFalse(self.bpy.ops.wm.obj_export.call_args.kwargs['export_materials'])
        value=self.execute('save',{'path':'new.blend'});self.assertEqual(value['_exports'][0]['path'],'new.blend')
        for op in ('import_asset','export_asset'):
            with self.assertRaises(NotImplementedError):self.execute(op,{'path':'asset.vrm'})
        self.scope['vrm_operate'].assert_not_called()
        self.scope['vrm_capabilities']=lambda:{'vrm_import':True,'vrm_export':True}
        self.execute('import_asset',{'path':'asset.vrm'});self.execute('export_asset',{'path':'asset.vrm'})
        self.assertEqual(self.scope['vrm_operate'].call_count,2)

    def test_cycles_parameters_metal_cpu_and_windows_optix_unchanged(self):
        value=self.execute('render',{'width':640,'height':480,'samples':48})
        self.assertEqual(value['render_device']['backend'],'METAL');self.assertTrue(value['hardware_render'])
        self.assertEqual(self.scene.cycles.samples,48);self.assertEqual(self.scene.render.engine,'CYCLES')
        self.assertEqual(self.scene.render.resolution_x,640);self.assertFalse(self.scene.render.use_compositing)
        self.scope['settings']['device']='cpu';value=self.execute('render');self.assertFalse(value['hardware_render'])
        self.scope['settings']={};self.prefs.devices=[NS(type='OPTIX',use=False)]
        value=self.execute('render');self.assertTrue(value['hardware_render']);self.assertEqual(self.prefs.compute_device_type,'OPTIX')

    def test_authorized_synthetic_script_lights_batch_and_failure(self):
        self.bpy.data.lights=NS(new=Mock(return_value=NS(energy=0)))
        value=self.execute('execute_code',{'code':"light=bpy.data.lights.new('Key',type='AREA');light.energy=500;result={'energy':light.energy}"})
        self.assertEqual(value['result']['energy'],500)
        value=self.execute('batch',{'steps':[{'operation':'create_primitive','arguments':{'kind':'cube'}},{'operation':'execute_code','arguments':{'code':"raise ValueError('synthetic')"}},{'operation':'scene_info'}]})
        self.assertEqual(value['completed'],2);self.assertTrue(value['partial_success'])

    def test_camera_and_open_disable_embedded_scripts(self):
        class Vector:
            def __init__(self,value):self.value=value
            def __sub__(self,value):return self
            def to_track_quat(self,*args):return self
            def to_euler(self):return [0,0,0]
        self.scope['Vector']=Vector
        self.scene.camera=NS(location=Vector([0,0,0]),data=NS(lens=0))
        self.execute('set_camera',{'location':[1,2,3],'target':[0,0,0],'lens':70})
        self.assertEqual(self.scene.camera.data.lens,70)
        self.execute('open',{'path':'scene.blend'})
        self.assertFalse(self.bpy.ops.wm.open_mainfile.call_args.kwargs['use_scripts'])
