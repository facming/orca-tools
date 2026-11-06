'use strict';
// SYNTHETIC Node identity + real main authority/native transport. Not signed Host acceptance.
const assert=require('node:assert/strict'),cp=require('node:child_process'),fs=require('node:fs');
const [bundle,handlerPath,bridge,addon]=process.argv.slice(2);
const {ToolkitAuthority,TOOLKIT_CONFIGURATION_METHOD:METHOD}=require(bundle);
const adapter={...require(handlerPath),...require(require('node:path').join(require('node:path').dirname(handlerPath),'commands.cjs'))};
const spawn=cp.spawn,closed=[];let mode='native',onSpawn;
cp.spawn=(file,args,options)=>{
 assert.equal(file,'/Library/PrivilegedHelperTools/OrcaToolkit/config-bridge');
 const child=mode==='exit'?spawn('/usr/bin/false',[],options):mode==='stall'?spawn('/bin/sleep',['60'],options):spawn(bridge,args,options);
 closed.push(new Promise(resolve=>child.once('close',resolve)));
 if(onSpawn)queueMicrotask(onSpawn);
 return child;
};
const authority=new ToolkitAuthority(adapter,require(addon).createSocketPair);
const envelope={version:1,request:{op:'status'}},approve=async()=>true;
const call=context=>authority.call(context,METHOD,envelope,approve);
(async()=>{
 const context=authority.openContext();
 assert.equal((await call({...context})).ok,false);
 assert.equal((await authority.call(context,METHOD,{...envelope,session:context},approve)).ok,false);
 const initial=await call(context);assert.equal(initial.ok,true,JSON.stringify(initial));
 assert.equal(initial.result.state,'migration-pending');await Promise.all(closed);
 const baseline=fs.readdirSync('/dev/fd').length;
 const contexts=Array.from({length:3},()=>authority.openContext());
 const replies=await Promise.all(contexts.flatMap(c=>Array.from({length:4},()=>call(c))));
 assert.equal(replies.filter(r=>r.ok).length,12);
 for(let i=0;i<12;i++)assert.equal((await call(context)).ok,true);
 let release;const old=authority.openContext();
 const pending=authority.call(old,METHOD,envelope,()=>new Promise(resolve=>{release=resolve;}));
 authority.revoke(old);release(true);assert.equal((await pending).ok,false);
 assert.equal((await call(old)).ok,false);
 const next=authority.openContext();onSpawn=()=>authority.revoke(next);
 assert.equal((await call(next)).ok,false);onSpawn=undefined;
 mode='exit';for(let i=0;i<12;i++)assert.equal((await call(context)).ok,false);
 mode='stall';assert.equal((await call(context)).ok,false);
 mode='native';assert.equal((await call(context)).ok,true);
 await Promise.all(closed);await new Promise(resolve=>setImmediate(resolve));
 assert.ok(fs.readdirSync('/dev/fd').length<=baseline,'FD leak after concurrency, abnormal exit and revoke');
 const {ToolkitTasks,toolkitDownloadLifecycle}=require(bundle);
 if(ToolkitTasks){
  const send=request=>authority.call(context,METHOD,{version:1,request},approve);
  const preview=await send({op:'preview',request_id:require('node:crypto').randomUUID(),sources:{settings:null,'downloads-preferences':null}});
  assert.equal(preview.ok,true);
  assert.equal((await send({op:'import',request_id:require('node:crypto').randomUUID(),preview_id:preview.result.preview_id,hashes:preview.result.hashes})).ok,true);
  const tasks=new ToolkitTasks(authority),handle=await tasks.create();
  await assert.rejects(tasks.update({...handle},'running'),/owner/);
  await tasks.update(handle,'running');await tasks.update(handle,'paused');
  const backup=await send({op:'backup'});
  assert.equal((await send({op:'restore',request_id:require('node:crypto').randomUUID(),backup_id:backup.result.backup_id})).ok,false);
  await tasks.update(handle,'running');await tasks.update(handle,'complete');await tasks.update(handle,'complete',true);
  const engine=require(require('node:path').join(require('node:path').dirname(handlerPath),'../modules/downloads/engine.cjs'));
  const root=fs.mkdtempSync(require('node:path').join(require('node:os').tmpdir(),'host-task-engine-'));
  const lifecycle=toolkitDownloadLifecycle(tasks),bytes=Buffer.from('Host-owned transfer fixture');
  let blockRead=false,readEntered;
  const options={dataDir:root,environment:'',sshHost:'',sshUser:'',sshPort:22,hostTasks:lifecycle,transport:{
   async prepare(){return {size:bytes.length,sha256:require('node:crypto').createHash('sha256').update(bytes).digest('hex')};},
   async read(job,offset,length,signal,write){if(blockRead){readEntered();await new Promise((resolve,reject)=>{if(signal.aborted)return reject(Error('Paused'));signal.addEventListener('abort',()=>reject(Error('Paused')),{once:true});});}write(bytes.subarray(offset,offset+length),0);},
   async cleanup(job){await lifecycle.transition(job.id,job.verified?'complete':'cancelled',true);}
  }};
  let manager=new engine.DownloadManager(options);
  try{
   const job=await manager.create({source:'/fixture/file.bin',folder:require('node:path').join(root,'output'),requestId:'12345678-1234-1234-1234-123456789abc'});
   await manager.runs.get(job.id)?.promise;
   assert.equal(manager.jobs.get(job.id).status,'complete');assert.deepEqual(fs.readFileSync(manager.jobs.get(job.id).destination),bytes);
   assert.equal((await manager.create({source:'/fixture/file.bin',folder:require('node:path').join(root,'output'),requestId:'12345678-1234-1234-1234-123456789abc'})).id,job.id);
   const status=await call(context);assert.equal(status.result.task_inventory.tasks.find(t=>t.task_id===job.id).released,true);
   blockRead=true;let entered=new Promise(resolve=>{readEntered=resolve;});
   const paused=await manager.create({source:'/fixture/paused.bin',folder:require('node:path').join(root,'output')});await entered;
   await manager.action(paused.id,'pause');assert.equal((await call(context)).result.task_inventory.tasks.find(t=>t.task_id===paused.id).state,'paused');
   blockRead=false;await manager.action(paused.id,'resume');await manager.runs.get(paused.id)?.promise;
   assert.equal((await call(context)).result.task_inventory.tasks.find(t=>t.task_id===paused.id).released,true);
   blockRead=true;entered=new Promise(resolve=>{readEntered=resolve;});
   const cancelled=await manager.create({source:'/fixture/cancelled.bin',folder:require('node:path').join(root,'output')});await entered;
   await manager.action(cancelled.id,'cancel');const record=(await call(context)).result.task_inventory.tasks.find(t=>t.task_id===cancelled.id);assert.equal(record.state,'cancelled');assert.equal(record.released,true);
   await manager.close();
   const lostLifecycle={...lifecycle,async create(target,id){await lifecycle.create(target,id);throw Error('Host reply lost after commit');}};
   manager=new engine.DownloadManager({...options,hostTasks:lostLifecycle});
   await assert.rejects(manager.create({source:'/fixture/crash-before-run.bin',folder:require('node:path').join(root,'output')}),/reply lost/);
   const interrupted=[...manager.jobs.values()].find(j=>j.source.endsWith('crash-before-run.bin'));
   assert.equal(interrupted.hostIntent,true);assert.equal((await call(context)).result.task_inventory.tasks.find(t=>t.task_id===interrupted.id).state,'created');
   interrupted.status='complete';interrupted.cleanup_verified=true;manager.save(interrupted);
   await manager.close();
   const restartedTasks=new ToolkitTasks(authority),restarted=toolkitDownloadLifecycle(restartedTasks);
   manager=new engine.DownloadManager({...options,hostTasks:restarted,transport:{...options.transport,async cleanup(job){await restarted.transition(job.id,job.status==='complete'?'complete':'cancelled',true);}}});
   await manager.ready;
   assert.equal(manager.jobs.get(interrupted.id).status,'paused');
   assert.equal((await call(context)).result.task_inventory.tasks.find(t=>t.task_id===interrupted.id).released,false);
   await assert.rejects(lifecycle.transition(interrupted.id,'running'),/owner|revision/);
   blockRead=false;await manager.action(interrupted.id,'resume');await manager.runs.get(interrupted.id)?.promise;
   assert.equal((await call(context)).result.task_inventory.tasks.find(t=>t.task_id===interrupted.id).released,true);
   assert.deepEqual(fs.readFileSync(manager.jobs.get(interrupted.id).destination),bytes);
   restartedTasks.revoke();
  }finally{await manager.close();fs.rmSync(root,{recursive:true,force:true});}
  assert.equal((await send({op:'restore',request_id:require('node:crypto').randomUUID(),backup_id:backup.result.backup_id})).ok,true);
  await assert.rejects(tasks.update(handle,'running'),/epoch/);
  const unknown=await tasks.create();let revoked=false;tasks.signal.addEventListener('abort',()=>{revoked=true;});tasks.revoke();assert.equal(revoked,true);await assert.rejects(tasks.update(unknown,'running'),/owner/);
  assert.equal((await send({op:'restore',request_id:require('node:crypto').randomUUID(),backup_id:backup.result.backup_id})).ok,false);
  console.log('PASS: main-owned handles + real service task records + existing download engine (synthetic transfer/cleanup); restore fencing, unknown lease retained');
 }
 await Promise.all(closed);await new Promise(resolve=>setImmediate(resolve));
 assert.ok(fs.readdirSync('/dev/fd').length<=baseline,'FD leak after task lifecycle and restore');
 authority.revokeAll();assert.equal((await call(context)).ok,false);
 console.log('PASS: SYNTHETIC identity; main authority -> actual socketpair/FD3/SCM_RIGHTS/Security.framework/SQLite; concurrency, revoke, reload, abnormal exit, timeout, FD lifetime');
})().catch(e=>{console.error(e);process.exitCode=1;});
