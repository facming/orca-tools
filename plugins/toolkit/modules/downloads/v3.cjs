'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {SshTransport}=require('./transport.cjs');
const {privateDirectory,privatePath,writeJson}=require('./local-state.cjs');
const {ToolkitError}=require('../../platform/errors.cjs');
const config=require('../../config-service/client.cjs');
const DOMAIN=Buffer.from('orca-download-revocation-v3\0');
function canonical(v){if(Array.isArray(v))return '['+v.map(canonical).join(',')+']';if(v&&typeof v==='object')return '{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}';return JSON.stringify(v).replace(/[\u007f-\uffff]/g,c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0'));}
function verify(envelope,trust,expected){
 if(!envelope||Object.keys(envelope).sort().join(',')!=='payload,signature'||typeof envelope.payload!=='string'||envelope.payload.length>32768||typeof envelope.signature!=='string')throw new ToolkitError('Invalid signed receipt.');
 const raw=Buffer.from(envelope.payload,'base64'),signature=Buffer.from(envelope.signature,'base64');
 if(raw.toString('base64')!==envelope.payload||signature.length!==64||!crypto.verify(null,Buffer.concat([DOMAIN,raw]),trust.receiptKey,signature))throw new ToolkitError('Receipt signature verification failed.');
 let payload;try{payload=JSON.parse(raw.toString('utf8'));}catch{throw new ToolkitError('Invalid receipt payload.');}
 // All payload fields are ASCII by protocol. Canonical equality also rejects duplicate keys.
 if(canonical(payload)!==raw.toString('utf8'))throw new ToolkitError('Ambiguous receipt serialization.');
 for(const [k,v] of Object.entries(expected))if(payload[k]!==v)throw new ToolkitError('Receipt binding mismatch: '+k);
 const common=['protocol','server_id','endpoint_id','account_id','authority_epoch','task_id','task_public_key_sha256','auth_generation','signing_key_id','action','state','request_id'];
 const fields=payload.action==='register'?[...common,'size','sha256']:[...common,'client_nonce','revocation_commit_seq','read_barrier','issued_at'];
 if(Object.keys(payload).sort().join(',')!==fields.sort().join(','))throw new ToolkitError('Unknown receipt schema.');
 return payload;
}
function trustFor(options){
 const file='/Library/Application Support/OrcaToolkitState/endpoints.json';
 for(let p=file;;p=path.dirname(p)){
  const s=fs.lstatSync(p);if(s.isSymbolicLink()||s.uid!==0||(s.mode&0o022))throw new ToolkitError('Endpoint trust must be administrator-owned.');if(p==='/')break;
 }
 const value=JSON.parse(fs.readFileSync(file,'utf8'))[options.environment];
 if(!value||value.host!==options.sshHost||value.account_id!==options.sshUser||value.port!==options.sshPort||!/^ssh-ed25519 [A-Za-z0-9+/=]+$/.test(value.hostKey)||!/^path:\//.test(value.worktree))throw new ToolkitError('Configure an administrator-verified v3 download endpoint.');
 if(crypto.createPublicKey(value.receiptKey).asymmetricKeyType!=='ed25519')throw new ToolkitError('Ed25519 receipt key required.');
 return value;
}
class V3Transport extends SshTransport{
 constructor(options){super(options);this.endpointOptions=options;this.trust=null;}
 ensureReady(){if(!this.trust){try{this.trust=trustFor(this.endpointOptions);}catch(e){throw e instanceof ToolkitError?e:new ToolkitError('Download authorization endpoint is not installed. Ask the administrator to configure v3 trust.');}}}
 bindings(job){return {protocol:3,server_id:this.trust.server_id,endpoint_id:this.trust.endpoint_id,account_id:this.trust.account_id,authority_epoch:this.trust.authority_epoch,task_id:job.id,task_public_key_sha256:job.publicKeyHash,signing_key_id:this.trust.signing_key_id};}
 async manage(request){
  const payload=Buffer.from(canonical(request)).toString('base64');
  // Fixed installed management client; no uploaded Python or shell program.
  const command='/opt/orca-download/manage '+payload;let handle;
  try{const r=await this.orca(['terminal','create','--worktree',this.trust.worktree,'--title','Download authorization v3','--command',command]);handle=r.terminal?.handle;if(!handle)throw Error();
   for(let n=0;n<60;n++){await this.wait(500);const r=await this.orca(['terminal','read','--terminal',handle,'--limit','30']);const line=r.terminal?.tail?.find(x=>x.startsWith('ORCA_DOWNLOAD_V3='));if(line){if(line.length>65536)throw Error();return JSON.parse(line.slice('ORCA_DOWNLOAD_V3='.length));}}
   throw Error();
  }catch{throw new ToolkitError('Download authorization unavailable; task key retained.');}
  finally{if(handle)await this.orca(['terminal','close','--terminal',handle]).catch(()=>{});}
 }
 async prepare(job){
  if(job.protocol!==3)throw new ToolkitError('Legacy task: manual cleanup required.');
  this.ensureReady();
  job.authorizationState='preparing';this.persist(job);
  const dir=this.directory(job);privateDirectory(this.dataDir);privateDirectory(path.join(this.dataDir,'keys'));privateDirectory(dir);
  const key=path.join(dir,'key');
  if(!fs.existsSync(key))await this.execute(this.runtime.keygen,['-q','-t','ed25519','-N','','-f',key],{env:this.runtime.childEnv,timeout:15000});
  privatePath(key);const pub=fs.lstatSync(key+'.pub');if(!pub.isFile()||pub.uid!==process.getuid()||pub.nlink!==1)throw new ToolkitError('Invalid task public key.');fs.chmodSync(key+'.pub',0o600);
  const publicKey=fs.readFileSync(key+'.pub','utf8').trim().split(/\s+/).slice(0,2).join(' ');
  job.publicKeyHash=crypto.createHash('sha256').update(Buffer.from(publicKey.split(' ')[1],'base64')).digest('hex');
  job.registrationId ||= crypto.randomUUID();job.trust ||= this.trust;
  if(canonical(job.trust)!==canonical(this.trust))throw new ToolkitError('Endpoint trust changed; manual verification required.');
  const request={op:'register',protocol:3,task_id:job.id,public_key:publicKey,source:job.source,request_id:job.registrationId};
  this.persist(job);
  const result=await new Promise((resolve,reject)=>{const child=this.spawn(this.runtime.keygen,['-Y','sign','-f',key,'-n','orca-download-registration-v3'],{env:this.runtime.childEnv,stdio:['pipe','pipe','pipe']});const chunks=[];const timer=setTimeout(()=>child.kill(),15000);child.stdout.on('data',b=>chunks.push(b));child.stderr.resume();child.on('error',reject);child.on('close',code=>{clearTimeout(timer);code===0?resolve({stdout:Buffer.concat(chunks)}):reject(new ToolkitError('Task signing failed.'));});child.stdin.end(canonical(request));});
  request.proof=Buffer.from(result.stdout).toString('base64');
  const meta=verify(await this.manage(request),this.trust,{...this.bindings(job),action:'register',state:'ACTIVE',request_id:job.registrationId});
  if(!/^[a-f0-9]{32}$/.test(meta.auth_generation)||!Number.isSafeInteger(meta.size)||meta.size<0||!/^[a-f0-9]{64}$/.test(meta.sha256))throw new ToolkitError('Invalid registration metadata.');
  job.authGeneration=meta.auth_generation;job.authorizationState='active';this.persist(job);
  const known=path.join(dir,'known_hosts'),pin=job.id+' '+this.trust.hostKey+'\n';
  if(fs.existsSync(known)){privatePath(known);if(fs.readFileSync(known,'utf8')!==pin)throw new ToolkitError('SSH host key changed.');}
  else fs.writeFileSync(known,pin,{mode:0o600,flag:'wx'});
  return meta;
 }
 read(job,...args){this.ensureReady();if(job.protocol!==3||!job.trust||canonical(job.trust)!==canonical(this.trust))return Promise.reject(new ToolkitError('Legacy or changed endpoint: manual verification required.'));return super.read(job,...args);}
 async cleanup(job){
  if(job.protocol!==3)throw new ToolkitError('Legacy task: manual cleanup required; personal authorized_keys was not changed.');
  this.ensureReady();
  if(!job.authGeneration)throw new ToolkitError('Registration result unknown; retry preparation before revocation. Task key retained.');
  if(canonical(job.trust)!==canonical(this.trust))throw new ToolkitError('Endpoint trust changed; task key retained.');
  const request={...this.bindings(job),op:'revoke',auth_generation:job.authGeneration,request_id:crypto.randomUUID(),client_nonce:crypto.randomBytes(32).toString('hex')};
  job.authorizationState='revoking';job.revocationChallenge=request;this.persist(job);
  try{
   const envelope=await this.manage(request);
   const receipt=verify(envelope,this.trust,{...this.bindings(job),action:'revoke',auth_generation:job.authGeneration,request_id:request.request_id,client_nonce:request.client_nonce,state:'REVOKED',read_barrier:'DRAINED'});
   if(!/^[1-9][0-9]*$/.test(receipt.revocation_commit_seq)||BigInt(receipt.revocation_commit_seq)<BigInt(job.revocationSequence||0))throw new ToolkitError('Stale revocation sequence.');
   job.cleanup_verified=true;job.receipt=envelope;job.revocationSequence=receipt.revocation_commit_seq;this.persist(job);
   // Commit proof before deleting only the locally bound, private task files.
   await (this.runtime?.configClient||config).mutate({op:'lease-release',task_id:job.id});
   const dir=this.directory(job);if(fs.existsSync(dir))privatePath(dir,true);
   for(const name of ['key.pub','known_hosts','key']){const file=path.join(dir,name);if(fs.existsSync(file)){privatePath(file);fs.unlinkSync(file);}}
   if(fs.existsSync(dir))fs.rmdirSync(dir);
   job.authorizationState='revoked';this.persist(job);
  }catch(error){job.authorizationState='revocation-review';this.persist(job);throw error;}
 }
 persist(job){writeJson(path.join(this.dataDir,'jobs',job.id+'.json'),job);const fd=fs.openSync(path.join(this.dataDir,'jobs'),'r');try{fs.fsyncSync(fd);}finally{fs.closeSync(fd);}}
}
module.exports={V3Transport,verify,canonical,trustFor};
