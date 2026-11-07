'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),crypto=require('node:crypto'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {V3Transport,verify,canonical}=require('./v3.cjs'),{SshTransport}=require('./transport.cjs');
const config=require('../../config-service/client.cjs');
function fixture(t){
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'orca-v3-'));t.after(()=>fs.rmSync(root,{force:true,recursive:true}));
 const {privateKey,publicKey}=crypto.generateKeyPairSync('ed25519');
 const trust={server_id:'server',endpoint_id:'endpoint',account_id:'reader',authority_epoch:'current',signing_key_id:'one',receiptKey:publicKey.export({type:'spki',format:'pem'})};
 const tr=Object.assign(Object.create(V3Transport.prototype),{dataDir:root,trust});
 const job={id:'a'.repeat(32),protocol:3,publicKeyHash:'b'.repeat(64),authGeneration:'c'.repeat(32),trust};
 const expected={...tr.bindings(job),auth_generation:job.authGeneration,action:'revoke',state:'REVOKED',request_id:'request',client_nonce:'d'.repeat(64),read_barrier:'DRAINED'};
 const payload={...expected,revocation_commit_seq:'1',issued_at:'1'};
 const sign=value=>{const raw=Buffer.from(canonical(value));return {payload:raw.toString('base64'),signature:crypto.sign(null,Buffer.concat([Buffer.from('orca-download-revocation-v3\0'),raw]),privateKey).toString('base64')};};
 fs.mkdirSync(path.join(root,'jobs'),{mode:0o700});fs.mkdirSync(path.join(root,'keys'),{mode:0o700});fs.mkdirSync(tr.directory(job),{mode:0o700});
 for(const name of ['key','key.pub','known_hosts'])fs.writeFileSync(path.join(tr.directory(job),name),'synthetic',{mode:0o600});
 return {tr,job,trust,payload,expected,sign,key:path.join(tr.directory(job),'key')};
}
test('v3 Ed25519 signatures bind every task, endpoint, nonce, epoch and drain field',t=>{
 const f=fixture(t);assert.deepEqual(verify(f.sign(f.payload),f.trust,f.expected),f.payload);
 for(const field of Object.keys(f.expected))assert.throws(()=>verify(f.sign({...f.payload,[field]:'wrong'}),f.trust,f.expected),/binding/);
 const fake=f.sign(f.payload);fake.signature=Buffer.alloc(64).toString('base64');assert.throws(()=>verify(fake,f.trust,f.expected),/signature/);
 assert.throws(()=>verify(f.sign({...f.payload,extra:'unexpected'}),f.trust,f.expected),/schema/);
 assert.throws(()=>verify('KEY_REMOVED:'+f.job.id,f.trust,f.expected));
 assert.throws(()=>verify(f.sign(f.payload),f.trust,{...f.expected,client_nonce:'e'.repeat(64)}),/binding/);
});
test('v3 cleanup persists proof before deleting key and releasing target lease',async t=>{
 const f=fixture(t);let released=false;
 t.mock.method(config,'mutate',request=>{assert.equal(request.op,'lease-release');assert.ok(fs.existsSync(f.key));const stored=JSON.parse(fs.readFileSync(path.join(f.tr.dataDir,'jobs',f.job.id+'.json')));assert.equal(stored.cleanup_verified,true);released=true;});
 f.tr.manage=async r=>f.sign({...f.payload,request_id:r.request_id,client_nonce:r.client_nonce});
 await f.tr.cleanup(f.job);assert.equal(released,true);assert.equal(fs.existsSync(f.key),false);assert.equal(f.job.authorizationState,'revoked');
});
test('v3 forged, pending, stale, unreachable, persistence and lease failures retain private key',async t=>{
 for(const mode of ['forged','pending','stale','unreachable','persist','lease']){
  const f=fixture(t);f.job.revocationSequence=mode==='stale'?'2':'0';
  f.tr.manage=async r=>{if(mode==='unreachable')throw Error('offline');if(mode==='pending')return {state:'REVOKING'};return f.sign({...f.payload,request_id:r.request_id,client_nonce:mode==='forged'?'bad':r.client_nonce});};
  t.mock.method(config,'mutate',()=>{if(mode==='lease')throw Error('unavailable');});
  if(mode==='persist'){const original=f.tr.persist;f.tr.persist=function(job){if(job.cleanup_verified)throw Error('disk full');return original.call(this,job);};}
  await assert.rejects(f.tr.cleanup(f.job));assert.ok(fs.existsSync(f.key),mode);
 }
});
test('macOS legacy preparation and both string cleanup paths fail closed',async t=>{
 const f=fixture(t);const transport=new SshTransport({dataDir:f.tr.dataDir,runtime:{platform:'darwin'},environment:'test',sshHost:'100.64.0.20',sshUser:'reader',sshPort:22});
 await assert.rejects(transport.prepare(f.job),/v3/);await assert.rejects(transport.cleanup(f.job),/manual/);await assert.rejects(transport.cleanupViaOrca(f.job),/manual/);
 f.job.protocol=2;await assert.rejects(f.tr.cleanup(f.job),/Legacy/);assert.ok(fs.existsSync(f.key));
});
test('canonical proof encoding preserves Unicode consistently with Python',()=>{
 assert.equal(canonical({source:'/project/中文😀',protocol:3}),'{"protocol":3,"source":"/project/\\u4e2d\\u6587\\ud83d\\ude00"}');
});
test('v3 real SSHSIG registration, signed metadata, retry and cleanup against authority SQLite',async t=>{
 const cp=require('node:child_process'),{promisify}=require('node:util');
 const root=fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(),'orca-v3-authority-')));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
 const state=path.join(root,'authority'),dataDir=path.join(root,'local');fs.mkdirSync(state,{mode:0o700});fs.mkdirSync(dataDir,{mode:0o700});fs.mkdirSync(path.join(dataDir,'jobs'),{mode:0o700});
 const source=path.join(root,'中文.dat');fs.writeFileSync(source,'PAYLOAD');
 const shared=path.resolve(__dirname,'../../config-service'),authority=path.resolve(__dirname,'../../../../services/download-authorization');
 const script=`import sys,os\nsys.path[:0]=sys.argv[2:4]\nfrom authority import Authority\nfrom secure_store import loads,dumps\np={'owners':{str(os.getuid()):[sys.argv[4]]},'reader_uid':os.getuid()+1,'lookup_uid':os.getuid()+2,'server_id':'server','endpoint_id':'endpoint','account_id':'reader','signing_key_id':'one','dispatcher':'/opt/orca-download/dispatch'}\ns=Authority(sys.argv[1],p,sys.argv[5]=='init')\ntry:\n print(dumps({'receiptKey':s.verify_key,'authority_epoch':s.meta('epoch')} if sys.argv[5]=='init' else s.handle(os.getuid(),loads(sys.stdin.buffer.read()))))\nfinally:s.close()`;
 const invoke=(op,r)=>JSON.parse(cp.execFileSync('python3',['-c',script,state,shared,authority,root,op],{input:r?canonical(r):'',encoding:'utf8'}));
 const trust={...invoke('init'),server_id:'server',endpoint_id:'endpoint',account_id:'reader',signing_key_id:'one',hostKey:'ssh-ed25519 U1lOVEhFVElD',host:'100.64.0.20',port:22,worktree:'path:/test'};
 const tr=new SshTransport({dataDir,runtime:{platform:'darwin',keygen:'/usr/bin/ssh-keygen',childEnv:{PATH:process.env.PATH}},environment:'test',sshHost:'100.64.0.20',sshUser:'reader',sshPort:22,execute:promisify(cp.execFile),spawn:cp.spawn});Object.setPrototypeOf(tr,V3Transport.prototype);tr.trust=trust;tr.manage=async r=>invoke('request',r);
 const job={id:crypto.randomUUID().replaceAll('-',''),protocol:3,source};
 const manage=tr.manage;tr.manage=async request=>{await manage(request);throw Error('registration response lost');};
 await assert.rejects(tr.prepare(job),/response lost/);Object.assign(job,JSON.parse(fs.readFileSync(path.join(dataDir,'jobs',job.id+'.json'))));tr.manage=manage;
 const meta=await tr.prepare(job);assert.equal(meta.size,7);assert.equal(meta.sha256,crypto.createHash('sha256').update('PAYLOAD').digest('hex'));assert.equal(job.authorizationState,'active');
 assert.deepEqual(await tr.prepare(job),meta);
 t.mock.method(config,'mutate',()=>({released:job.id}));await tr.cleanup(job);assert.equal(job.cleanup_verified,true);assert.equal(fs.existsSync(path.join(tr.directory(job),'key')),false);
 assert.equal(invoke('request',{op:'status',task_id:job.id}).state,'REVOKED');
 // Exercise v3 through the actual manager lifecycle, including process-style
 // reconstruction. Only range transport is synthetic; registration/receipts use
 // real SSHSIG, Ed25519 and persistent authority SQLite above.
 const {DownloadManager}=require('./engine.cjs');
 const options={dataDir:path.join(root,'manager'),runtime:tr.runtime,environment:'test',sshHost:'100.64.0.20',sshUser:'reader',sshPort:22,hostTasks:{async create(){},async transition(){}}};
 const manager=new DownloadManager(options);let entered;
 const reading=new Promise(resolve=>{entered=resolve;});
 Object.assign(manager.transport,{trust,manage:tr.manage,read:async(_job,_offset,_length,signal)=>{entered();await new Promise((_,reject)=>signal.addEventListener('abort',()=>reject(Error('Paused')),{once:true}));}});
 const created=await manager.create({source,folder:path.join(root,'output'),concurrency:1});await reading;
 await manager.action(created.id,'pause');assert.equal(manager.jobs.get(created.id).status,'paused');
 assert.ok(fs.existsSync(path.join(manager.transport.directory(manager.jobs.get(created.id)),'key')));await manager.close();
 const resumed=new DownloadManager(options);t.after(()=>resumed.close());
 Object.assign(resumed.transport,{trust,manage:tr.manage,read:async(_job,offset,length,_signal,onData)=>onData(fs.readFileSync(source).subarray(offset,offset+length),0)});
 await resumed.action(created.id,'resume');await resumed.runs.get(created.id)?.promise;
 const complete=resumed.jobs.get(created.id);assert.equal(complete.status,'complete');assert.equal(complete.cleanup_verified,true);assert.equal(complete.authorizationState,'revoked');
 assert.equal(fs.readFileSync(complete.destination,'utf8'),'PAYLOAD');assert.equal(fs.existsSync(path.join(resumed.transport.directory(complete),'key')),false);
 assert.equal(invoke('request',{op:'status',task_id:complete.id}).state,'REVOKED');await resumed.close();

});

test('async Host lease release must finish before deleting task keys; rejection retains them',async t=>{
 for(const reject of [false,true]){
  const f=fixture(t);let release;
  const entered=new Promise(resolve=>{f.tr.runtime={configClient:{mutate:async request=>{assert.equal(request.op,'lease-release');resolve();return new Promise((ok,bad)=>{release=()=>reject?bad(Error('Host offline')):ok({released:f.job.id});});}}};});
  t.mock.method(config,'mutate',()=>{throw Error('direct Bridge fallback forbidden');});
  f.tr.manage=async r=>f.sign({...f.payload,request_id:r.request_id,client_nonce:r.client_nonce});
  const cleanup=f.tr.cleanup(f.job);await entered;assert.ok(fs.existsSync(f.key));release();
  if(reject){await assert.rejects(cleanup,/Host offline/);assert.ok(fs.existsSync(f.key));}else{await cleanup;assert.equal(fs.existsSync(f.key),false);}
 }
});

test('restart re-verifies persisted receipt and retries Host release before deleting keys without network',async t=>{
 const f=fixture(t);let failed=true;
 f.tr.endpointOptions={hostTasks:{async transition(id,state,released){assert.equal(id,f.job.id);assert.equal(state,'cancelled');assert.equal(released,true);assert.ok(fs.existsSync(f.key));if(failed)throw Error('Host response lost');}}};
 f.tr.manage=async r=>f.sign({...f.payload,request_id:r.request_id,client_nonce:r.client_nonce});
 await assert.rejects(f.tr.cleanup(f.job),/Host response lost/);
 const saved=JSON.parse(fs.readFileSync(path.join(f.tr.dataDir,'jobs',f.job.id+'.json')));
 f.tr.manage=async()=>{throw Error('offline; persisted proof must suffice');};
 const forged=structuredClone(saved);forged.receipt.signature=Buffer.alloc(64).toString('base64');
 await assert.rejects(f.tr.cleanup(forged),/signature/);assert.ok(fs.existsSync(f.key));
 failed=false;await f.tr.cleanup(saved);assert.equal(fs.existsSync(f.key),false);
});
test('lost remote revoke reply reuses the same nonce and request id, ignoring JSON revoked flags',async t=>{
 const f=fixture(t);let original;
 f.job.cleanup_verified=true;f.job.authorizationState='revoked';
 f.tr.manage=async r=>{original=structuredClone(r);throw Error('reply lost');};
 await assert.rejects(f.tr.cleanup(f.job),/reply lost/);assert.ok(fs.existsSync(f.key));
 f.tr.manage=async r=>{assert.deepEqual(r,original);return f.sign({...f.payload,request_id:r.request_id,client_nonce:r.client_nonce});};
 t.mock.method(config,'mutate',()=>({}));await f.tr.cleanup(f.job);assert.equal(fs.existsSync(f.key),false);
});

test('remote revocation followed by local key deletion failure retries verified proof offline',async t=>{
 const f=fixture(t);t.mock.method(config,'mutate',()=>({}));
 f.tr.manage=async r=>f.sign({...f.payload,request_id:r.request_id,client_nonce:r.client_nonce});
 const unlink=fs.unlinkSync,mock=t.mock.method(fs,'unlinkSync',file=>{if(file===f.key)throw Object.assign(Error('local denial'),{code:'EACCES'});return unlink(file);});
 await assert.rejects(f.tr.cleanup(f.job),/local denial/);assert.ok(fs.existsSync(f.key));assert.ok(f.job.receipt);
 mock.mock.restore();f.tr.manage=async()=>{throw Error('network must not be used');};
 await f.tr.cleanup(f.job);assert.equal(f.job.authorizationState,'revoked');assert.equal(fs.existsSync(f.key),false);
});
test('authorization retries lost replies with the identical registration and stops on denial',async()=>{
 const tr=Object.assign(Object.create(V3Transport.prototype),{trust:{worktree:'path:/project'},wait:async()=>{}});
 const commands=[];let disconnected=true;
 tr.orca=async args=>{
  if(args[1]==='create'){commands.push(args.at(-1));return {terminal:{handle:'transfer'}};}
  if(args[1]==='read'&&disconnected){disconnected=false;throw Object.assign(Error('connection lost'),{retryable:true});}
  if(args[1]==='read')return {terminal:{tail:['ORCA_DOWNLOAD_V3={"payload":"receipt","signature":"signed"}']}};
  return {};
 };
 assert.deepEqual(await tr.manage({op:'register',request_id:'same-request'}),{payload:'receipt',signature:'signed'});
 assert.equal(new Set(commands).size,1);
 tr.orca=async args=>args[1]==='create'?{terminal:{handle:'denied'}}:args[1]==='read'?{terminal:{tail:['PermissionError: rejected']}}:{};
 await assert.rejects(tr.manage({op:'register'}),/authorization rejected/);
 const controller=new AbortController();controller.abort();
 await assert.rejects(tr.manage({op:'register'},{signal:controller.signal}),/abort/i);
});

test('remote authorization timeout traceback retries instead of becoming a permanent denial',async()=>{
 const tr=Object.assign(Object.create(V3Transport.prototype),{trust:{worktree:'path:/project'},wait:async()=>{}});
 let timeout=true;
 tr.orca=async args=>{
  if(args[1]==='create')return {terminal:{handle:'auth'}};
  if(args[1]==='read'){const tail=timeout?['Traceback (most recent call last):','TimeoutError: timed out']:['ORCA_DOWNLOAD_V3={"payload":"receipt","signature":"signed"}'];timeout=false;return {terminal:{tail}};}
  return {};
 };
 assert.deepEqual(await tr.manage({op:'register',request_id:'same-request'}),{payload:'receipt',signature:'signed'});
});
