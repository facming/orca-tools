'use strict';
// Actual hidden Electron window; synthetic service identity and dialog answers.
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),cp=require('node:child_process'),assert=require('node:assert/strict');
const {createRequire}=require('node:module');
const [orca,stage,windowBundle,authorityBundle]=process.argv.slice(2).map(p=>path.resolve(p));
const {_electron}=createRequire(path.join(orca,'package.json'))('playwright');
const root=fs.mkdtempSync(path.join(os.tmpdir(),'host-electron-'));
const config=path.resolve(__dirname,'../plugins/toolkit/config-service');
let electron;
(async()=>{
 const state=path.join(root,'state');fs.mkdirSync(state,{mode:0o700});
 cp.execFileSync('python3',['-c','import sys;sys.path.insert(0,sys.argv[1]);from config_store import ConfigStore;s=ConfigStore(sys.argv[2],True);s.close()',config,state]);
 const fixture=path.join(root,'main.cjs');
 fs.writeFileSync(fixture,`
 const {app,dialog,BrowserWindow,shell}=require('electron');
 app.setActivationPolicy('prohibited');app.setPath('home',${JSON.stringify(root)});app.setPath('userData',${JSON.stringify(path.join(root,'profile'))});
 const cp=require('node:child_process');
 const {ToolkitAuthority}=require(${JSON.stringify(authorityBundle)});
 const {validate}=require(${JSON.stringify(path.join(config,'commands.cjs'))});
 global.fixture={active:'a',allow:true,confirmations:[],calls:[],folder:${JSON.stringify(path.join(root,'chosen folder'))}};
 dialog.showOpenDialog=async()=>({canceled:!global.fixture.folder,filePaths:global.fixture.folder?[global.fixture.folder]:[]});
 const Module=require('node:module'),originalLoad=Module._load;
 Module._load=function(file,...rest){if(file.endsWith('/out/cli/runtime-client.js'))return {RuntimeClient:class{constructor(){this.isRemote=true;}async call(method,{path}){if(method.startsWith('accounts.')){if(method==='accounts.selectClaude')global.fixture.active=arguments[1].accountId;return {ok:true,_meta:{runtimeId:'fixture'},result:{claude:{activeAccountId:global.fixture.active,accounts:[{id:'a',email:'a@example.test'},{id:'b',email:'b@example.test'}]}}};}if(method!=='files.browseServerDir')throw Error('unexpected RPC');return {ok:true,_meta:{runtimeId:'fixture'},result:{pathFlavor:'posix',resolvedPath:path,entries:[{name:'data.bin',isDirectory:false,isSymlink:false},{name:'sub',isDirectory:true,isSymlink:false}]}};}}};return originalLoad.call(this,file,...rest);};
 shell.openExternal=async url=>{global.fixture.opened=url;};
 // The staged module is patched separately; no real Bridge or GPU is contacted.
 const stagedGpu=require(${JSON.stringify(path.join(stage,'modules/gpu-macos.cjs'))}).MacGpu;
 stagedGpu.prototype.status=async()=>({comfyui:{state:'running'},godot:{state:'available'},blender:{state:'available'},unity:{state:'installed'}});
 stagedGpu.prototype.request=async()=>({workflows:[{name:'Fixture workflow',available:true}]});
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
 const page=await electron.firstWindow();await page.setViewportSize({width:1000,height:900});page.on('pageerror',error=>console.error(error));
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
 await page.locator('#host-accounts #refresh').click();await page.waitForFunction(()=>document.querySelectorAll('#host-accounts .card').length===2);
 await page.getByRole('button',{name:'Use this account',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#host-accounts #message').textContent.includes('Account selected'));
 assert.equal(await electron.evaluate(()=>global.fixture.active),'b');
 await page.locator('#host-gpu-refresh').click();await page.waitForFunction(()=>document.getElementById('host-gpu-workflows').textContent.includes('Fixture workflow'));
 assert.match(await page.locator('#host-gpu-status').textContent(),/unity: installed/);
 await page.locator('#host-gpu-open').click();await page.waitForFunction(()=>document.getElementById('host-gpu-status').textContent.includes('Opened'));
 assert.equal(await electron.evaluate(()=>global.fixture.opened),'http://127.0.0.1:18773/');
 await assert.rejects(page.evaluate(()=>window.orcaToolkitTools.call({operation:'accounts-select',accountId:'b',expectedActiveAccountId:'a',environment:'injected'})));
 await page.getByRole('link',{name:'Downloads',exact:true}).click();
 const downloads=await page.evaluate(()=>window.orcaToolkitDownloads.call({operation:'list'}));assert.deepEqual(downloads.jobs,[]);assert.deepEqual(downloads.recovery,[]);assert.equal(downloads.defaultFolder,path.join(root,'Downloads','Orca'));
 await page.locator('#host-download-browse').click();await page.waitForFunction(()=>document.getElementById('host-download-folder').value.endsWith('chosen folder'));
 await page.locator('#host-download-remote').click();await page.getByRole('button',{name:'Folder: /project',exact:true}).click();await page.getByRole('button',{name:'File: data.bin',exact:true}).click();
 assert.equal(await page.locator('#host-download-source').inputValue(),'/project/data.bin');
 const saved=await page.evaluate(()=>window.orcaToolkitDownloads.call({operation:'list'}));assert.equal(saved.defaultFolder,path.join(root,'chosen folder'));assert.equal(saved.remoteLocation.directory,'/project');
 await electron.evaluate(()=>{global.fixture.folder=null;});await page.locator('#host-download-browse').click();await page.waitForFunction(()=>!document.getElementById('host-download-browse').disabled);assert.equal(await page.locator('#host-download-folder').inputValue(),path.join(root,'chosen folder'));
 await assert.rejects(page.evaluate(()=>window.orcaToolkitDownloads.call({operation:'browse-remote',directory:'/etc'})),/authorized/);
 await assert.rejects(page.evaluate(()=>window.orcaToolkitDownloads.call({operation:'action',id:'a'.repeat(32),action:'release'})));

 const invoke=request=>JSON.parse(cp.execFileSync('python3',[path.join(config,'fixture.py'),state],{input:JSON.stringify(request),encoding:'utf8'}));
 const status=invoke({op:'status'}).result;
 assert.equal(invoke({op:'task-create',request_id:require('node:crypto').randomUUID(),task_id:'a'.repeat(32),owner:'b'.repeat(32),epoch:status.epoch,store_id:status.store_id,target:{environment:'fixture',sshHost:'100.64.0.20',sshUser:'reader',sshPort:22}}).ok,true);
 await page.locator('#host-download-reconcile').click();await page.waitForFunction(()=>document.getElementById('host-download-recovery').textContent.includes('no local file'));
 await page.locator('#config-status').click();await page.waitForFunction(()=>document.getElementById('config-capabilities').textContent.includes('leases'));
 assert.equal(await page.locator('#config-restore').isDisabled(),true);assert.equal(await page.locator('#setting-downloads-sshHost').isDisabled(),true);
 const visible=await page.evaluate(()=>window.orcaToolkitConfiguration.call({operation:'status'}));assert.equal(JSON.stringify(visible).includes('owner'),false);
 await assert.rejects(page.evaluate(()=>window.orcaToolkitConfiguration.call({operation:'task-update'})),/Unauthorized/);
 const denied=await electron.evaluate(async({BrowserWindow},stage)=>{
  const other=new BrowserWindow({show:false,webPreferences:{sandbox:true,contextIsolation:true,nodeIntegration:false,preload:stage+'/preload.cjs'}});
  try{await other.loadFile(stage+'/panel.html');other.webContents.send('toolkit:ready:v1');return await other.webContents.executeJavaScript("Promise.all([window.orcaToolkitDownloads.call({operation:'list'}),window.orcaToolkitTools.call({operation:'accounts'})].map(p=>p.then(()=>false,()=>true))).then(results=>results.every(Boolean))");}finally{other.destroy();}
 },stage);assert.equal(denied,true);
 await page.locator('#host-downloads').scrollIntoViewIfNeeded();await page.screenshot({path:'/tmp/toolkit-host-electron-7b11.png',fullPage:true});
 assert.equal(await electron.evaluate(()=>global.fixture.panel.isVisible()),false);
 console.log('PASS: actual hidden Electron 1000x900 + production window/preload/sealed engine; preview, native-dialog Cancel/Allow fixture, edit, backup/restore, account switch, four engine status, fixed GPU manager URL, strict tools schema, leased target restrictions, remote browsing, saved preferences, folder selection/cancellation, redacted owner, second-WebContents denial. Identity/dialog answers synthetic; not Developer ID acceptance.');
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{if(electron){const kill=setTimeout(()=>electron.process().kill(),5000);try{await electron.close();}finally{clearTimeout(kill);}}fs.rmSync(root,{recursive:true,force:true});});
