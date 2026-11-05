'use strict';
const nodeTest=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),http=require('node:http');
const test=(name,fn)=>nodeTest(name,{skip:process.platform!=='darwin'},fn);
const {Gpu}=require('./modules/gpu.cjs'),{startServer}=require('./server.cjs'),{compose}=require('./compose-ui.cjs');
const checkNames=['installed','launch_agent','tailscale','token','peers','comfyui'];
function fixture(t){
 const home=fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(),'gpu-fixture-')));t.after(()=>fs.rmSync(home,{recursive:true,force:true}));
 const gpuDir=path.join(home,'bridge');fs.mkdirSync(gpuDir,{mode:0o700});
 const put=(name,data)=>fs.writeFileSync(path.join(gpuDir,name),typeof data==='string'?data:JSON.stringify(data),{mode:0o600});
 put('config.json',{platform:'darwin',data_dir:gpuDir});put('launcher.json',{platform:'darwin',label:'org.orca.local-gpu-bridge'});put('token','c'.repeat(64));
 return {home,gpuDir,put,runtime:{platform:'darwin',home,gpuDir,local:home,configDir:path.join(home,'toolkit')}};
}
async function manager(t,f){
 const state={value:{platform:'darwin',capability:'comfyui',ready:true,checks:Object.fromEntries(checkNames.map(k=>[k,true]))},calls:[]};
 const server=http.createServer((req,res)=>{
  state.calls.push(req.url);assert.equal(req.headers.authorization,'Bearer '+'c'.repeat(64));
  res.setHeader('Content-Type','application/json');res.end(JSON.stringify(req.url==='/api/workflows'?{workflows:[]}:state.value));
 });await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>server.close(r)));
 const get=http.get;t.mock.method(http,'get',(url,options,callback)=>get(String(url).replace(':18773',':'+server.address().port),options,callback));
 return state;
}
test('Mac GPU ready requires all six checks and actual health response',async t=>{
 const f=fixture(t),state=await manager(t,f),gpu=new Gpu(f.runtime);
 assert.equal((await gpu.status()).online,true);
 for(const name of checkNames){state.value.checks[name]=false;assert.equal((await gpu.status()).online,false,name);state.value.checks[name]=true;}
 state.value.ready=false;assert.equal((await gpu.status()).online,false);
 state.value.ready=true;delete state.value.capability;assert.equal((await gpu.status()).online,false);
});
test('launcher alone, invalid token and unsafe configuration never report ready or start a process',async t=>{
 const f=fixture(t),state=await manager(t,f),gpu=new Gpu(f.runtime);let spawns=0;f.runtime.spawnDetached=()=>{spawns++;};
 f.put('token','invalid');assert.equal((await gpu.status()).online,false);
 f.put('token','c'.repeat(64));fs.chmodSync(path.join(f.gpuDir,'config.json'),0o644);assert.equal((await gpu.status()).online,false);
 fs.chmodSync(path.join(f.gpuDir,'config.json'),0o600);fs.unlinkSync(path.join(f.gpuDir,'token'));
 assert.equal((await gpu.status()).online,false);await assert.rejects(gpu.open(),/private configuration/);
 assert.equal(state.calls.length,0);assert.equal(spawns,0);
});
test('Mac GPU rejects symlink secrets and secret-bearing HTTP errors are redacted',async t=>{
 const f=fixture(t),gpu=new Gpu(f.runtime),token=path.join(f.gpuDir,'token');
 fs.renameSync(token,token+'-target');fs.symlinkSync(token+'-target',token);assert.equal((await gpu.status()).online,false);
 fs.unlinkSync(token);fs.renameSync(token+'-target',token);
 t.mock.method(http,'get',()=>{const {EventEmitter}=require('node:events');const req=new EventEmitter();process.nextTick(()=>req.emit('error',Error('fixture-secret')));return req;});
 await assert.rejects(gpu.open(),e=>!e.message.includes('fixture-secret')&&/offline/.test(e.message));
});
test('Mac manager can open before ComfyUI is ready and exposes only read-only ComfyUI queries',async t=>{
 const f=fixture(t),state=await manager(t,f),gpu=new Gpu(f.runtime);state.value.checks.comfyui=false;state.value.ready=false;
 assert.deepEqual(await gpu.open(),{url:'http://127.0.0.1:18773/',ready:false});
 await assert.rejects(gpu.mac.request('/api/native/setup'),/Only ComfyUI/);
 await assert.rejects(gpu.mac.request('/api/import'),/Only ComfyUI/);
});
test('Toolkit Mac routes deny native operations while health/workflows work and Accounts stay passive',async t=>{
 require('./fixtures/config-service.cjs').install(t);
 const f=fixture(t),state=await manager(t,f);
 const s=await startServer({runtime:f.runtime,dataDir:f.runtime.configDir,port:0});t.after(()=>s.close());
 const html=await (await fetch(s.url)).text(),token=html.match(/const token='([a-f0-9]+)'/)[1];
 const headers={'X-Toolkit-Token':token,'Content-Type':'application/json',Origin:new URL(s.url).origin};
 for(const route of ['native','native/setup','import']){
  const response=await fetch(s.url+'api/gpu/'+route,{method:route==='native'?'GET':'POST',headers,body:route==='native'?undefined:'{}'});
  assert.equal(response.status,400);
 }
 assert.equal((await fetch(s.url+'api/gpu/workflows',{headers})).status,200);
 const result=await (await fetch(s.url+'api/status',{headers})).json();assert.equal(result.modules.gpu.status,'ready');assert.equal(result.modules.accounts.status,'preview');
 state.value.checks.tailscale=false;state.value.ready=false;
 assert.equal((await (await fetch(s.url+'api/status',{headers})).json()).modules.gpu.status,'offline');
});
test('Mac GPU panel has no native scripts or firewall controls; Windows panel preserved',()=>{
 const mac=compose('overview','darwin'),win=compose();
 assert.ok(mac.includes('ComfyUI on Mac'));assert.ok(mac.includes("'/api/status'"));assert.ok(mac.includes("'/api/gpu/workflows'"));
 assert.ok(!mac.includes('HNetCfg'));assert.ok(!mac.includes("'/api/gpu/native/setup'"));assert.ok(win.includes("'/api/gpu/native/setup'"));
});
test('Godot availability is independent of ComfyUI and requires every verified check',async t=>{
 const f=fixture(t),state=await manager(t,f),gpu=new Gpu(f.runtime);
 const names=['installed','path','permissions','version','architecture','bridge_api'];
 state.value.godot={state:'available',checks:Object.fromEntries(names.map(k=>[k,true]))};
 state.value.checks.comfyui=false;state.value.ready=false;
 let status=await gpu.status();assert.equal(status.online,false);assert.equal(status.godot.state,'available');
 for(const key of names){state.value.godot.checks[key]=false;assert.equal((await gpu.status()).godot.state,'error',key);state.value.godot.checks[key]=true;}
 for(const value of ['installed','running','unsupported','error']){state.value.godot.state=value;assert.equal((await gpu.status()).godot.state,value);}
 state.value.godot.state='available';state.value.platform='win32';assert.equal((await gpu.status()).godot.state,'error');
 state.value.platform='darwin';state.value.ready=true;state.value.checks.comfyui=true;
 state.value.godot.state='error';assert.equal((await gpu.status()).online,true);
});
test('Toolkit status and panel expose distinct Godot capability without native write routes',async t=>{
 const f=fixture(t),state=await manager(t,f);
 state.value.godot={state:'installed',checks:{installed:true}};
 const s=await startServer({runtime:f.runtime,dataDir:f.runtime.configDir,port:0});t.after(()=>s.close());
 const html=await (await fetch(s.url)).text(),token=html.match(/const token='([a-f0-9]+)'/)[1];
 const result=await (await fetch(s.url+'api/status',{headers:{'X-Toolkit-Token':token}})).json();
 assert.equal(result.modules.gpu.godot.state,'installed');assert.equal(result.modules.gpu.status,'ready');
 assert.match(html,/godot-state/);assert.match(html,/not sandboxed/);
});
test('Blender status is independent and every availability check must pass',async t=>{
 const f=fixture(t),state=await manager(t,f),gpu=new Gpu(f.runtime);
 const names=['installed','path','permissions','version','architecture','bridge_api'];
 state.value.blender={state:'available',checks:Object.fromEntries(names.map(k=>[k,true]))};
 state.value.godot={state:'error'};state.value.ready=false;state.value.checks.comfyui=false;
 let status=await gpu.status();assert.equal(status.blender.state,'available');assert.equal(status.godot.state,'error');assert.equal(status.comfyui.state,'installed');
 for(const key of names){state.value.blender.checks[key]=false;assert.equal((await gpu.status()).blender.state,'error');state.value.blender.checks[key]=true;}
 for(const value of ['installed','running','unsupported','error']){state.value.blender.state=value;assert.equal((await gpu.status()).blender.state,value);}
 state.value.blender.state='available';state.value.platform='win32';assert.equal((await gpu.status()).blender.state,'error');
 assert.match(compose('overview','darwin'),/blender-state/);
});
test('Unity running requires owned Editor API and Metal; all four engines stay independent',async t=>{
 const f=fixture(t),state=await manager(t,f),gpu=new Gpu(f.runtime);
 const base=['installed','path','permissions','version','architecture','bridge_api'];
 const all=[...base,'editor_api','metal','graphics'];
 state.value.unity={state:'running',checks:Object.fromEntries(all.map(k=>[k,true]))};
 state.value.blender={state:'available',checks:Object.fromEntries(base.map(k=>[k,true]))};
 state.value.godot={state:'available',checks:Object.fromEntries(base.map(k=>[k,true]))};
 assert.equal((await gpu.status()).unity.state,'running');
 for(const key of all){state.value.unity.checks[key]=false;const value=await gpu.status();assert.equal(value.unity.state,'error',key);assert.equal(value.blender.state,'available');assert.equal(value.godot.state,'available');assert.equal(value.comfyui.state,'running');state.value.unity.checks[key]=true;}
 state.value.unity.state='available';state.value.unity.checks.editor_api=false;assert.equal((await gpu.status()).unity.state,'available');
 state.value.checks.comfyui=false;state.value.ready=false;assert.equal((await gpu.status()).unity.state,'available');
 for(const value of ['installed','unsupported','error']){state.value.unity.state=value;assert.equal((await gpu.status()).unity.state,value);}
 assert.match(compose('overview','darwin'),/unity-state/);
});
