'use strict';
const {ToolkitError, publicMessage} = require('../../platform/errors.cjs');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {SshTransport} = require('./transport.cjs');

class Semaphore {
  constructor(limit) { this.limit = limit; this.active = 0; this.waiters = []; }
  async run(signal, operation) {
    await new Promise((resolve, reject) => {
      const ticket = {resolve, reject, signal, abort: null};
      ticket.abort = () => { this.waiters = this.waiters.filter(t => t !== ticket); reject(new ToolkitError('Paused')); };
      if (signal.aborted) return reject(new ToolkitError('Paused'));
      if (this.active < this.limit) { this.active++; resolve(); }
      else { this.waiters.push(ticket); signal.addEventListener('abort', ticket.abort, {once: true}); }
    });
    try { if (signal.aborted) throw new ToolkitError('Paused'); return await operation(); }
    finally {
      this.active--;
      while (this.waiters.length) {
        const next = this.waiters.shift();
        next.signal.removeEventListener('abort', next.abort);
        if (!next.signal.aborted) { this.active++; next.resolve(); break; }
      }
    }
  }
}
const {privateDirectory,privatePath,writeJson,acquireLock,localError}=require('./local-state.cjs');
const {validRemote}=require('./projects.cjs');
function safeName(source) {
  let name = path.posix.basename(source).replace(/[<>:"/\\|?*\x00-\x1f]/g, '_').replace(/[. ]+$/, '');
  if (!name || /^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)/i.test(name)) name = 'download_' + name;
  return name.slice(0, 180);
}
function chunkSize(size, concurrency) {
  // Keep small transfers parallel without creating tiny SSH requests. Large
  // transfers retain bounded ranges for retries and persisted resume offsets.
  return Math.max(256*1024,Math.min(4*1024*1024,Math.ceil(size/concurrency)));
}
class DownloadManager {
  constructor(options) {
    if(options.hostTasks?.signal?.aborted)throw new ToolkitError('Host task session revoked');
    this.options = options; this.root = options.dataDir; this.jobs = new Map(); this.runs = new Map();
    this.live = new Map(); this.slots = new Semaphore(16); this.closed = false;
    this.transport = options.transport || (options.runtime?.platform==='darwin'?new (require('./v3.cjs').V3Transport)(options):new SshTransport(options));
    this.privateFiles=options.runtime?.platform==='darwin';
    if(this.privateFiles){privateDirectory(this.root);privateDirectory(path.join(this.root,'jobs'));}
    else fs.mkdirSync(path.join(this.root, 'jobs'), {recursive: true});
    for (const name of fs.readdirSync(path.join(this.root, 'jobs'))) {
      if (!/^[a-f0-9]{32}\.json$/.test(name)) continue;
      try {
        if(this.privateFiles)privatePath(path.join(this.root,'jobs',name));
        const job = JSON.parse(fs.readFileSync(path.join(this.root, 'jobs', name), 'utf8'));
        if(this.privateFiles&&(!job||job.id+'.json'!==name||!validRemote(job.source)||!path.isAbsolute(job.destination)||!path.isAbsolute(job.partial)||path.dirname(job.partial)!==path.dirname(job.destination)||!path.basename(job.partial).endsWith('.'+job.id+'.part')||!Number.isInteger(job.concurrency)||job.concurrency<1||job.concurrency>16||!Number.isSafeInteger(job.size)||job.size<0||!Number.isSafeInteger(job.chunk)||job.chunk<1||job.chunk>8*1024*1024||(job.sha256&&!/^[a-f0-9]{64}$/.test(job.sha256))||!Array.isArray(job.done)||job.done.some(i=>!Number.isInteger(i)||i<0||i>=Math.ceil(job.size/job.chunk))))throw Error();
        if(this.privateFiles&&job.protocol!==3){job.authorizationState='legacy-manual';job.cleanupWarning='Legacy task: manual cleanup required.';}
        if(job.status==='complete'&&job.cleanupWarning)this.options.onConnection?.('error');
        if (job.error) job.error = 'Previous download failed. Check the destination and remote connection before retrying.';
        if (!['complete','cancelled'].includes(job.status)) job.status = 'paused';
        this.jobs.set(job.id, job);
      } catch {if(this.privateFiles)throw new ToolkitError('Saved download state is invalid or unsafe. Inspect it before continuing.');}
    }
    if(this.privateFiles)this.releaseLock=acquireLock(this.root);
    this.hostRevoked=()=>{this.close().catch(()=>{});};
    options.hostTasks?.signal?.addEventListener('abort',this.hostRevoked,{once:true});
  }
  save(job) { const file=path.join(this.root,'jobs',job.id+'.json');if(this.privateFiles&&fs.existsSync(file))privatePath(file);writeJson(file,job); }
  list() {
    return [...this.jobs.values()].reverse().map(job => {
      const live = this.live.get(job.id), done = new Set(job.done);
      let bytes = [...done].reduce((n,i) => n + Math.min(job.chunk, job.size - i * job.chunk), 0);
      if (live) bytes += [...live.ranges.values()].reduce((a,b) => a+b, 0);
      const speed = live ? Math.max(0, (bytes-live.initialBytes) / Math.max(1,(Date.now()-live.start)/1000)) : 0;
      return {id:job.id, source:job.source, name:job.name, destination:job.destination, status:job.status,
        size:job.size || 0, bytes, speed, percent:job.status==='complete'?100:job.size?Math.min(100,bytes/job.size*100):0,
        concurrency:job.concurrency, connections:live?.ranges.size||0, retries:job.retries||0,
        error:job.error||null, verified:job.verified||false, cleanupWarning:job.cleanupWarning||null,authorizationState:job.authorizationState||null};
    });
  }
  async create({source, folder, concurrency=12}) {
    if (this.closed) throw new ToolkitError('Download manager is closing');
    this.transport.ensureReady?.();
    if (!validRemote(source))
      throw new ToolkitError('Enter an absolute remote file path');
    if (typeof folder!=='string' || !path.isAbsolute(folder) || folder.length>(this.privateFiles?4096:220)||/[\x00-\x1f]/.test(folder)) throw new ToolkitError('Enter an absolute local folder path');
    if (!Number.isInteger(concurrency) || concurrency<1 || concurrency>16) throw new ToolkitError('Choose 1 to 16 connections');
    if ([...this.jobs.values()].filter(j=>!['complete','cancelled'].includes(j.status)).length>=20) throw new ToolkitError('Pause or finish an existing download first');
    folder=path.resolve(folder);try{fs.mkdirSync(folder,{recursive:true});fs.accessSync(folder,fs.constants.W_OK|fs.constants.X_OK);}catch(error){throw localError(error);}
    const id=this.options.hostTasks?await this.options.hostTasks.create(Object.fromEntries(['environment','sshHost','sshUser','sshPort'].map(key=>[key,this.options[key]]))):crypto.randomUUID().replaceAll('-',''), name=safeName(source), ext=path.extname(name), stem=name.slice(0,name.length-ext.length);
    let destination=path.join(folder,name), suffix=1;
    while(fs.existsSync(destination)||[...this.jobs.values()].some(j=>j.destination===destination&&j.status!=='cancelled')) destination=path.join(folder,`${stem} (${++suffix})${ext}`);
    const job={id,source,name:path.basename(destination),destination,partial:path.join(folder,`.${name}.${id}.part`),
      concurrency,chunk:4*1024*1024,status:'preparing',size:0,done:[],retries:0};
    if(this.options.hostTasks)job.protocol=3;
    if(this.privateFiles&&!this.options.transport&&!this.options.hostTasks){job.protocol=3;await (this.options.runtime?.configClient||require('../../config-service/client.cjs')).mutate({op:'lease-acquire',task_id:id,target:Object.fromEntries(['environment','sshHost','sshUser','sshPort'].map(k=>[k,this.options[k]]))});}
    this.jobs.set(id,job); this.save(job);
    await this.start(job); return this.list().find(j=>j.id===id);
  }
  start(job) {
    if(this.closed)throw new ToolkitError('Download manager is closing');
    if(this.privateFiles&&!this.options.transport&&job.protocol!==3)throw new ToolkitError('Legacy task: manual cleanup required.');
    if (this.runs.has(job.id)) throw new ToolkitError('This download is already running');
    const controller=new AbortController();
    const promise=(async()=>{await this.options.hostTasks?.transition(job.id,'running');if(controller.signal.aborted)throw new ToolkitError('Paused');await this.run(job,controller.signal);})().catch(async error=>{
      job.status=controller.signal.aborted?'paused':'error';if(!controller.signal.aborted)this.options.onConnection?.('error');job.error=controller.signal.aborted?null:publicMessage(localError(error), 'Download failed. Check file permissions and the remote connection.');this.save(job);
      await this.options.hostTasks?.transition(job.id,job.status).catch(()=>{});
    }).finally(()=>{this.live.delete(job.id);this.runs.delete(job.id);});
    this.runs.set(job.id,{controller,promise});
  }
  async run(job,signal) {
    job.error=null;
    if (!job.sha256) {
      job.status='preparing';job.cleanupWarning='Transfer access cleanup pending; use Clear access.';this.save(job);
      const meta=await this.transport.prepare(job);
      const handle=fs.openSync(job.partial,'wx',0o600);
      try{fs.ftruncateSync(handle,meta.size);fs.fsyncSync(handle);}catch(error){fs.closeSync(handle);fs.unlinkSync(job.partial);throw error;}
      fs.closeSync(handle);job.size=meta.size;job.sha256=meta.sha256;job.fingerprint=meta.fingerprint;
      job.chunk=chunkSize(job.size,job.concurrency);
      this.save(job);
    }
    if(signal.aborted)throw new ToolkitError('Paused');
    if(job.publishing&&fs.existsSync(job.destination)){
      const info=fs.lstatSync(job.destination);
      if(!info.isFile()||info.dev!==job.publishing.dev||info.ino!==job.publishing.ino)throw new ToolkitError('Destination already exists. Existing files are never overwritten.');
      const digest=crypto.createHash('sha256');for await(const block of fs.createReadStream(job.destination)){if(signal.aborted)throw new ToolkitError('Paused');digest.update(block);}
      if(digest.digest('hex')!==job.sha256)throw new ToolkitError('Published file checksum failed. Existing files are retained.');
      if(fs.existsSync(job.partial)){const part=fs.lstatSync(job.partial);if(part.dev!==info.dev||part.ino!==info.ino)throw new ToolkitError('Partial file changed. Existing files are retained.');fs.unlinkSync(job.partial);}
      return this.complete(job);
    }
    if(!fs.existsSync(job.partial))throw new ToolkitError('Partial file is missing. Create a new download.');
    if(this.privateFiles)privatePath(job.partial);
    const fd=fs.openSync(job.partial,fs.constants.O_RDWR|(this.privateFiles?fs.constants.O_NOFOLLOW:0)), done=new Set(job.done);
    let results;try{
    // Recheck persisted ranges after restart; legacy jobs retain the final whole-file check.
    job.chunkHashes ||= {};
    for(const index of done){if(!job.chunkHashes[index])continue;const data=Buffer.alloc(Math.min(job.chunk,job.size-index*job.chunk));fs.readSync(fd,data,0,data.length,index*job.chunk);if(crypto.createHash('sha256').update(data).digest('hex')!==job.chunkHashes[index]){done.delete(index);delete job.chunkHashes[index];}}
    job.done=[...done];
    if(fs.fstatSync(fd).size!==job.size)throw new ToolkitError('Partial file size changed. Create a new download.');
    const live={ranges:new Map(),start:Date.now(),initialBytes:[...done].reduce((n,i)=>n+Math.min(job.chunk,job.size-i*job.chunk),0)};
    this.live.set(job.id,live);job.status='downloading';job.error=null;this.save(job);
    const queue=Array.from({length:Math.ceil(job.size/job.chunk)},(_,i)=>i).filter(i=>!done.has(i));let cursor=0,failed=false;
    const worker=async()=>{
      while(!signal.aborted&&!failed&&cursor<queue.length){const index=queue[cursor++],offset=index*job.chunk,length=Math.min(job.chunk,job.size-offset);let success=false,lastError;
        for(let attempt=0;attempt<6&&!signal.aborted;attempt++){
          try{
            await this.slots.run(signal,async()=>{
              live.ranges.set(index,0);let received=0;const digest=crypto.createHash('sha256');
              try{await this.transport.read(job,offset,length,signal,(data,position)=>{
                if(position!==received||position+data.length>length)throw new ToolkitError('Invalid byte range');
                let written=0;while(written<data.length){const count=fs.writeSync(fd,data,written,data.length-written,offset+position+written);if(!count)throw new ToolkitError('Unable to write download range.');written+=count;}digest.update(data);received+=data.length;live.ranges.set(index,received);
              });if(received!==length)throw new ToolkitError('Incomplete byte range');job.chunkHashes[index]=digest.digest('hex');}finally{live.ranges.delete(index);}
            });success=true;break;
          }catch(error){lastError=error;if(signal.aborted)break;job.retries++;this.save(job);
            await new Promise(resolve=>{const timer=setTimeout(finish,Math.min(10000,1000*(attempt+1)));function finish(){clearTimeout(timer);signal.removeEventListener('abort',finish);resolve();}signal.addEventListener('abort',finish,{once:true});});
          }
        }
        if(signal.aborted)return;
        if(!success){failed=true;throw lastError||new ToolkitError('Transfer interrupted');}
        fs.fsyncSync(fd);done.add(index);job.done=[...done];this.save(job);
      }
    };
    results=await Promise.allSettled(Array.from({length:job.concurrency},worker));}finally{fs.closeSync(fd);}
    if(signal.aborted)throw new ToolkitError('Paused');
    const error=results.find(r=>r.status==='rejected');if(error)throw error.reason;
    job.status='verifying';this.save(job);
    const digest=crypto.createHash('sha256');for await(const block of fs.createReadStream(job.partial)){if(signal.aborted)throw new ToolkitError('Paused');digest.update(block);}
    if(digest.digest('hex')!==job.sha256)throw new ToolkitError('Checksum failed. The partial file has been retained. Start a new download.');
    const info=fs.statSync(job.partial);job.publishing={dev:info.dev,ino:info.ino};this.save(job);
    // Hard-link with exclusive destination semantics: never overwrite a file which appeared during transfer.
    try{fs.linkSync(job.partial,job.destination);}catch(error){
      if(!['EPERM','ENOTSUP','EACCES'].includes(error.code))throw error;
      fs.copyFileSync(job.partial,job.destination,fs.constants.COPYFILE_EXCL);
    }
    fs.unlinkSync(job.partial);await this.complete(job);
  }
  async complete(job){
    job.verified=true;job.status='complete';delete job.publishing;this.save(job);
    try{await this.options.hostTasks?.transition(job.id,'complete');await this.transport.cleanup(job);job.cleanupWarning=null;this.save(job);}catch{job.cleanupWarning='Transfer access cleanup pending; use Clear access.';this.save(job);}
    await this.options.onComplete?.(job).catch(()=>{});
  }
  async action(id,action) {
    const job=this.jobs.get(id);if(!job)throw new ToolkitError('Download not found');
    if(action==='pause'){
      const run=this.runs.get(id);if(run){run.controller.abort();await run.promise;}
    }else if(action==='resume'){
      if(!['paused','error'].includes(job.status))throw new ToolkitError('This download cannot be resumed');
      if(this.runs.has(id))throw new ToolkitError('Wait for the current operation to finish');
      await this.start(job);
    }else if(action==='cancel'){
      if(job.status==='complete')throw new ToolkitError('Completed files are kept');
      await this.action(id,'pause');
      await this.options.hostTasks?.transition(job.id,'cancelled');
      try{await this.transport.cleanup(job);job.cleanupWarning=null;}catch{job.cleanupWarning='Transfer access cleanup pending; use Clear access.';}
      fs.rmSync(job.partial,{force:true});job.status='cancelled';this.save(job);
    }else if(action==='cleanup'){
      if(this.runs.has(id))throw new ToolkitError('Pause the download before clearing access');
      if(!['complete','cancelled'].includes(job.status))await this.options.hostTasks?.transition(job.id,'cancelled');
      await this.transport.cleanup(job);job.cleanupWarning=null;this.save(job);
      if(!['complete','cancelled'].includes(job.status)){job.status='cancelled';fs.rmSync(job.partial,{force:true});this.save(job);}
    }else throw new ToolkitError('Unknown action');
    return this.list().find(j=>j.id===id);
  }
  async pauseAll(){await Promise.all([...this.runs.keys()].map(id=>this.action(id,'pause')));}
  async close(){this.closed=true;this.options.hostTasks?.signal?.removeEventListener('abort',this.hostRevoked);await this.pauseAll();if(this.releaseLock){this.releaseLock();this.releaseLock=null;}}
}
module.exports={DownloadManager,Semaphore,safeName,chunkSize};
