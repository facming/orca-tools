'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),crypto=require('node:crypto'),cp=require('node:child_process'),zlib=require('node:zlib');
const {EventEmitter}=require('node:events'),{PassThrough}=require('node:stream');
const {SshTransport}=require('./transport.cjs'),{DownloadManager}=require('./engine.cjs'),{RemoteBrowser}=require('../../remote-browser.cjs'),{startServer}=require('../../server.cjs');
const macOnly={skip:process.platform==='win32'&&'requires POSIX modes and signals'};
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const tempRoots=[];test.after(()=>{for(const root of tempRoots)fs.rmSync(root,{recursive:true,force:true});});
function temp(t){const root=fs.mkdtempSync(path.join(os.tmpdir(),'orca-mac-download-'));tempRoots.push(root);return root;}
function runtime(root){const tool=path.join(root,'mock executable');fs.writeFileSync(tool,'',{mode:0o700});return {platform:'darwin',home:root,configDir:path.join(root,'config'),downloadsDir:path.join(root,'state'),cli:tool,ssh:tool+' ssh',keygen:tool+' keygen',childEnv:{HOME:root},loadSdk:()=>({RuntimeClient:class{constructor(){this.isRemote=true;}call(){throw Error('No real RPC');}}})};}
function transportFixture(t,platform='darwin'){
 const root=temp(t),r=runtime(root);r.platform=platform;const calls=[],states=[],job={id:'a'.repeat(32),source:'/project/中文 file.bin'};
 const meta={size:4,sha256:crypto.createHash('sha256').update('DATA').digest('hex'),hostKey:'ssh-ed25519 U1lOVEhFVElD',fingerprint:[1,2,4,3]};
 let cleanup=false,failCleanup=false,failSsh=false,script='';
 const tr=new SshTransport({dataDir:path.join(root,'私有 state'),runtime:r,environment:'Mock environment',sshHost:'100.64.0.20',sshUser:'worker',sshPort:2222,wait:async()=>{},onConnection:s=>states.push(s),
  async execute(exe,args,opts){calls.push({exe,args,opts});assert.equal(opts.env,r.childEnv);
   if(exe===r.keygen){const key=args[args.indexOf('-f')+1];fs.writeFileSync(key,'SYNTHETIC-NOT-A-KEY',{mode:0o600});fs.writeFileSync(key+'.pub','ssh-ed25519 U1lOVEhFVElD orca-download:'+job.id);return {stdout:''};}
   if(exe===r.ssh){if(failSsh)throw Error('fixture secret');return {stdout:'KEY_REMOVED:'+job.id+'\n'};}
   assert.equal(exe,r.cli);assert.deepEqual(args.slice(-3),['--environment','Mock environment','--json']);
   let result={};if(args[0]==='repo')result={repos:[{path:'/project'}]};
   if(args[1]==='create'){cleanup=args.includes('Clear transfer access');const command=args[args.indexOf('--command')+1];script=zlib.inflateSync(Buffer.from(command.match(/decode\("([^"]+)"/)[1],'base64')).toString();result={terminal:{handle:'mock-terminal'}};}
   if(args[1]==='read'){if(failCleanup&&cleanup)throw Error('fixture secret');result={terminal:{tail:[cleanup?script.match(/ORCA_DOWNLOAD_CLEANED=[a-f0-9]+:[a-f0-9]+/)[0]:'ORCA_DOWNLOAD_META='+JSON.stringify(meta)]}};}
   return {stdout:JSON.stringify({ok:true,result})};
  },spawn(exe,args,opts){calls.push({exe,args,opts});const child=new EventEmitter();child.stdout=new PassThrough();child.stderr=new PassThrough();child.kill=()=>setImmediate(()=>child.emit('close',1));setImmediate(()=>{child.stdout.write('DATA');child.stdout.end();child.emit('close',0);});return child;}
 });
 return {root,r,tr,job,meta,calls,states,get script(){return script;},fail(){failSsh=true;failCleanup=true;},fallback(){failSsh=true;failCleanup=false;}};
}
test('macOS and Windows SSH fixtures isolate identities, pin host keys and use bounded reads',macOnly,async t=>{
 for(const platform of ['darwin','win32']){const f=transportFixture(t,platform);await f.tr.prepare(f.job);
  const keygen=f.calls.find(c=>c.exe===f.r.keygen);assert.deepEqual(keygen.args,['-q','-t','ed25519','-N','','-f',path.join(f.tr.directory(f.job),'key'),'-C','orca-download:'+f.job.id]);
  const args=f.tr.arguments(f.job);for(const option of ['StrictHostKeyChecking=yes','IdentitiesOnly=yes','BatchMode=yes','IdentityAgent=none','ClearAllForwardings=yes','UpdateHostKeys=no','HostKeyAlias='+f.job.id])assert.ok(args.includes(option));
  assert.deepEqual(args.slice(0,3),['-F',platform==='darwin'?'/dev/null':'NUL','-T']);assert.ok(args.some(a=>a.startsWith('UserKnownHostsFile="')&&a.includes('私有 state')));
  assert.match(f.script,/allowed not in source.parents/);assert.match(f.script,/m.register\(request\)/);
  const bytes=[];await f.tr.read(f.job,0,4,new AbortController().signal,b=>bytes.push(b));assert.equal(Buffer.concat(bytes).toString(),'DATA');assert.deepEqual(f.calls.at(-1).args.slice(-3),['read','0','4']);
  await assert.rejects(f.tr.read(f.job,-1,4,new AbortController().signal,()=>{}),/range/);
  for(const name of ['key','known_hosts'])assert.equal(fs.statSync(path.join(f.tr.directory(f.job),name)).mode&0o777,0o600);
  assert.equal(fs.statSync(f.tr.directory(f.job)).mode&0o777,0o700);
  const before=fs.readFileSync(path.join(f.tr.directory(f.job),'known_hosts'));f.meta.hostKey='ssh-ed25519 Q0hBTkdFRA==';await assert.rejects(f.tr.prepare(f.job),/host key changed/);assert.deepEqual(fs.readFileSync(path.join(f.tr.directory(f.job),'known_hosts')),before);
  await f.tr.cleanup(f.job);assert.equal(fs.existsSync(f.tr.directory(f.job)),false);
 }
});
test('transport rejects out-of-project paths before keygen, unsafe key files and preserves keys on cleanup failure',macOnly,async t=>{
 const f=transportFixture(t);for(const source of ['/etc/passwd','/project/../etc/passwd','/project2/file'])await assert.rejects(f.tr.prepare({...f.job,source}));assert.equal(f.calls.some(c=>c.exe===f.r.keygen),false);
 await f.tr.prepare(f.job);const key=path.join(f.tr.directory(f.job),'key');fs.chmodSync(key,0o644);assert.throws(()=>f.tr.arguments(f.job),/unsafe/);fs.chmodSync(key,0o600);
 f.fail();await assert.rejects(f.tr.cleanup(f.job));assert.ok(fs.existsSync(key));f.fallback();await f.tr.cleanup(f.job);assert.ok(!fs.existsSync(key));assert.match(f.script,/m.cleanup\(/);assert.match(f.script,/ORCA_DOWNLOAD_CLEANED/);
});
async function settled(m,id){for(let i=0;i<3000;i++){if(!m.runs.has(id))return m.jobs.get(id);await sleep(5);}throw Error('Timeout');}
function engineFixture(t,size=24*1024*1024){
 const root=temp(t),r=runtime(root),data=Buffer.alloc(size,37),reads=[];let active=0,max=0,fail=true,cleanup=0,delay=1,badHash=false,failCleanup=false;
 const fake={async prepare(){return {size,sha256:badHash?'0'.repeat(64):crypto.createHash('sha256').update(data).digest('hex')};},async read(job,offset,length,signal,onData){reads.push(offset);active++;max=Math.max(max,active);try{if(fail){fail=false;throw Error('synthetic interruption');}for(let n=0;n<length;n+=65536){if(signal.aborted)throw Error('Paused');onData(data.subarray(offset+n,offset+Math.min(length,n+65536)),n);await sleep(delay);}}finally{active--; }},async cleanup(){cleanup++;if(failCleanup)throw Error('offline');}};
 const options={runtime:r,dataDir:r.downloadsDir,transport:fake},m=new DownloadManager(options);t.after(()=>m.close());return {root,r,data,reads,m,options,folder:path.join(root,'中文 folder'),get max(){return max;},get cleanup(){return cleanup;},bad(){badHash=true;},noCleanup(){failCleanup=true;},allowCleanup(){failCleanup=false;}};
}
test('macOS parallel transfer, retries, progress, private metadata, exclusive lock and cleanup',macOnly,async t=>{
 const f=engineFixture(t);assert.throws(()=>new DownloadManager(f.options),/locked/);
 const row=await f.m.create({source:'/project/中文 大文件.bin',folder:f.folder,concurrency:4});
 while(!f.m.list()[0].bytes)await sleep(5);assert.ok(f.m.list()[0].speed>0);assert.ok(f.m.list()[0].percent>0);
 const job=await settled(f.m,row.id);assert.equal(job.status,'complete');assert.ok(job.retries>0);assert.ok(f.max>1&&f.max<=4);assert.equal(job.cleanupWarning,null);assert.equal(f.cleanup,1);assert.deepEqual(fs.readFileSync(job.destination),f.data);
 assert.equal(fs.statSync(path.join(f.r.downloadsDir,'jobs',job.id+'.json')).mode&0o777,0o600);assert.equal(fs.statSync(path.join(f.r.downloadsDir,'jobs')).mode&0o777,0o700);assert.equal(fs.statSync(job.destination).mode&0o777,0o600);
});
test('macOS pause/restart rechecks chunk hashes and re-downloads corrupted persisted ranges',macOnly,async t=>{
 const f=engineFixture(t);const row=await f.m.create({source:'/project/resume.bin',folder:f.folder,concurrency:2});
 while(!f.m.jobs.get(row.id).done.length)await sleep(5);await f.m.action(row.id,'pause');const job=f.m.jobs.get(row.id),index=job.done[0];assert.ok(job.chunkHashes[index]);
 await f.m.close();const fd=fs.openSync(job.partial,'r+');fs.writeSync(fd,Buffer.from('X'),0,1,index*job.chunk);fs.closeSync(fd);
 const m=new DownloadManager(f.options);t.after(()=>m.close());assert.equal(m.jobs.get(job.id).status,'paused');const before=f.reads.length;await m.action(job.id,'resume');assert.equal((await settled(m,job.id)).status,'complete');assert.ok(f.reads.slice(before).includes(index*job.chunk));assert.deepEqual(fs.readFileSync(job.destination),f.data);
});
test('SHA-256 failure, APFS destination collision, cleanup debt and TCC diagnostics retain safe state',macOnly,async t=>{
 const f=engineFixture(t,1024*1024);f.bad();let row=await f.m.create({source:'/project/bad.bin',folder:f.folder,concurrency:4});let job=await settled(f.m,row.id);assert.equal(job.status,'error');assert.match(job.error,/Checksum/);assert.equal(fs.existsSync(job.destination),false);assert.ok(job.cleanupWarning);
 const g=engineFixture(t,1024*1024);row=await g.m.create({source:'/project/File.bin',folder:g.folder,concurrency:4});fs.writeFileSync(row.destination,'KEEP');job=await settled(g.m,row.id);assert.equal(job.status,'error');assert.equal(fs.readFileSync(row.destination,'utf8'),'KEEP');
 const h=engineFixture(t,1024);h.noCleanup();row=await h.m.create({source:'/project/clean.bin',folder:h.folder});job=await settled(h.m,row.id);assert.equal(job.status,'complete');assert.ok(job.cleanupWarning);h.allowCleanup();await h.m.action(job.id,'cleanup');assert.equal(job.cleanupWarning,null);
 let denial='EPERM';const original=fs.mkdirSync;t.mock.method(fs,'mkdirSync',(folder,opts)=>{if(folder===path.join(h.root,'denied'))throw Object.assign(Error('sensitive path'),{code:denial});return original(folder,opts);});
 for(const code of ['EPERM','EACCES']){denial=code;await assert.rejects(h.m.create({source:'/project/a',folder:path.join(h.root,'denied')}),e=>/Files and Folders/.test(e.message)&&!e.message.includes('sensitive'));}
});
test('SIGKILL leaves recoverable tasks and stale lock; restart is passive until Resume',macOnly,async t=>{
 const root=temp(t),r=runtime(root),folder=path.join(root,'crash folder'),engine=require.resolve('./engine.cjs');
 const script=`const {DownloadManager}=require(process.argv[1]);const fs=require('node:fs'),crypto=require('node:crypto');const data=Buffer.alloc(8*1024*1024,37);const m=new DownloadManager({dataDir:process.argv[2],runtime:{platform:'darwin'},transport:{async prepare(){return {size:data.length,sha256:crypto.createHash('sha256').update(data).digest('hex')};},async read(job,offset,length,signal,onData){if(offset){process.send({id:job.id});await new Promise(()=>{});}onData(data.subarray(offset,offset+length),0);},async cleanup(){}}});m.create({source:'/project/crash.bin',folder:process.argv[3],concurrency:1});setInterval(()=>{},1000);`;
 const child=cp.spawn(process.execPath,['-e',script,engine,r.downloadsDir,folder],{stdio:['ignore','ignore','pipe','ipc']});t.after(()=>{if(child.exitCode===null)child.kill('SIGKILL');});
 const row=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Child timeout')),10000);child.once('message',v=>{clearTimeout(timer);resolve(v);});child.once('error',reject);});
 const exited=new Promise(resolve=>child.once('exit',resolve));child.kill('SIGKILL');await exited;
 let reads=0;const data=Buffer.alloc(8*1024*1024,37),m=new DownloadManager({dataDir:r.downloadsDir,runtime:r,transport:{async read(job,offset,length,signal,onData){reads++;assert.equal(offset,4*1024*1024);onData(data.subarray(offset,offset+length),0);},async cleanup(){}}});t.after(()=>m.close());
 assert.equal(reads,0);assert.equal(m.jobs.get(row.id).status,'paused');await m.action(row.id,'resume');assert.equal((await settled(m,row.id)).status,'complete');assert.equal(reads,1);
});
test('remote browser rejects traversal, sibling prefixes, symlinks, unconfirmed and outside replies',macOnly,async()=>{
 const calls=[];let outside=false,confirmed=true;const client={isRemote:true,async call(method,{path:directory}){calls.push(directory);assert.equal(method,'files.browseServerDir');return {ok:confirmed,_meta:{runtimeId:'mock'},result:{pathFlavor:'posix',resolvedPath:outside?'/etc':directory,entries:[{name:'link',isDirectory:true,isSymlink:true},{name:'safe',isDirectory:true,isSymlink:false},{name:'中文.bin',isDirectory:false,isSymlink:false}]}};}};
 const browser=new RemoteBrowser({}, {listProjects:async()=>['/project'],createClient:()=>client});
 const roots=await browser.list('Mock');assert.equal(roots.entries[0].path,'/project');assert.equal(calls.length,0);
 for(const directory of ['/etc','/project2','/project/../etc','/project/./safe','/project/evil\0'])await assert.rejects(browser.list('Mock',{directory}));assert.equal(calls.length,0);
 await assert.rejects(browser.list('Mock',{directory:'/project/link'}),/symbolic/);assert.deepEqual(calls,['/project']);
 const safe=await browser.list('Mock',{directory:'/project/safe',query:'中文'});assert.equal(safe.entries[0].name,'中文.bin');assert.equal(safe.parent,'/project');
 outside=true;await assert.rejects(browser.list('Mock',{directory:'/project'}),/outside/);outside=false;confirmed=false;await assert.rejects(browser.list('Mock',{directory:'/project'}),/confirmed/);confirmed=true;client.isRemote=false;await assert.rejects(browser.list('Mock'),/not remote/);
});
test('macOS HTTP reports setup/configured/ready/error honestly, keeps Accounts/GPU boundaries and opens Finder',macOnly,async t=>{
 const root=temp(t),r=runtime(root);for(const file of [r.ssh,r.keygen])fs.writeFileSync(file,'',{mode:0o700});let creates=0,browses=0,opened,connection;
 const fake={jobs:new Map(),list(){return [...this.jobs.values()];},async close(){},async create({folder}){const job={id:'a'.repeat(32),status:'complete',destination:path.join(folder,'mock.bin'),verified:true};this.jobs.set(job.id,job);return job;}};
 r.openPath=async folder=>{opened=folder;};let fail=false;
 const service=await startServer({runtime:r,port:0,createDownloads:opts=>{creates++;connection=opts.onConnection;return fake;},gpu:{async status(){return {online:false};}},remoteBrowser:{async list(){browses++;if(fail)throw Error('secret pairing');return {directory:'/project',entries:[],parent:null};}}});t.after(()=>service.close());
 const html=await (await fetch(service.url)).text(),headers={'X-Toolkit-Token':html.match(/const token='([a-f0-9]+)'/)[1],'Content-Type':'application/json',Origin:service.url.slice(0,-1)};
 const get=route=>fetch(service.url+'api/'+route,{headers}).then(r=>r.json());const post=(route,body)=>fetch(service.url+'api/'+route,{method:'POST',headers,body:JSON.stringify(body)});
 assert.equal((await get('status')).modules.downloads.status,'needs-setup');assert.equal(creates,0);assert.equal(browses,0);
 const settings=structuredClone(service.controller.settings);settings.downloads={enabled:true,environment:'Mock',sshHost:'100.64.0.20',sshUser:'worker',sshPort:22};assert.equal((await post('settings',settings)).status,200);
 let state=await get('status');assert.equal(state.modules.downloads.status,'configured');assert.equal(state.modules.downloads.available,true);assert.equal(state.modules.accounts.status,'preview');assert.equal(state.modules.gpu.status,'offline');assert.equal(browses,0);
 assert.equal((await get('downloads/jobs')).defaultFolder,path.join(root,'Downloads','Orca'));await post('downloads/remote-browse',{directory:'/project'});assert.equal((await get('status')).modules.downloads.status,'ready');
 fail=true;await post('downloads/remote-browse',{directory:'/project'});state=await get('status');assert.equal(state.modules.downloads.status,'error');assert.ok(!JSON.stringify(state).includes('secret'));connection('offline');assert.equal((await get('status')).modules.downloads.status,'offline');
 const folder=path.join(root,'中文 folder');await post('downloads/jobs',{source:'/project/file',folder});const jobs=await get('downloads/jobs');assert.equal(jobs.jobs[0].status,'complete');await post('downloads/open-folder',{id:jobs.jobs[0].id});assert.equal(opened,folder);
 fake.jobs.set('pending',{status:'paused'});settings.downloads.environment='Other';assert.equal((await post('settings',settings)).status,400);fake.jobs.clear();fs.unlinkSync(r.keygen);await service.controller.configureDownloads();assert.equal((await get('status')).modules.downloads.status,'error');assert.equal(service.controller.downloads,null);
});
test('publication interrupted after APFS hard-link resumes only the same inode and verified bytes',macOnly,async t=>{
 const f=engineFixture(t,1024);const unlink=fs.unlinkSync;let blocked=true;
 t.mock.method(fs,'unlinkSync',file=>{if(blocked&&file.endsWith('.part')){blocked=false;throw Object.assign(Error('mock interruption'),{code:'EIO'});}return unlink(file);});
 const row=await f.m.create({source:'/project/publish.bin',folder:f.folder});const job=await settled(f.m,row.id);assert.equal(job.status,'error');assert.ok(job.publishing);assert.equal(fs.statSync(job.partial).ino,fs.statSync(job.destination).ino);
 await f.m.close();const m=new DownloadManager(f.options);t.after(()=>m.close());await m.action(job.id,'resume');assert.equal((await settled(m,job.id)).status,'complete');assert.equal(fs.existsSync(job.partial),false);assert.deepEqual(fs.readFileSync(job.destination),f.data);
});
test('private saved metadata and partial symlinks fail closed, disk-full allocation can retry',macOnly,async t=>{
 const f=engineFixture(t,1024),truncate=fs.ftruncateSync;let full=true;
 t.mock.method(fs,'ftruncateSync',(...args)=>{if(full){full=false;throw Object.assign(Error('mock disk full'),{code:'ENOSPC'});}return truncate(...args);});
 const row=await f.m.create({source:'/project/space.bin',folder:f.folder});let job=await settled(f.m,row.id);assert.match(job.error,/Not enough space/);assert.equal(job.sha256,undefined);await f.m.action(job.id,'resume');assert.equal((await settled(f.m,job.id)).status,'complete');
 await f.m.close();const meta=path.join(f.r.downloadsDir,'jobs',job.id+'.json');fs.chmodSync(meta,0o644);assert.throws(()=>new DownloadManager(f.options),/unsafe/);fs.chmodSync(meta,0o600);
 const saved=JSON.parse(fs.readFileSync(meta));saved.status='paused';saved.verified=false;const target=path.join(f.root,'untouched');fs.writeFileSync(target,'KEEP');fs.symlinkSync(target,saved.partial);fs.writeFileSync(meta,JSON.stringify(saved));
 const m=new DownloadManager(f.options);t.after(()=>m.close());await m.action(job.id,'resume');job=await settled(m,job.id);assert.equal(job.status,'error');assert.equal(fs.readFileSync(target,'utf8'),'KEEP');
});
test('Downloads UI renders actual task status and configured/error availability without claiming connected',macOnly,async()=>{
 const vm=require('node:vm'),nodes=new Map();
 function node(){return {textContent:'',style:{},dataset:{},children:[],classList:{add(){},remove(){}},append(...children){this.children.push(...children);},replaceChildren(...children){this.children=children;},querySelector(){return node();},replaceWith(){}};}
 const document={getElementById(id){if(!nodes.has(id))nodes.set(id,node());return nodes.get(id);},createElement:node,querySelector:()=>null};
 let state={availability:{status:'configured',available:true},jobs:[],defaultFolder:'/synthetic/中文 folder'};
 const context=vm.createContext({document,window:{addEventListener(){}},setInterval(){},setTimeout(){},clearTimeout(){},fetch:async()=>({ok:true,json:async()=>state})});
 const html=fs.readFileSync(path.join(__dirname,'index.html'),'utf8'),script=html.match(/<script nonce="__TOKEN__">([\s\S]*?)<\/script>/)[1];vm.runInContext(script,context);await vm.runInContext('refresh()',context);
 assert.match(nodes.get('connection').textContent,/not checked/);assert.equal(nodes.get('start').disabled,false);
 state={...state,availability:{status:'error',available:true,error:'Remote unavailable'},jobs:[{id:'a'.repeat(32),status:'paused',name:'test.bin',destination:'/synthetic/test.bin',source:'/project/test.bin',size:10,bytes:5,percent:50,concurrency:4}]};await vm.runInContext('refresh()',context);
 assert.equal(nodes.get('connection').textContent,'Downloads error');assert.equal(nodes.get('preview-notice').textContent,'Remote unavailable');assert.equal(nodes.get('downloads').children.length,1);
 state={...state,availability:{status:'needs-setup',available:false},jobs:[]};await vm.runInContext('refresh()',context);assert.equal(nodes.get('start').disabled,true);assert.equal(nodes.get('browse-remote').disabled,true);
});
