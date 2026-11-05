"""Mock Unity/Pipeline only: never executes Unity, project C#, or GPU workloads."""
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import plistlib
import struct
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

from bridge_platform.macos import private_dir, MacOSPlatform
from bridge_platform.macos_unity import UnityControl, discover, editor, EXTENSION, FIELDS, asset_path
from bridge_platform.macos_godot import Unsupported
from test_macos_godot import Child, image, mcp
from bridge import Bridge, Server, make_handler


class UnityTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name).resolve();self.data=private_dir(self.root/'data')
        self.project=self.root/'项目 with spaces'
        for name in ('Assets/Editor','Packages','ProjectSettings'):(self.project/name).mkdir(parents=True)
        (self.project/'Packages/manifest.json').write_text('{"dependencies":{}}')
        (self.project/'ProjectSettings/ProjectVersion.txt').write_text('m_EditorVersion: 6000.0.60f1\n')
        (self.project/'Assets/Editor/OrcaUnityBridge.cs').write_bytes(EXTENSION.read_bytes())
        self.app=self.root/'Applications/Unity/Hub/Editor/6000.0.60f1/Unity.app'
        (self.app/'Contents/MacOS').mkdir(parents=True)
        self.meta={'CFBundleIdentifier':'com.unity3d.UnityEditor','CFBundleExecutable':'Unity','CFBundleShortVersionString':'6000.0.60f1','LSMinimumSystemVersion':'11.0'}
        self.plist=self.app/'Contents/Info.plist';self.plist.write_bytes(plistlib.dumps(self.meta))
        self.exe=self.app/'Contents/MacOS/Unity';self.exe.write_bytes(struct.pack('<IIIIIIII',0xfeedfacf,0x0100000c,0,2,0,0,0,0));self.exe.chmod(0o700)
        self.config={'data_dir':str(self.data),'native':{'unity_executable':str(self.app),'projects':[
            {'id':'fixture','root':str(self.project),'enabled':True,'allow_scripts':True}]}}
        self.enterContext(patch('bridge_platform.macos_unity.platform.system',return_value='Darwin'))
        self.enterContext(patch('bridge_platform.macos_unity.platform.machine',return_value='arm64'))
        self.enterContext(patch('bridge_platform.macos_unity.platform.mac_ver',return_value=('14.0','','')))
        self.kill=self.enterContext(patch('bridge_platform.macos_godot.os.killpg'))
        self.children=[];self.workers=[];self.paused=False;self.healthy=True;self.metal=True;self.compatible=True;self.result={'message':'ok'};self.response_ok=True
        def spawn(argv,**kwargs):
            child=Child(None);self.children.append(child)
            env=kwargs['env'];folder=Path(env['ORCA_UNITY_SPOOL']);secret=env['ORCA_UNITY_SESSION']
            def publish(name,value):
                tmp=folder/(uuid.uuid4().hex+'.tmp');tmp.write_text(json.dumps(value));tmp.replace(folder/name)
            def worker():
                while child.poll() is None:
                    if self.healthy:
                        publish('health.json',dict(session=secret,project=str(self.project),version='6000.0.60f1',pid=child.pid,timestamp=time.time(),metal=self.metal,compatible=self.compatible))
                    for path in folder.glob('*.request.json'):
                        if self.paused:continue
                        request=json.loads(path.read_text());ident=request['id_request'];op=request['operation']
                        if op=='gameview.capture':(folder/(ident+'.png')).write_bytes(image())
                        if op=='scene.save':(self.project/('Assets/OrcaBridge-'+ident)/'scene.unity').write_bytes(b'%YAML 1.1\n# synthetic scene')
                        publish(ident+'.response.json',dict(session=secret,id_request=ident,ok=self.response_ok,result=copy.deepcopy(self.result)))
                        path.unlink()
                    time.sleep(.01)
            t=threading.Thread(target=worker,daemon=True);self.workers.append(t);t.start()
            return child
        self.spawn=Mock(side_effect=spawn);self.run=Mock(side_effect=AssertionError('Discovery must not execute'))
        self.control=UnityControl(self.config,self.run,self.spawn);self.addCleanup(self.cleanup)

    def cleanup(self):
        self.control.close()
        for child in self.children:child.code=-15
        for worker in self.workers:worker.join(1)

    def wait(self, predicate):
        for _ in range(400):
            if predicate():return
            time.sleep(.01)
        self.fail('Mock deadline exceeded')

    def start(self,timeout=300):
        job=self.control.start('fixture',True,timeout)
        self.wait(lambda:self.control.job_status(job['id']).get('ready') or self.control.job_status(job['id'])['status']=='failed')
        return self.control.job_status(job['id'])

    def finish(self,name,arguments=None):
        job=self.control.call('fixture',name,arguments,True)
        self.wait(lambda:self.control.job_status(job['id'])['status'] in ('succeeded','failed','cancelled'))
        return self.control.job_status(job['id'])

    def test_hub_and_editor_discovery_no_execution(self):
        hub=self.root/'Applications/Unity Hub.app/Contents';hub.mkdir(parents=True)
        (hub/'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':'com.unity3d.unityhub'}))
        value=discover([self.root/'Applications'])
        self.assertEqual(value['hubs'],[str(hub.parent)]);self.assertEqual(value['editors'][0]['version'],'6000.0.60f1')
        self.assertEqual(discover([],str(self.exe))['editors'][0]['path'],str(self.exe))
        self.run.assert_not_called();self.spawn.assert_not_called()

    def test_arm64_permissions_and_symlinks(self):
        original=self.exe.read_bytes()
        self.exe.write_bytes(original[:4]+struct.pack('<I',0x01000007)+original[8:])
        with self.assertRaises(Unsupported):editor(self.app)
        self.exe.write_bytes(original)
        for mode in (0o600,0o777):
            self.exe.chmod(mode)
            with self.assertRaises(ValueError):editor(self.app)
        self.exe.chmod(0o700)
        link=self.root/'link.app';link.symlink_to(self.app)
        with self.assertRaises(ValueError):editor(link)

    def test_editor_version_os_boundary(self):
        for version in ('2022.3.60f1','6000.1.0f1','6000.0.1b1'):
            self.plist.write_bytes(plistlib.dumps({**self.meta,'CFBundleShortVersionString':version}))
            self.assertEqual(self.control.status()['state'],'unsupported')
        self.plist.write_bytes(plistlib.dumps(self.meta))
        with patch('bridge_platform.macos_unity.platform.mac_ver',return_value=('10.15','','')):
            self.assertEqual(self.control.status()['state'],'unsupported')

    def test_project_version_exact_no_upgrade(self):
        for text in ('','m_EditorVersion: 6000.0.59f1','m_EditorVersion: 6000.1.0f1'):
            (self.project/'ProjectSettings/ProjectVersion.txt').write_text(text)
            with self.assertRaises(Unsupported):self.control.start('fixture',True)
        self.spawn.assert_not_called()

    def test_launch_arrays_metal_minimal_environment(self):
        with patch.dict(os.environ,{'TOKEN':'synthetic','DYLD_INSERT_LIBRARIES':'synthetic'}):job=self.start()
        argv,kw=self.spawn.call_args.args[0],self.spawn.call_args.kwargs
        self.assertIsInstance(argv,list);self.assertIn('-force-metal',argv)
        self.assertFalse(any('d3d' in x.lower() for x in argv));self.assertFalse(kw['shell']);self.assertTrue(kw['start_new_session'])
        self.assertNotIn('TOKEN',kw['env']);self.assertNotIn('DYLD_INSERT_LIBRARIES',kw['env']);self.assertTrue(job['ready'])
        self.assertNotIn(kw['env']['ORCA_UNITY_SESSION'],json.dumps(job))

    def test_start_authorization_strict(self):
        for authorized in (None,False,1,'true'):
            with self.assertRaises(ValueError):self.control.start('fixture',authorized)
        self.config['native']['projects'][0]['allow_scripts']=False
        with self.assertRaises(ValueError):self.control.start('fixture',True)
        self.spawn.assert_not_called()

    def test_call_and_tools_authorization_independently_rechecked(self):
        self.start()
        for authorized in (None,False,1):
            with self.assertRaises(ValueError):self.control.call('fixture','play.start',{},authorized)
            with self.assertRaises(ValueError):self.control.tools('fixture',authorized)
        self.config['native']['projects'][0]['allow_scripts']=False
        with self.assertRaises(ValueError):self.control.call('fixture','play.start',{},True)

    def test_extension_missing_or_modified_rejected(self):
        (self.project/'Assets/Editor/OrcaUnityBridge.cs').write_text('// altered')
        with self.assertRaises(Unsupported):self.control.start('fixture',True)
        self.spawn.assert_not_called()

    def test_status_requires_owned_api_health(self):
        self.assertEqual(self.control.status()['state'],'available')
        self.healthy=False
        job=self.control.start('fixture',True)
        self.wait(lambda:self.control.session is not None)
        self.assertEqual(self.control.status()['state'],'installed')
        self.assertFalse(self.control.status()['checks']['editor_api'])
        self.control.cancel(job['id'])

    def test_metal_unavailable_fails_without_settings_changes(self):
        self.metal=False;job=self.start()
        self.assertEqual(job['status'],'failed');self.assertEqual(job['error_code'],'unsupported_graphics')
        self.assertTrue(self.kill.called)

    def test_incompatible_graphics_pipeline_fails(self):
        self.compatible=False;job=self.start()
        self.assertEqual(job['error_code'],'unsupported_graphics')

    def test_catalog_and_gameobject_operations(self):
        self.start();names={x['name'] for x in self.control.tools('fixture',True)['tools']};self.assertEqual(names,set(FIELDS))
        for name,args in [('editor.status',{}),('scene.get',{}),('hierarchy.get',{}),('gameobject.create',{'name':'Cube'}),('gameobject.get',{'id':12}),('gameobject.delete',{'id':12}),('transform.set',{'id':12,'position':[1,2,3]}),('inspector.get',{'id':12}),('component.add',{'id':12,'component':'Light'}),('component.set',{'id':12,'component':'Light','property':'intensity','value':5})]:
            self.assertEqual(self.finish(name,args)['status'],'succeeded',name)

    def test_arbitrary_eval_shell_components_and_d3d_rejected(self):
        self.start()
        for name,args in [('exec',{}),('script.eval',{}),('package.add',{}),('component.add',{'id':1,'component':'MyCode'}),('component.set',{'id':1,'component':'Camera','property':'script','value':1}),('transform.set',{'id':1,'position':[float('nan'),0,0]}),('editor.status',{'args':'-force-d3d11'})]:
            with self.assertRaises(ValueError):self.control.call('fixture',name,args,True)

    def test_save_scene_new_only_and_play(self):
        self.start();path='Assets/new.unity'
        self.assertEqual(self.finish('scene.save',{'path':path})['status'],'succeeded')
        self.assertTrue((self.project/path).read_bytes().startswith(b'%YAML'))
        with self.assertRaises(ValueError):self.control.call('fixture','scene.save',{'path':path},True)
        for name in ('play.start','play.stop'):self.assertEqual(self.finish(name)['status'],'succeeded')

    def test_capture_hash_download_recheck(self):
        self.start();job=self.finish('gameview.capture');self.assertEqual(job['status'],'succeeded')
        artifact=job['artifacts'][0];self.assertEqual(artifact['sha256'],hashlib.sha256(image()).hexdigest())
        with self.control.open_artifact(job['id'],0) as stream:self.assertEqual(stream.read(),image())
        (self.control.root/job['id']/'capture.png').write_bytes(b'tampered')
        with self.assertRaises(ValueError):self.control.open_artifact(job['id'],0)

    def test_console_redaction(self):
        self.start();self.result={'message':'token=synthetic-secret /Users/fixture/private 192.0.2.1','password':'synthetic-secret'}
        job=self.finish('console.get');text=json.dumps(job)
        for value in ('synthetic-secret','/Users/fixture','192.0.2.1'):self.assertNotIn(value,text)

    def test_assets_restricted(self):
        self.start();(self.project/'Assets/image.png').write_bytes(image())
        self.assertEqual(self.finish('assets.list')['status'],'succeeded')
        self.assertEqual(self.finish('asset.import',{'path':'Assets/image.png'})['status'],'succeeded')
        for value in ('Library/a.png','Assets/../key.png','/etc/key.png','Assets/Editor/OrcaUnityBridge.cs','Assets/x.cs','Assets/.git/key.png'):
            with self.assertRaises((ValueError,OSError)):self.control.call('fixture','asset.import',{'path':value},True)

    def test_symlink_and_project_identity(self):
        (self.project/'Assets/link').symlink_to(self.root)
        with self.assertRaises(ValueError):self.control.start('fixture',True)
        (self.project/'Assets/link').unlink()
        self.project.rename(self.root/'old');self.project.mkdir()
        with self.assertRaises(ValueError):self.control.start('fixture',True)

    def test_external_lock_and_no_adoption(self):
        self.assertTrue(self.control.stop('fixture',True)['external_processes_untouched']);self.kill.assert_not_called()
        (self.project/'Temp').mkdir();(self.project/'Temp/UnityLockfile').write_text('synthetic')
        job=self.start();self.assertEqual(job['status'],'failed');self.spawn.assert_not_called()

    def test_timeout_and_exit_code(self):
        self.healthy=False;job=self.control.start('fixture',True,1)
        self.wait(lambda:self.control.job_status(job['id'])['status']=='failed')
        value=self.control.job_status(job['id']);self.assertTrue(value['timed_out']);self.assertTrue(value['outcome_unknown']);self.assertEqual(value['exit_code'],-15)

    def test_cancel_pending_edit_ends_only_owned_session(self):
        self.start();self.paused=True;job=self.control.call('fixture','play.start',{},True)
        self.wait(lambda:self.control.job_status(job['id'])['status']=='running')
        self.assertEqual(self.control.cancel(job['id'])['status'],'cancelled');self.assertTrue(self.kill.called)

    def test_stopped_queued_edit_cannot_enter_a_new_session(self):
        self.start()
        entered, release = threading.Event(), threading.Event()
        validate = self.control.validate
        def blocked(body):
            if body['operation'] == 'gameobject.create' and threading.current_thread() is not threading.main_thread():
                entered.set()
                release.wait(3)
            return validate(body)
        with patch.object(self.control, 'validate', side_effect=blocked):
            job=self.control.call('fixture','gameobject.create',{'name':'never created'},True)
            self.assertTrue(entered.wait(2))
            self.control.stop('fixture',True)
            self.start()
            release.set()
            self.control.threads[-2].join(2)
        self.assertEqual(self.control.job_status(job['id'])['status'],'failed')
        self.assertFalse(list(self.control.session['folder'].glob(job['id']+'*')))
        self.assertEqual(self.control.status()['state'],'running')
        self.assertEqual(self.spawn.call_count,2)

    def test_abnormal_exit_diagnostics(self):
        job=self.start();self.children[0].code=23
        self.wait(lambda:self.control.job_status(job['id'])['status']=='failed')
        self.assertEqual(self.control.job_status(job['id'])['exit_code'],23)

    def test_restart_unknown_never_signals_old_pid(self):
        job=self.start();self.control.save(self.control.jobs[job['id']]);self.kill.reset_mock()
        recovered=UnityControl(self.config,self.run,self.spawn)
        self.assertTrue(recovered.job_status(job['id'])['outcome_unknown']);recovered.close();self.kill.assert_not_called()

    def test_mcp_schema_required_boolean_and_no_auto_consent(self):
        client=object.__new__(mcp.Client);client.request=Mock(return_value={})
        for tool in mcp.UNITY_TOOLS:
            if not tool['name'].startswith('unity_'):continue
            schema=tool['inputSchema'];self.assertIn('authorized',schema['required']);self.assertEqual(schema['properties']['authorized']['type'],'boolean')
            for auth in (True,False):
                args={'project_id':'fixture','authorized':auth}
                if tool['name']=='unity_call':args['name']='scene.get'
                client.call(tool['name'],args);self.assertIs(client.request.call_args.args[1]['authorized'],auth)
            with self.assertRaises(ValueError):mcp.check_args(tool,{'project_id':'fixture'})

    def test_http_mcp_health_operations_and_artifacts(self):
        from bridge_platform.macos_native import NativeControl
        native=NativeControl(self.config);native.unity.close();native.unity=self.control;self.addCleanup(native.close)
        config={**self.config,'token':'c'*64,'allowed_peers':['127.0.0.1'],'workflow_dirs':[]}
        adapter=MacOSPlatform(self.root)
        with patch.object(adapter,'native_controls',return_value=(native,None)),patch.object(adapter,'authorize_peer',return_value=True):
            bridge=Bridge(config,platform_adapter=adapter)
            server=Server(('127.0.0.1',0),make_handler(bridge));threading.Thread(target=server.serve_forever,daemon=True).start()
            self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
            file=self.root/'client.fixture.json';file.write_text(json.dumps({'url':'http://127.0.0.1:'+str(server.server_port),'token':config['token']}));client=mcp.Client(file)
            for args in ({'project_id':'fixture'},{'project_id':'fixture','authorized':False}):
                with self.assertRaises(ValueError):client.call('unity_start',args)
            job=client.call('unity_start',{'project_id':'fixture','authorized':True})
            self.wait(lambda:client.call('native_job_status',{'job_id':job['id']}).get('ready'))
            self.assertTrue(client.call('unity_tools',{'project_id':'fixture','authorized':True})['tools'])
            job=client.call('unity_call',{'project_id':'fixture','authorized':True,'name':'gameview.capture'})
            self.wait(lambda:client.call('native_job_status',{'job_id':job['id']})['status']=='succeeded')
            receipt=client.call('fetch_native_artifact',{'job_id':job['id'],'index':0,'destination_dir':str(self.root/'download')})
            self.assertEqual(Path(receipt['path']).read_bytes(),image())
            self.assertEqual(client.call('list_workflows',{}),{'workflows':[]})
            client.call('unity_stop',{'project_id':'fixture','authorized':True});self.assertFalse(native.godot.active);self.assertFalse(native.blender.active)

    def test_storage_failure_does_not_disable_other_engines(self):
        from bridge_platform.macos_native import NativeControl
        with patch('bridge_platform.macos_native.UnityControl',side_effect=ValueError('synthetic')):native=NativeControl(self.config)
        self.addCleanup(native.close)
        self.assertEqual(native.unity.status()['state'],'error');self.assertEqual(native.godot.status()['state'],'unsupported')
        native.godot.close=Mock(side_effect=ValueError('synthetic'));native.blender.close=Mock();native.unity.close=Mock()
        with self.assertRaises(ValueError):native.close()
        native.blender.close.assert_called_once();native.unity.close.assert_called_once();native.godot.close=Mock()

    def test_render_pipeline_versions_never_rewritten(self):
        path=self.project/'Packages/manifest.json'
        original=json.dumps({'dependencies':{'com.unity.render-pipelines.universal':'16.0.1'}})
        path.write_text(original)
        with self.assertRaises(Unsupported):self.control.start('fixture',True)
        self.assertEqual(path.read_text(),original);self.spawn.assert_not_called()
        path.write_text(json.dumps({'dependencies':{'com.unity.render-pipelines.universal':'17.0.3'}}))
        self.assertTrue(self.start()['ready'])

    def test_response_error_no_retry(self):
        self.start();self.response_ok=False
        job=self.finish('gameobject.create',{'name':'mock'})
        self.assertEqual(job['status'],'failed');self.assertEqual(job['error_code'],'editor_rejected')
        self.assertEqual(self.spawn.call_count,1)

    def test_forged_or_stale_health_is_not_running(self):
        self.start();self.healthy=False
        time.sleep(.03)
        folder=self.control.session['folder'];path=folder/'health.json'
        value=json.loads(path.read_text())
        for change in ({'pid':-1},{'session':'wrong'},{'timestamp':time.time()-60},{'project':'/synthetic/unapproved'}):
            path.write_text(json.dumps({**value,**change}))
            self.assertIsNone(self.control.health())
        path.write_text(json.dumps(value))

    def test_artifact_symlink_and_size_rejected(self):
        from bridge_platform.macos_godot import open_inside, LIMIT
        file=self.project/'Assets/large.png'
        with file.open('wb') as stream:stream.truncate(LIMIT+1)
        with self.assertRaises(ValueError):asset_path(self.project,'Assets/large.png',{'.png'})
        link=self.project/'Assets/link.png';link.symlink_to(file)
        with self.assertRaises(ValueError):asset_path(self.project,'Assets/link.png',{'.png'})

    def test_mcp_stdio_rejects_missing_nonboolean_authorization(self):
        client=Mock()
        for name in ('unity_start','unity_stop','unity_tools','unity_call'):
            for auth in ({},{'authorized':False},{'authorized':'true'}):
                args=dict(project_id='fixture',**auth)
                if name=='unity_call':args['name']='scene.get'
                request={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':name,'arguments':args}}
                stdin=SimpleNamespace(buffer=io.BytesIO(json.dumps(request).encode()+b'\n'));stdout=io.StringIO()
                client.call.return_value={}
                with patch.object(mcp.sys,'stdin',stdin),patch.object(mcp.sys,'stdout',stdout):mcp.serve(client,mcp.UNITY_TOOLS)
                response=json.loads(stdout.getvalue())
                self.assertEqual(response['result'].get('isError',False), auth.get('authorized') is not False)

    def test_windows_dispatch_consumes_explicit_consent_without_altering_native_signature(self):
        handler=make_handler(SimpleNamespace())
        for body in ({'project_id':'fixture'},{'project_id':'fixture','authorized':True}):
            fake=SimpleNamespace(body=lambda:dict(body))
            self.assertEqual(handler.unity_legacy_body(fake),{'project_id':'fixture'})
        for auth in (False,1,'true',None):
            fake=SimpleNamespace(body=lambda:{'project_id':'fixture','authorized':auth})
            with self.assertRaises(ValueError):handler.unity_legacy_body(fake)

    def test_extension_uses_only_editor_apis_and_fixed_dispatch(self):
        source=EXTENSION.read_text()
        for token in ('EditorSceneManager.SaveScene','AssetDatabase.ImportAsset','ScreenCapture.CaptureScreenshot','EditorApplication.isPlaying','Undo.AddComponent<','authorized','GraphicsDeviceType.Metal'):
            self.assertIn(token,source)
        for token in ('Process.Start','Assembly.Load','SetGraphicsAPIs','EditorPrefs.Set','Evaluate','HttpListener','InputSimulator'):
            self.assertNotIn(token,source)
        for op in FIELDS:self.assertIn('"'+op+'"',source)

if __name__=='__main__':unittest.main()
