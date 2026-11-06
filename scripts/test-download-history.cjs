'use strict';
// Real temporary files and SIGKILL recovery; no remote transport or credentials.
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),crypto=require('node:crypto'),assert=require('node:assert/strict'),{fork}=require('node:child_process'),{once}=require('node:events'),{monitorEventLoopDelay,performance}=require('node:perf_hooks');
const {DownloadManager}=require('../plugins/toolkit/modules/downloads/engine.cjs');
const options=root=>({dataDir:path.join(root,'state'),runtime:{platform:'darwin'},transport:{}});
if(process.argv[2]==='child'){
 const manager=new DownloadManager(options(process.argv[3]));manager.ready.then(()=>{process.send({pending:manager.list().filter(j=>j.verificationState==='pending').length,verified:manager.list().filter(j=>j.verified).length});});
}else (async()=>{
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'orca-history-'));
 let manager,child;
 try{
  fs.mkdirSync(path.join(root,'files'),{mode:0o700});fs.mkdirSync(path.join(root,'state','jobs'),{recursive:true,mode:0o700});
  const small=Buffer.alloc(1024,37),large=Buffer.alloc(64*1024*1024,53);
  for(let n=0;n<1003;n++){
   const id=n.toString(16).padStart(32,'0'),data=n<3?large:small,destination=path.join(root,'files',id+'.bin');
   fs.writeFileSync(destination,data,{mode:0o600});
   fs.writeFileSync(path.join(root,'state','jobs',id+'.json'),JSON.stringify({id,source:'/fixture/'+id+'.bin',name:id+'.bin',destination,partial:destination+'.'+id+'.part',concurrency:2,chunk:4*1024*1024,size:data.length,sha256:crypto.createHash('sha256').update(data).digest('hex'),done:[],status:'complete',verified:true,verificationState:'verified',protocol:3}),{mode:0o600});
  }
  child=fork(__filename,['child',root],{stdio:['ignore','ignore','inherit','ipc']});
  const [initial]=await once(child,'message');assert.deepEqual(initial,{pending:1003,verified:0});const exited=once(child,'exit');child.kill('SIGKILL');await exited;child=null;
  const loop=monitorEventLoopDelay({resolution:10});loop.enable();const start=performance.now();
  manager=new DownloadManager(options(root));await manager.ready;const readyMs=performance.now()-start;
  assert.equal(manager.list().filter(j=>j.verified).length,0);assert.equal(manager.list().length,1003);
  await manager.verificationDone;loop.disable();assert.equal(manager.list().filter(j=>j.verificationState==='verified').length,1003);
  console.log(JSON.stringify({result:'PASS',records:1003,largeFiles:3,largeFileMiB:64,readyMs:Math.round(readyMs),totalMs:Math.round(performance.now()-start),eventLoopP99Ms:Math.round(loop.percentile(99)/1e6),eventLoopMaxMs:Math.round(loop.max/1e6),rssMiB:Math.round(process.memoryUsage().rss/1024/1024),recovery:'SIGKILL during pending verification; all hashes recomputed',remote:'none'}));
 }finally{child?.kill('SIGKILL');await manager?.close();fs.rmSync(root,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exitCode=1;});
