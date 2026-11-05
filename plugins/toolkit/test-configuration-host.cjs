'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto'),cp=require('node:child_process');
const {createHostClient,METHOD}=require('./config-service/host-client.cjs');
const {createHostHandler}=require('./config-service/host-handler.cjs');
const {createCommands,validate}=require('./config-service/commands.cjs');
const {createClient}=require('./config-service/client.cjs');
const fixture=require('./fixtures/config-service.cjs');
test('missing or revoked Host API fails closed without spawning a local Bridge',async t=>{
 t.mock.method(cp,'spawn',()=>{throw Error('must not spawn');});t.mock.method(cp,'spawnSync',()=>{throw Error('must not spawn');});
 let calls=0;const client=createHostClient({host:{has:()=>false,call:()=>{calls++;}}});
 await assert.rejects(client.read('settings'),/sealed Host/);assert.equal(calls,0);
 const denied=createHostClient({host:{has:()=>true,call:async()=>{throw Error('private host details');}}});
 await assert.rejects(denied.read('settings'),e=>/Host unavailable or access denied/.test(e.message)&&!e.message.includes('private'));
 const session={},handler=createHostHandler(candidate=>candidate===session);
 assert.equal((await handler({version:1,request:{op:'status'}},{})).ok,false);
 for(const envelope of [{version:2,request:{op:'status'}},{version:1,request:{op:'status'},pid:1},{version:1,request:{op:'status',caller:'toolkit'}},{version:1,request:{op:'exec',script:'bad'}}])assert.equal((await handler(envelope,session)).ok,false);
 assert.throws(()=>createHostHandler(),/authorizer/);
});
test('commands reject arbitrary scripts, identity claims, paths and malformed mutations before Host calls',async()=>{
 let calls=0;const command=createCommands({request:async()=>{calls++;}});
 for(const input of [{operation:'exec',script:'bad'},{operation:'status',op:'backup'},{operation:'status',pid:1},{operation:'preview',sources:{settings:'/tmp/file','downloads-preferences':null}},{operation:'restore',backup_id:'../../db'},{operation:'confirm',preview_id:'a'.repeat(32),hashes:{}},{operation:'patch',namespace:'settings',store_id:'a'.repeat(32),epoch:'b'.repeat(32),revision:'1',patch:[{op:'set',path:['__proto__'],value:1}]}])await assert.rejects(command(input));
 assert.equal(calls,0);assert.throws(()=>validate({op:'patch-many'}));
});
test('async clients keep per-instance and per-namespace snapshots, preserving exact unknown values',async t=>{
 const backend=fixture.install(t,{settings:Buffer.from('{"future":9007199254740993123456789}').toString('base64'),'downloads-preferences':null});
 const a=createClient(async r=>backend.request(r)),b=createClient(async r=>backend.request(r));
 const first=await a.read('settings'),second=await b.read('settings');
 assert.throws(()=>a.save('settings',second,second),/Reload/);assert.throws(()=>a.save('downloads-preferences',first,first),/Reload/);
 await a.save('settings',{gpu:{enabled:false}},first);await b.save('settings',{accounts:{enabled:false}},second);
 const raw=backend.request({op:'read',namespace:'settings'}).raw_json;
 assert.ok(raw.includes('9007199254740993123456789'));assert.equal(JSON.parse(raw).gpu.enabled,false);assert.equal(JSON.parse(raw).accounts.enabled,false);
});
test('maintenance UI previews exact bytes, confirms a snapshot, displays backup/restore and reuses request IDs',async t=>{
 const backend=fixture.install(t),elements=new Map();
 const element=id=>{if(!elements.has(id))elements.set(id,{disabled:false,checked:false,files:[],value:'',textContent:''});return elements.get(id);};
 const source=fs.readFileSync(path.join(__dirname,'configuration-panel.html'),'utf8').match(/<script nonce="__TOKEN__">([\s\S]*?)<\/script>/)[1];
 const requests=[];let reloaded=0,fail=false;
 const command=createCommands(backend);
 // This fixture is already migrated; preview remains available for inspection.
 const window={dispatchEvent:()=>{reloaded++;},confirm:()=>true,orcaToolkitConfiguration:{call:async request=>{requests.push(request);if(fail)throw Error('service offline');return command(request);}}};
 vm.runInNewContext(source,{document:{getElementById:element},window,crypto,Event:class{},Uint8Array,btoa:s=>Buffer.from(s,'binary').toString('base64'),fetch:async(url,options)=>{
  assert.equal(url,'/api/configuration');assert.equal(options.headers['X-Toolkit-Token'],'__TOKEN__');const request=JSON.parse(options.body);requests.push(request);
  try{if(fail)throw Error('service offline');const result=await command(request);return {ok:true,json:async()=>result};}catch(e){return {ok:false,json:async()=>({error:e.message})};}
 }});
 await element('config-preview').onclick();assert.match(element('config-result').textContent,/Select both/);assert.equal(requests.length,0);
 const bytes=Buffer.from('{"future":9007199254740993123456789}');element('config-settings-file').files=[{size:bytes.length,arrayBuffer:async()=>Uint8Array.from(bytes).buffer}];element('config-preferences-defaults').checked=true;
 await element('config-preview').onclick();assert.equal(element('config-confirm').disabled,false);assert.equal(requests.at(-1).sources.settings,bytes.toString('base64'));
 await element('config-confirm').onclick();assert.match(element('config-result').textContent,/Already migrated/);assert.equal(requests.at(-1).operation,'confirm');
 await element('config-backup').onclick();assert.match(element('config-backup-id').value,/^backup-/);
 await element('config-restore').onclick();assert.match(element('config-result').textContent,/"state": "ready"/);assert.equal(reloaded,1);
 const restore=requests.at(-1);await element('config-retry').onclick();assert.deepEqual(requests.at(-1),restore);
 fail=true;await element('config-status').onclick();assert.match(element('config-result').textContent,/offline/);
 window.toolkitConfiguration.setState({configService:'host-required'});assert.equal(element('config-preview').disabled,true);assert.equal(element('config-status').disabled,false);
});
test('Host method is versioned and response errors cannot masquerade as successful reads',async()=>{
 const client=createHostClient({host:{has:m=>m===METHOD,call:async()=>({ok:true})}});await assert.rejects(client.read('settings'),/Invalid trusted/);
});

test('Host commands retain local legacy-task guard before target changes, imports and restores',async t=>{
 const os=require('node:os'),{Controller}=require('./controller.cjs'),root=fs.mkdtempSync(path.join(os.tmpdir(),'orca-host-legacy-'));
 t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
 const runtime={platform:'darwin',home:root,configDir:root,downloadsDir:path.join(root,'downloads'),configClient:createClient(async()=>{throw Error('offline');})};
 const controller=new Controller({runtime,gpu:{},accounts:{}});fs.mkdirSync(path.join(runtime.downloadsDir,'jobs'),{recursive:true});
 const file=path.join(runtime.downloadsDir,'jobs','a'.repeat(32)+'.json');fs.writeFileSync(file,JSON.stringify({status:'paused',protocol:2}));
 let calls=0;const command=createCommands({request:async()=>{calls++;}},{beforeMutation:r=>controller.prepareConfigurationChange(r)});
 await assert.rejects(command({operation:'restore',backup_id:'backup-'+'b'.repeat(32)+'.sqlite'}),/pending downloads/);
 await assert.rejects(command({operation:'patch',namespace:'settings',store_id:'a'.repeat(32),epoch:'b'.repeat(32),revision:'1',patch:[{op:'set',path:['downloads','sshHost'],value:'example.invalid'}]}),/pending downloads/);
 assert.equal(calls,0);await command({operation:'status'});assert.equal(calls,1);
 fs.writeFileSync(file,JSON.stringify({status:'complete',cleanupWarning:'unknown'}));assert.throws(()=>controller.prepareConfigurationChange({op:'import'}),/pending downloads/);
 fs.writeFileSync(file,JSON.stringify({status:'cancelled'}));controller.prepareConfigurationChange({op:'restore'});
});

test('localhost page token and correct Origin cannot invoke configuration or read service status',async t=>{
 const os=require('node:os'),{startServer}=require('./server.cjs');
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'toolkit-localhost-deny-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
 let calls=0;
 const configClient=createClient(async()=>{calls++;throw Error('host-required');});
 const service=await startServer({port:0,runtime:{platform:'darwin',configDir:root,downloadsDir:path.join(root,'downloads'),home:root,configClient},gpu:{async status(){return {}; }},accounts:{}});
 t.after(()=>service.close());
 const html=await(await fetch(service.url)).text();
 const token=html.match(/const token='([a-f0-9]+)'/)[1];
 const headers={'X-Toolkit-Token':token,Origin:new URL(service.url).origin,'Content-Type':'application/json'};
 const before=calls;
 for(const request of [{method:'GET',path:'api/config-service'},{method:'POST',path:'api/configuration',body:JSON.stringify({operation:'status',pluginKey:'toolkit',session:'forged'})}]){
  const result=await fetch(service.url+request.path,{method:request.method,headers,body:request.body});assert.equal(result.status,403);
 }
 assert.equal(calls,before);
});

test('revocation during async Host authorization prevents socket creation',async()=>{
 let release,created=0;
 const handler=createHostHandler(()=>new Promise(resolve=>{release=resolve;}),{createSocketPair:()=>{created++;throw Error('revoked');}});
 const controller=new AbortController();const result=handler({version:1,request:{op:'status'}},{},controller.signal);
 controller.abort();release(true);assert.equal((await result).ok,false);assert.equal(created,0);
});
