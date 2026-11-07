'use strict';
// Real Host SQLite and Ed25519 verification; injected endpoint, range transport and identity.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os'),cp=require('node:child_process'),crypto=require('node:crypto');
const {ToolkitAuthority,ToolkitTasks,toolkitDownloadLifecycle}=require(process.argv[2]);
const config=path.resolve(__dirname,'../plugins/toolkit/config-service');
const {DownloadManager}=require('../plugins/toolkit/modules/downloads/engine.cjs');
const {V3Transport,canonical}=require('../plugins/toolkit/modules/downloads/v3.cjs');
function fixture(t){
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'host-recovery-')),state=path.join(root,'state'),dataDir=path.join(root,'downloads');fs.mkdirSync(state,{mode:0o700});
 cp.execFileSync('python3',['-c','import sys;sys.path.insert(0,sys.argv[1]);from config_store import ConfigStore;s=ConfigStore(sys.argv[2],True);s.close()',config,state]);
 const request=r=>JSON.parse(cp.execFileSync('python3',[path.join(config,'fixture.py'),state],{input:JSON.stringify(r),encoding:'utf8'}));
 const preview=request({op:'preview',request_id:crypto.randomUUID(),sources:{settings:null,'downloads-preferences':null}}).result;assert.equal(request({op:'import',request_id:crypto.randomUUID(),preview_id:preview.preview_id,hashes:preview.hashes}).ok,true);
 let loseRelease=false,remoteTimeout=false,loseCreate=false,stopAfterRelease=false;
 const authority=new ToolkitAuthority({validate:require(path.join(config,'commands.cjs')).validate,createHostHandler:authorize=>async(envelope,grant)=>{
  assert.ok(authorize(grant));const result=request(envelope.request);
  if(loseCreate&&envelope.request.op==='task-create'){loseCreate=false;return {ok:false,error:'create reply lost'};}
  if(loseRelease&&envelope.request.op==='task-update'&&envelope.request.released){loseRelease=false;return {ok:false,error:'release reply lost'};}
  return result;
 }},()=>{throw Error('synthetic identity');});
 const {privateKey,publicKey}=crypto.generateKeyPairSync('ed25519'),bytes=Buffer.from('AUTHENTIC BYTES');
 const trust={server_id:'server',endpoint_id:'endpoint',account_id:'reader',authority_epoch:'remote-epoch',signing_key_id:'one',receiptKey:publicKey.export({type:'spki',format:'pem'})};
 let manager,tasks,lifecycle,tr,requests=[];
 function open(){
  tasks=new ToolkitTasks(authority);lifecycle=toolkitDownloadLifecycle(tasks);
  tr=Object.assign(Object.create(V3Transport.prototype),{dataDir,trust,endpointOptions:{hostTasks:{...lifecycle,async transition(...args){await lifecycle.transition(...args);if(stopAfterRelease&&args[2]){stopAfterRelease=false;throw Error('exit after Host release');}}}},
   async prepare(job){
    job.protocol=3;job.trust=trust;job.publicKeyHash='b'.repeat(64);job.authGeneration='c'.repeat(32);job.registrationId='saved';
    fs.mkdirSync(this.directory(job),{mode:0o700,recursive:true});for(const name of ['key','key.pub','known_hosts']){const file=path.join(this.directory(job),name);if(!fs.existsSync(file))fs.writeFileSync(file,'fixture',{mode:0o600});}this.persist(job);
    return {size:15,sha256:crypto.createHash('sha256').update(bytes).digest('hex')};
   },
   async read(job,offset,length,signal,write){write(bytes.subarray(offset,offset+length),0);},
   async manage(r){requests.push(structuredClone(r));if(remoteTimeout)throw Error('revoke timeout');const payload={...this.bindings(manager.jobs.get(r.task_id)),auth_generation:r.auth_generation,action:'revoke',state:'REVOKED',request_id:r.request_id,client_nonce:r.client_nonce,read_barrier:'DRAINED',revocation_commit_seq:'1',issued_at:'1'};const raw=Buffer.from(canonical(payload));return {payload:raw.toString('base64'),signature:crypto.sign(null,Buffer.concat([Buffer.from('orca-download-revocation-v3\0'),raw]),privateKey).toString('base64')};}
  });
  manager=new DownloadManager({dataDir,runtime:{platform:'darwin'},environment:'',sshHost:'',sshUser:'',sshPort:22,transport:tr,hostTasks:lifecycle});return manager;
 }
 t.after(async()=>{await manager?.close();authority.revokeAll();fs.rmSync(root,{recursive:true,force:true});});
 return {root,dataDir,open,request,get manager(){return manager;},get tasks(){return tasks;},get tr(){return tr;},get requests(){return requests;},fault(name,value=true){if(name==='release')loseRelease=value;if(name==='remote')remoteTimeout=value;if(name==='create')loseCreate=value;if(name==='exit')stopAfterRelease=value;},async create(){const row=await manager.create({source:'/project/file',folder:path.join(root,'out'),requestId:'12345678-1234-1234-1234-123456789abc'});await manager.runs.get(row.id)?.promise;return manager.jobs.get(row.id);},record(id){return request({op:'status'}).result.task_inventory.tasks.find(r=>r.task_id===id);},key(job){return path.join(dataDir,'keys',job.id,'key');}};
}
for(const mode of ['release','exit'])test(mode+': restart claims released terminal record and cleans keys using saved proof offline',async t=>{
 const f=fixture(t);f.open();f.fault(mode);const job=await f.create();assert.equal(f.record(job.id).released,true);assert.ok(fs.existsSync(f.key(job)));assert.ok(job.receipt);
 await f.manager.close();f.fault('remote');f.open();await f.manager.ready;assert.equal(fs.existsSync(f.key(job)),false);assert.equal(f.manager.jobs.get(job.id).cleanupWarning,null);
 await f.manager.action(job.id,'cleanup');await f.manager.action(job.id,'cleanup');assert.equal(f.record(job.id).released,true);
});
test('completed transfer with revoke timeout retains lease and retries stable challenge',async t=>{
 const f=fixture(t);f.open();f.fault('remote');const job=await f.create();assert.equal(job.status,'complete');assert.equal(f.record(job.id).released,false);assert.ok(fs.existsSync(f.key(job)));const challenge=f.requests[0];
 f.fault('remote',false);await f.manager.action(job.id,'cleanup');assert.deepEqual(f.requests[1],challenge);assert.equal(f.record(job.id).released,true);
});
test('Host create committed then local write failed: same request retries without duplicate task',async t=>{
 const f=fixture(t),manager=f.open();await manager.ready;const save=manager.save.bind(manager);let fail=true;
 manager.save=job=>{if(!job.hostIntent&&fail){fail=false;throw Error('disk full after Host create');}save(job);};
 await assert.rejects(f.create(),/disk full/);const job=[...manager.jobs.values()][0];assert.equal(f.record(job.id).state,'created');assert.equal(job.hostIntent,true);
 const retry=await f.create();assert.equal(retry.id,job.id);assert.equal(f.record(job.id).released,true);
});
test('unknown create outcome plus forged JSON completion is paused after restart; forged hash cannot complete',async t=>{
 const f=fixture(t);f.open();f.fault('create');await assert.rejects(f.create(),/reply lost/);const job=[...f.manager.jobs.values()][0];
 Object.assign(job,{status:'complete',verified:true,cleanup_verified:true,sha256:'0'.repeat(64),size:15});f.manager.save(job);await f.manager.close();f.open();await f.manager.ready;
 assert.equal(f.manager.jobs.get(job.id).status,'paused');assert.equal(f.manager.jobs.get(job.id).verified,false);await f.manager.action(job.id,'resume');await f.manager.runs.get(job.id)?.promise;assert.equal(f.record(job.id).state,'error');assert.equal(f.record(job.id).released,false);assert.equal(fs.existsSync(job.destination),false);
 await f.manager.action(job.id,'cancel');await f.manager.action(job.id,'cancel');await f.manager.close();f.open();await f.manager.ready;await f.manager.action(job.id,'cancel');assert.equal(f.record(job.id).released,true);
});
test('forged receipt cannot delete keys even after authoritative release',async t=>{
 const f=fixture(t);f.open();f.fault('exit');const job=await f.create();job.receipt.signature=Buffer.alloc(64).toString('base64');f.manager.save(job);await f.manager.close();f.open();await f.manager.ready;assert.ok(fs.existsSync(f.key(job)));await assert.rejects(f.manager.action(job.id,'cleanup'),/signature/);
});
test('two independent claimants race one task; orphan and old authorization retain leases',async t=>{
 const f=fixture(t);f.open();await f.manager.ready;const handle=await f.tasks.create();const snapshot=await f.tasks.inventory();
 const contenders=[new ToolkitTasks(f.tasks.authority),new ToolkitTasks(f.tasks.authority)];
 const results=await Promise.allSettled(contenders.map(tasks=>tasks.claim(snapshot.task_inventory.tasks[0],snapshot.store_id)));assert.equal(results.filter(r=>r.status==='fulfilled').length,1);
 const lifecycle=toolkitDownloadLifecycle(contenders[0]);const recovery=await lifecycle.reconcile([],{environment:'',sshHost:'',sshUser:'',sshPort:22});assert.equal(recovery[0].state,'orphan');
 const old=await lifecycle.reconcile([{id:handle.id,protocol:2}],{environment:'',sshHost:'',sshUser:'',sshPort:22});assert.equal(old[0].state,'review');assert.equal(f.record(handle.id).released,false);for(const tasks of contenders)tasks.revoke();
});

test('concurrent repeated Host create requests share one durable record',async t=>{
 const f=fixture(t);f.open();await f.manager.ready;
 const results=await Promise.all([f.tasks.create(undefined,'f'.repeat(32)),f.tasks.create(undefined,'f'.repeat(32))]);assert.equal(results[0],results[1]);
 assert.equal(f.request({op:'status'}).result.task_inventory.tasks.length,1);
});

test('released terminal retries still reject a stale owner after another Host claim',async t=>{
 const f=fixture(t);f.open();const job=await f.create();const old=f.tasks,snapshot=await old.inventory();
 const next=new ToolkitTasks(old.authority);await next.claim(snapshot.task_inventory.tasks[0],snapshot.store_id);
 await assert.rejects(f.manager.action(job.id,'cleanup'),/owner|revision/);next.revoke();
});
