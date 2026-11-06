'use strict';
// Actual hidden Electron window; synthetic service identity and dialog answers.
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),cp=require('node:child_process'),assert=require('node:assert/strict');
const {createRequire}=require('node:module');
const [orca,stage,windowBundle,authorityBundle]=process.argv.slice(2).map(p=>path.resolve(p));
const {_electron}=createRequire(path.join(orca,'package.json'))('playwright');
const root=fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(),'host-electron-')));
const config=path.resolve(__dirname,'../plugins/toolkit/config-service');
let electron;
(async()=>{
 const remoteState=path.join(root,'remote-authority');fs.mkdirSync(remoteState,{mode:0o700});
 const authorityPath=path.resolve(__dirname,'../services/download-authorization');
 const remoteScript=`import sys,os
sys.path[:0]=sys.argv[2:4]
from authority import Authority
from secure_store import loads,dumps
p={'owners':{str(os.getuid()):[sys.argv[4]]},'reader_uid':os.getuid()+1,'lookup_uid':os.getuid()+2,'server_id':'server','endpoint_id':'endpoint','account_id':'reader','signing_key_id':'one','dispatcher':'/opt/orca-download/dispatch'}
s=Authority(sys.argv[1],p,sys.argv[5]=='init')
try:
 print(dumps({'receiptKey':s.verify_key,'authority_epoch':s.meta('epoch')} if sys.argv[5]=='init' else s.handle(os.getuid(),loads(sys.stdin.buffer.read()))))
finally:s.close()`;
 const remoteArgs=['-c',remoteScript,remoteState,config,authorityPath,root];
 const trust={...JSON.parse(cp.execFileSync('python3',[...remoteArgs,'init'],{encoding:'utf8'})),server_id:'server',endpoint_id:'endpoint',account_id:'reader',signing_key_id:'one',hostKey:'ssh-ed25519 U1lOVEhFVElD',host:'100.64.0.20',port:22,worktree:'path:/test'};
 const small=path.join(root,'small.bin'),large=path.join(root,'large.bin');
 fs.writeFileSync(small,'verified payload');fs.writeFileSync(large,Buffer.alloc(32*1024*1024,37));
 const state=path.join(root,'state');fs.mkdirSync(state,{mode:0o700});
 cp.execFileSync('python3',['-c','import sys;sys.path.insert(0,sys.argv[1]);from config_store import ConfigStore;s=ConfigStore(sys.argv[2],True);s.close()',config,state]);
 const fixture=path.join(root,'main.cjs');
 fs.writeFileSync(fixture,`
 const {app,dialog,BrowserWindow,shell}=require('electron');
 app.setActivationPolicy('prohibited');app.setPath('home',${JSON.stringify(root)});app.setPath('userData',${JSON.stringify(path.join(root,'profile'))});
 const cp=require('node:child_process');
 const {ToolkitAuthority}=require(${JSON.stringify(authorityBundle)});
 const {validate}=require(${JSON.stringify(path.join(config,'commands.cjs'))});
 const fs=require('node:fs');const activeFile=${JSON.stringify(path.join(root,'remote-account'))};
 global.fixture={active:fs.existsSync(activeFile)?fs.readFileSync(activeFile,'utf8'):'a',offline:false,readFailure:false,cleanupFailure:false,allow:true,confirmations:[],calls:[],folder:${JSON.stringify(path.join(root,'chosen folder'))}};
 dialog.showOpenDialog=async()=>({canceled:!global.fixture.folder,filePaths:global.fixture.folder?[global.fixture.folder]:[]});
 const Module=require('node:module'),originalLoad=Module._load;
 Module._load=function(file,...rest){if(file==='@orca/process-host')return {runProcess:async()=>{throw Error('Unexpected process');}};if(file.endsWith('/out/cli/runtime-client.js'))return {RuntimeClient:class{constructor(){this.isRemote=true;}async call(method,{path}){if(method.startsWith('accounts.')){if(global.fixture.offline)throw {code:'ECONNRESET'};if(method==='accounts.selectClaude'){global.fixture.active=arguments[1].accountId;fs.writeFileSync(activeFile,global.fixture.active);}global.fixture.accountCalls=(global.fixture.accountCalls||[]).concat({method,params:arguments[1]});return {ok:true,_meta:{runtimeId:'fixture'},result:{claude:{activeAccountId:global.fixture.active,accounts:[{id:'a',email:'a@example.test'},{id:'b',email:'b@example.test'}]},rateLimits:{claude:{status:'ok',updatedAt:Date.now(),usageMetadata:{authProvenance:'managed:'+global.fixture.active},session:{usedPercent:25,resetsAt:Date.now()+3600000},weekly:{usedPercent:40}}}}};}if(method!=='files.browseServerDir')throw Error('unexpected RPC');return {ok:true,_meta:{runtimeId:'fixture'},result:{pathFlavor:'posix',resolvedPath:path,entries:[{name:'data.bin',isDirectory:false,isSymlink:false},{name:'sub',isDirectory:true,isSymlink:false}]}};}}};return originalLoad.call(this,file,...rest);};
 const {V3Transport,canonical}=require(${JSON.stringify(path.join(stage,'modules/downloads/v3.cjs'))});
 V3Transport.prototype.ensureReady=function(){this.trust=${JSON.stringify(trust)};};
 V3Transport.prototype.manage=async function(request){const reply=JSON.parse(cp.execFileSync('python3',${JSON.stringify([...remoteArgs,'request'])},{input:canonical(request),encoding:'utf8'}));if(request.op==='revoke'&&global.fixture.cleanupFailure)throw Error('Synthetic lost revoke reply');return reply;};
 V3Transport.prototype.read=async function(job,offset,length,signal,onData){
  const fd=fs.openSync(job.source,'r'),data=Buffer.alloc(length);try{fs.readSync(fd,data,0,length,offset);}finally{fs.closeSync(fd);}
  for(let n=0;n<data.length;n+=65536){if(signal.aborted)throw Error('Paused');if(global.fixture.readFailure){global.fixture.readFailure=false;throw Error('Synthetic network interruption');}onData(data.subarray(n,n+65536),n);await new Promise(r=>setTimeout(r,10));}
 };
 shell.showItemInFolder=file=>{global.fixture.revealed=file;};
 cp.execFile=()=>{throw Error('unexpected external request');};
 cp.execFile[require('node:util').promisify.custom]=async(file,args)=>{if(args[0]!=='repo'||args[1]!=='list')throw Error('unexpected external request');return {stdout:JSON.stringify({ok:true,result:{repos:[{path:'/project'}]}}),stderr:''};};

 dialog.showMessageBox=async(_panel,options)=>{global.fixture.confirmations.push(options);return {response:global.fixture.allow?1:0};};
 const authority=new ToolkitAuthority({validate,createHostHandler:authorize=>async(envelope,grant)=>{
  if(!authorize(grant))throw Error('revoked');global.fixture.calls.push(envelope.request.op);
  return JSON.parse(cp.execFileSync('python3',[${JSON.stringify(path.join(config,'fixture.py'))},${JSON.stringify(state)}],{input:JSON.stringify(envelope.request),encoding:'utf8'}));
 }},()=>{throw Error('synthetic transport only');});
 app.whenReady().then(async()=>{global.fixture.panel=await require(${JSON.stringify(windowBundle)}).createToolkitMaintenanceWindow(authority,${JSON.stringify(stage)});});
 `);
 electron=await _electron.launch({executablePath:createRequire(path.join(orca,'package.json'))('electron'),args:[fixture],env:{...process.env,ORCA_BACKGROUND_LAUNCH:'1'}});
 const page=await electron.firstWindow();page.setDefaultTimeout(20000);await page.setViewportSize({width:1000,height:900});page.on('pageerror',error=>console.error(error));
 await page.waitForFunction(()=>document.getElementById('config-capabilities').textContent.includes('Preview both'));
 assert.equal(await electron.evaluate(()=>global.fixture.panel.isVisible()),false);
 await page.locator('#config-settings-defaults').check();await page.locator('#config-preferences-defaults').check();
 await page.locator('#config-preview').click();await page.waitForFunction(()=>!document.getElementById('config-confirm').disabled);
 await electron.evaluate(()=>{global.fixture.allow=false;});await page.locator('#config-confirm').click();
 await page.waitForFunction(()=>document.getElementById('config-result').textContent.includes('Unauthorized'));
 await electron.evaluate(()=>{global.fixture.allow=true;});await page.locator('#config-confirm').click();
 await page.waitForFunction(()=>document.getElementById('config-capabilities').textContent.includes('ready'));
 await page.locator('#setting-accounts-environment').fill('fixture');await page.locator('#setting-downloads-environment').fill('fixture');await page.locator('#setting-downloads-sshHost').fill('100.64.0.20');await page.locator('#setting-downloads-sshUser').fill('reader');
 await page.locator('#config-save').click();await page.waitForFunction(()=>document.getElementById('config-result').textContent.includes('"operation": "patch"'));
 await page.locator('#config-backup').click();await page.waitForFunction(()=>document.getElementById('config-backup-id').value.startsWith('backup-'));
 await page.locator('#config-restore').click();await page.waitForFunction(()=>document.getElementById('config-result').textContent.includes('"operation": "restore"'));
 const call=input=>page.evaluate(input=>window.orcaToolkitDownloads.call(input),input);
 const accounts=input=>page.evaluate(input=>window.orcaToolkitTools.call(input),input);
 await page.getByRole('link',{name:'Accounts',exact:true}).click();
 await page.locator('#host-accounts #refresh').click();await page.waitForFunction(()=>document.querySelectorAll('#host-accounts .card').length===2);
 assert.match(await page.locator('#host-accounts').textContent(),/75% remaining/);
 await page.locator('#host-accounts .card').nth(1).getByRole('button',{name:'Disable account',exact:true}).click();
 assert.equal((await accounts({operation:'accounts'})).accounts[1].disabled,true);
 await page.locator('#host-accounts .card').nth(1).getByRole('button',{name:'Enable account',exact:true}).click();
 await page.getByRole('button',{name:'Use this account',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#host-accounts #message').textContent.includes('Account selected'));
 assert.equal((await accounts({operation:'accounts'})).activeAccountId,'b');
 await electron.evaluate(()=>{global.fixture.offline=true;});await page.locator('#host-accounts #refresh').click();await page.waitForFunction(()=>document.querySelector('#host-accounts #message').textContent.includes('disconnected'));
 assert.equal(await page.locator('#host-accounts .card').count(),0);
 await electron.evaluate(()=>{global.fixture.offline=false;});await page.locator('#host-accounts #refresh').click();await page.waitForFunction(()=>document.querySelectorAll('#host-accounts .card').length===2);
 await page.locator('#host-accounts').screenshot({path:'/tmp/toolkit-accounts-7b15.png'});
 await page.getByRole('link',{name:'Downloads',exact:true}).click();
 await page.locator('#host-download-remote').click();await page.getByRole('button',{name:'Folder: /project',exact:true}).click();await page.getByRole('button',{name:'File: data.bin',exact:true}).click();
 assert.equal(await page.locator('#host-download-source').inputValue(),'/project/data.bin');
 const folder=path.join(root,'Downloads','Orca');
 const submit=async(source,concurrency=2)=>{await page.locator('#host-download-source').fill(source);await page.locator('#host-download-folder').fill(folder);await page.locator('#host-download-concurrency').fill(String(concurrency));await page.locator('#host-download-create').click();await page.waitForFunction(()=>!document.getElementById('host-download-create').disabled);return (await call({operation:'list'})).jobs.find(job=>job.source===source);};
 const waitJob=async(id,status)=>{for(let i=0;i<300;i++){const row=(await call({operation:'list'})).jobs.find(j=>j.id===id);if(row.status===status)return row;if(row.status==='error')throw Error(row.error);await new Promise(r=>setTimeout(r,100));}throw Error('Task timeout: '+status);};
 const one=await submit(small);await waitJob(one.id,'complete');assert.equal(fs.readFileSync(one.destination,'utf8'),'verified payload');
 await call({operation:'open-folder',id:one.id});assert.equal(await electron.evaluate(()=>global.fixture.revealed),one.destination);
 await electron.evaluate(()=>{global.fixture.readFailure=true;});const big=await submit(large,2);
 for(let i=0;i<100;i++){if((await call({operation:'list'})).jobs.find(j=>j.id===big.id).bytes>0)break;await new Promise(r=>setTimeout(r,50));}
 await call({operation:'pause-all'});assert.equal((await call({operation:'list'})).jobs.find(j=>j.id===big.id).status,'paused');
 const changes=await page.evaluate(()=>Promise.allSettled([0,1].map(()=>window.orcaToolkitTools.call({operation:'accounts-select',accountId:'a',expectedActiveAccountId:'b'}))));
 assert.equal(changes.filter(r=>r.status==='fulfilled').length,1);
 assert.equal((await call({operation:'list'})).jobs.find(j=>j.id===big.id).status,'paused');
 await accounts({operation:'accounts-select',accountId:'b',expectedActiveAccountId:'a'});
 await accounts({operation:'accounts-disable',accountId:'a',disabled:true});
 assert.equal(await page.locator('#setting-downloads-sshHost').isDisabled(),true);
 // A new renderer request ID for the same unfinished file still returns one durable task.
 const replay=await call({operation:'create',requestId:require('node:crypto').randomUUID(),source:large,folder,concurrency:2});assert.equal(replay.job.id,big.id);
 await call({operation:'action',id:big.id,action:'resume'});
 const requestId=require('node:crypto').randomUUID(),pendingInput={operation:'create',source:large,folder,concurrency:2},targetKey=(await call({operation:'list'})).targetKey;
 await page.evaluate(({requestId,pendingInput,targetKey})=>localStorage.setItem('toolkit-pending-downloads',JSON.stringify({[JSON.stringify([targetKey,pendingInput])]:requestId})),{requestId,pendingInput,targetKey});
 assert.equal((await call({...pendingInput,requestId})).job.id,big.id);
 // Restart the actual Electron process with the same isolated profile and authority databases.
 await electron.close();electron=await _electron.launch({executablePath:createRequire(path.join(orca,'package.json'))('electron'),args:[fixture],env:{...process.env,ORCA_BACKGROUND_LAUNCH:'1'}});
 const restored=await electron.firstWindow();restored.setDefaultTimeout(20000);await restored.setViewportSize({width:1000,height:900});
 await restored.waitForFunction(()=>!!window.orcaToolkitDownloads);
 const invoke=input=>restored.evaluate(input=>window.orcaToolkitDownloads.call(input),input);
 let saved=await invoke({operation:'list'});assert.equal(saved.jobs.find(j=>j.id===big.id).status,'paused');for(let i=0;i<100&&!saved.jobs.find(j=>j.id===one.id).verified;i++){await new Promise(r=>setTimeout(r,20));saved=await invoke({operation:'list'});}assert.equal(saved.jobs.find(j=>j.id===one.id).verificationState,'verified');
 const durableRequests=await restored.evaluate(()=>JSON.parse(localStorage.getItem('toolkit-pending-downloads')));assert.equal(durableRequests[JSON.stringify([targetKey,pendingInput])],requestId);
 assert.equal((await invoke({...pendingInput,requestId})).job.id,big.id);
 await invoke({operation:'action',id:one.id,action:'verify'});
 for(let i=0;i<100;i++){const row=(await invoke({operation:'list'})).jobs.find(j=>j.id===one.id);if(row.verificationState==='verified')break;await new Promise(r=>setTimeout(r,20));}
 assert.equal((await invoke({operation:'list'})).jobs.find(j=>j.id===one.id).verificationState,'verified');
 const restoredAccounts=await restored.evaluate(()=>window.orcaToolkitTools.call({operation:'accounts'}));assert.equal(restoredAccounts.activeAccountId,'b');assert.equal(restoredAccounts.accounts[0].disabled,true);
 await invoke({operation:'action',id:big.id,action:'resume'});
 for(let i=0;i<400;i++){saved=await invoke({operation:'list'});if(saved.jobs.find(j=>j.id===big.id).status==='complete')break;await new Promise(r=>setTimeout(r,100));}
 const completed=saved.jobs.find(j=>j.id===big.id);assert.equal(completed.status,'complete');assert.equal(completed.verified,true);
 for(let i=0;i<50;i++){saved=await invoke({operation:'list'});if(!saved.jobs.find(j=>j.id===big.id).cleanupWarning)break;await new Promise(r=>setTimeout(r,100));}
 assert.equal(saved.jobs.find(j=>j.id===big.id).cleanupWarning,null);
 assert.equal(require('node:crypto').createHash('sha256').update(fs.readFileSync(big.destination)).digest('hex'),require('node:crypto').createHash('sha256').update(fs.readFileSync(large)).digest('hex'));
 await electron.evaluate(()=>{global.fixture.cleanupFailure=true;});
 const cancel=await invoke({operation:'create',requestId:require('node:crypto').randomUUID(),source:large,folder,concurrency:1});
 await assert.rejects(invoke({operation:'action',id:cancel.job.id,action:'cancel'}));
 assert.ok((await invoke({operation:'list'})).jobs.find(j=>j.id===cancel.job.id).cleanupWarning);
 await electron.evaluate(()=>{global.fixture.cleanupFailure=false;});await invoke({operation:'action',id:cancel.job.id,action:'cleanup'});
 const cleaned=(await invoke({operation:'list'})).jobs.find(j=>j.id===cancel.job.id);assert.equal(cleaned.status,'cancelled');assert.equal(cleaned.cleanupWarning,null);
 await restored.getByRole('link',{name:'Downloads',exact:true}).click();await restored.locator('#host-download-refresh').click();await restored.locator('#host-downloads').screenshot({path:'/tmp/toolkit-downloads-7b15.png'});
 assert.equal(await electron.evaluate(()=>global.fixture.panel.isVisible()),false);
 console.log('PASS: real hidden macOS Electron/main/production preload, account masking/usage/switch/disable/enable/disconnect/reconnect/restart; remote browse fixture; small and 32 MiB files, progress/pause/resume/retry/restart/dedup/cancel/cleanup/folder reveal routing; real SSHSIG, signed receipts, SHA-256, filesystem and both SQLite authorities. Accounts, remote listing, byte transport, service identity and dialogs are synthetic. No VPS or production credentials.');
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{if(electron){const kill=setTimeout(()=>electron.process().kill(),5000);try{await electron.close();}finally{clearTimeout(kill);}}fs.rmSync(root,{recursive:true,force:true});});
