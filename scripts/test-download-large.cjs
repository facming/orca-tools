'use strict';
// Real local 1 GiB file and checksum; deterministic synthetic remote ranges.
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const {DownloadManager}=require('../plugins/toolkit/modules/downloads/engine.cjs');
const root=fs.mkdtempSync(path.join(os.tmpdir(),'orca-large-download-'));
(async()=>{
 const size=1024*1024*1024,block=Buffer.alloc(4*1024*1024,37),digest=crypto.createHash('sha256');
 for(let i=0;i<size/block.length;i++)digest.update(block);
 const sha256=digest.digest('hex');
 const manager=new DownloadManager({dataDir:path.join(root,'state'),runtime:{platform:'darwin'},transport:{
  async prepare(){return {size,sha256};},
  async read(_job,_offset,length,signal,onData){if(signal.aborted)throw Error('Paused');onData(block.subarray(0,length),0);},
  async cleanup(){}
 }});
 try{
  const row=await manager.create({source:'/fixture/one-gib.bin',folder:path.join(root,'files'),concurrency:8});
  await manager.runs.get(row.id).promise;
  const done=manager.list()[0];assert.equal(done.status,'complete');assert.equal(done.verified,true);assert.equal(fs.statSync(done.destination).size,size);
  await manager.close();const reopened=new DownloadManager(manager.options);try{await reopened.ready;assert.equal(reopened.list()[0].verified,true);}finally{await reopened.close();}
  console.log('PASS: actual macOS 1 GiB destination, eight range workers, SHA-256 publication and restart rehash. Remote ranges synthetic; no VPS/SSH acceptance.');
 }finally{await manager.close();}
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(()=>fs.rmSync(root,{recursive:true,force:true}));
