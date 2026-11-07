'use strict';
// Real temporary files/processes, synthetic history; no Host leases or remote access.
// --large: 1,000 small files + eight 1 GiB files. Default retains the 7B-15 workload.
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),crypto=require('node:crypto'),assert=require('node:assert/strict'),{fork}=require('node:child_process'),{once}=require('node:events'),{monitorEventLoopDelay,performance}=require('node:perf_hooks');
const {setTimeout:delay}=require('node:timers/promises');
const {DownloadManager}=require('../plugins/toolkit/modules/downloads/engine.cjs');
const options=root=>({dataDir:path.join(root,'state'),runtime:{platform:'darwin'},transport:{}});
if(process.argv[2]==='child'){
 const manager=new DownloadManager(options(process.argv[3]));
 process.once('message',async()=>{
  const start=performance.now();await manager.close();
  process.send({closeMs:Math.round(performance.now()-start)},()=>process.disconnect());
 });
 manager.ready.then(()=>process.send({pending:manager.list().filter(j=>j.verificationState==='pending').length,verified:manager.list().filter(j=>j.verified).length}));
}else (async()=>{
 assert.ok(process.argv.slice(2).every(arg=>arg==='--large'),'Usage: node scripts/test-download-history.cjs [--large]');
 const largeMode=process.argv.includes('--large'),largeCount=largeMode?8:3,largeMiB=largeMode?1024:64,records=1000+largeCount;
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'orca-history-'));
 let manager,child,sampler,loop;
 try{
  const disk=fs.statfsSync(root),logicalBytes=largeCount*largeMiB*1024*1024+1000*1024;
  assert.ok(disk.bavail*disk.bsize>logicalBytes+2*1024**3,'Need workload size plus 2 GiB free; no files created');
  fs.mkdirSync(path.join(root,'files'),{mode:0o700});fs.mkdirSync(path.join(root,'state','jobs'),{recursive:true,mode:0o700});
  const small=Buffer.alloc(1024,37),block=crypto.randomBytes(4*1024*1024);
  for(let n=0;n<records;n++){
   const id=n.toString(16).padStart(32,'0'),size=n<largeCount?largeMiB*1024*1024:small.length,destination=path.join(root,'files',id+'.bin');
   const fd=fs.openSync(destination,'wx',0o600),digest=crypto.createHash('sha256');
   try{for(let written=0;written<size;){const data=n<largeCount?block.subarray(0,Math.min(block.length,size-written)):small;let offset=0;while(offset<data.length)offset+=fs.writeSync(fd,data,offset,data.length-offset);digest.update(data);written+=data.length;}}finally{fs.closeSync(fd);}
   fs.writeFileSync(path.join(root,'state','jobs',id+'.json'),JSON.stringify({id,source:'/fixture/'+id+'.bin',name:id+'.bin',destination,partial:destination+'.'+id+'.part',concurrency:2,chunk:4*1024*1024,size,sha256:digest.digest('hex'),done:[],status:'complete',verified:true,verificationState:'verified',protocol:3}),{mode:0o600});
  }
  let closeMs,exitMs;
  for(const graceful of [true,false]){
   child=fork(__filename,['child',root],{stdio:['ignore','ignore','inherit','ipc']});
   const [initial]=await once(child,'message',{signal:AbortSignal.timeout(30000)});assert.deepEqual(initial,{pending:records,verified:0});
   await delay(250); // Interrupt an active large-file read, not just metadata loading.
   const start=performance.now(),exited=once(child,'exit',{signal:AbortSignal.timeout(10000)});
   if(graceful){const reply=once(child,'message',{signal:AbortSignal.timeout(10000)});child.send('close');closeMs=(await reply)[0].closeMs;}
   else child.kill('SIGKILL');
   const [code,signal]=await exited;
   if(graceful){assert.equal(code,0);exitMs=Math.round(performance.now()-start);}else assert.equal(signal,'SIGKILL');
   child=null;
  }
  loop=monitorEventLoopDelay({resolution:10});loop.enable();const start=performance.now(),cpuStart=process.cpuUsage();
  let peakRss=process.memoryUsage().rss,listMaxMs=0,listSamples=0;
  manager=new DownloadManager(options(root));await manager.ready;const readyMs=performance.now()-start;
  assert.equal(manager.list().filter(j=>j.verified).length,0);assert.equal(manager.list().length,records);
  sampler=setInterval(()=>{const before=performance.now();manager.list();listMaxMs=Math.max(listMaxMs,performance.now()-before);listSamples++;peakRss=Math.max(peakRss,process.memoryUsage().rss);},100);
  await manager.verificationDone;clearInterval(sampler);loop.disable();assert.equal(manager.list().filter(j=>j.verificationState==='verified').length,records);
  const totalMs=performance.now()-start,cpu=process.cpuUsage(cpuStart);
  console.log(JSON.stringify({result:'PASS',platform:os.platform(),arch:os.arch(),osRelease:os.release(),node:process.version,records,largeFiles:largeCount,largeFileMiB:largeMiB,logicalBytes,readyMs:Math.round(readyMs),totalMs:Math.round(totalMs),logicalMiBPerSecond:+(logicalBytes/1024**2/(totalMs/1000)).toFixed(2),physicalDiskIO:'not measured; filesystem cache may satisfy reads',cpuMs:Math.round((cpu.user+cpu.system)/1000),eventLoopP99Ms:Math.round(loop.percentile(99)/1e6),eventLoopMaxMs:Math.round(loop.max/1e6),peakRssMiB:Math.round(peakRss/1024**2),listSamples,listMaxMs:Math.round(listMaxMs),closeMs,childExitMs:exitMs,recovery:'graceful close and SIGKILL during active verification; restart resets all trust and recomputes every full hash',remote:'none',hostLeases:'none'}));
 }finally{clearInterval(sampler);loop?.disable();if(child&&child.exitCode===null&&child.signalCode===null){const exited=once(child,'exit');child.kill('SIGKILL');await exited;}await manager?.close();fs.rmSync(root,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exitCode=1;});
