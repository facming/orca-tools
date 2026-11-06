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
 const {app,dialog,BrowserWindow}=require('electron');
 app.setActivationPolicy('prohibited');app.setPath('home',${JSON.stringify(root)});app.setPath('userData',${JSON.stringify(path.join(root,'profile'))});
 const cp=require('node:child_process');
 const {ToolkitAuthority}=require(${JSON.stringify(authorityBundle)});
 const {validate}=require(${JSON.stringify(path.join(config,'commands.cjs'))});
 global.fixture={allow:true,confirmations:[],calls:[]};
 dialog.showMessageBox=async(_panel,options)=>{global.fixture.confirmations.push(options);return {response:global.fixture.allow?1:0};};
 const authority=new ToolkitAuthority({validate,createHostHandler:authorize=>async(envelope,grant)=>{
  if(!authorize(grant))throw Error('revoked');global.fixture.calls.push(envelope.request.op);
  return JSON.parse(cp.execFileSync('python3',[${JSON.stringify(path.join(config,'fixture.py'))},${JSON.stringify(state)}],{input:JSON.stringify(envelope.request),encoding:'utf8'}));
 }},()=>{throw Error('synthetic transport only');});
 app.whenReady().then(async()=>{global.fixture.panel=await require(${JSON.stringify(windowBundle)}).createToolkitMaintenanceWindow(authority,${JSON.stringify(stage)});});
 `);
 electron=await _electron.launch({executablePath:createRequire(path.join(orca,'package.json'))('electron'),args:[fixture],env:{...process.env,ORCA_BACKGROUND_LAUNCH:'1'}});
 const page=await electron.firstWindow();await page.setViewportSize({width:1000,height:900});
 await page.waitForFunction(()=>document.getElementById('config-capabilities').textContent.includes('Preview both'));
 assert.equal(await electron.evaluate(()=>global.fixture.panel.isVisible()),false);
 await page.locator('#config-settings-defaults').check();await page.locator('#config-preferences-defaults').check();
 await page.locator('#config-preview').click();await page.waitForFunction(()=>!document.getElementById('config-confirm').disabled);
 await electron.evaluate(()=>{global.fixture.allow=false;});await page.locator('#config-confirm').click();
 await page.waitForFunction(()=>document.getElementById('config-result').textContent.includes('Unauthorized'));
 await electron.evaluate(()=>{global.fixture.allow=true;});await page.locator('#config-confirm').click();
 await page.waitForFunction(()=>document.getElementById('config-capabilities').textContent.includes('ready'));
 await page.locator('#setting-downloads-environment').fill('fixture');await page.locator('#setting-downloads-sshHost').fill('100.64.0.20');await page.locator('#setting-downloads-sshUser').fill('reader');
 await page.locator('#config-save').click();await page.waitForFunction(()=>document.getElementById('config-result').textContent.includes('"operation": "patch"'));
 await page.locator('#config-backup').click();await page.waitForFunction(()=>document.getElementById('config-backup-id').value.startsWith('backup-'));
 await page.locator('#config-restore').click();await page.waitForFunction(()=>document.getElementById('config-result').textContent.includes('"operation": "restore"'));
 assert.deepEqual(await page.evaluate(()=>window.orcaToolkitDownloads.call({operation:'list'})),{jobs:[],recovery:[]});
 const invoke=request=>JSON.parse(cp.execFileSync('python3',[path.join(config,'fixture.py'),state],{input:JSON.stringify(request),encoding:'utf8'}));
 const status=invoke({op:'status'}).result;
 assert.equal(invoke({op:'task-create',request_id:require('node:crypto').randomUUID(),task_id:'a'.repeat(32),owner:'b'.repeat(32),epoch:status.epoch,store_id:status.store_id,target:{environment:'fixture',sshHost:'100.64.0.20',sshUser:'reader',sshPort:22}}).ok,true);
 await page.locator('#config-status').click();await page.waitForFunction(()=>document.getElementById('config-capabilities').textContent.includes('leases'));
 assert.equal(await page.locator('#config-restore').isDisabled(),true);assert.equal(await page.locator('#setting-downloads-sshHost').isDisabled(),true);
 const visible=await page.evaluate(()=>window.orcaToolkitConfiguration.call({operation:'status'}));assert.equal(JSON.stringify(visible).includes('owner'),false);
 await assert.rejects(page.evaluate(()=>window.orcaToolkitConfiguration.call({operation:'task-update'})),/Unauthorized/);
 const denied=await electron.evaluate(async({BrowserWindow},stage)=>{
  const other=new BrowserWindow({show:false,webPreferences:{sandbox:true,contextIsolation:true,nodeIntegration:false,preload:stage+'/preload.cjs'}});
  try{await other.loadFile(stage+'/panel.html');other.webContents.send('toolkit:ready:v1');return await other.webContents.executeJavaScript("window.orcaToolkitDownloads.call({operation:'list'}).then(()=>false,()=>true)");}finally{other.destroy();}
 },stage);assert.equal(denied,true);
 await page.screenshot({path:'/tmp/toolkit-host-electron-7b9.png',fullPage:true});
 assert.equal(await electron.evaluate(()=>global.fixture.panel.isVisible()),false);
 console.log('PASS: actual hidden Electron 1000x900 + production window/preload/sealed engine; preview, native-dialog Cancel/Allow fixture, edit, backup/restore, leased target restrictions, redacted owner, second-WebContents denial. Identity/dialog answers synthetic; not Developer ID acceptance.');
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{await electron?.close();fs.rmSync(root,{recursive:true,force:true});});
