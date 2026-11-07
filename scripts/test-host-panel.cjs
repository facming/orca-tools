'use strict';
// Rendered Chromium + disposable SQLite. Synthetic Host binding, not Electron acceptance.
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),cp=require('node:child_process'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const {createRequire}=require('node:module');
const {chromium}=createRequire(path.resolve(process.argv[2]||'../orca','package.json'))('playwright');
const config=path.resolve(__dirname,'../plugins/toolkit/config-service'),root=fs.mkdtempSync(path.join(os.tmpdir(),'host-panel-'));
const invoke=request=>{const r=JSON.parse(cp.execFileSync('python3',[path.join(config,'fixture.py'),root],{input:JSON.stringify(request),encoding:'utf8'}));if(!r.ok)throw Error(r.error);return r.result;};
let browser;
(async()=>{
 cp.execFileSync('python3',['-c','import sys;sys.path.insert(0,sys.argv[1]);from config_store import ConfigStore;s=ConfigStore(sys.argv[2],True);s.close()',config,root]);
 browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1000,height:900}});
 await page.exposeFunction('hostFixture',async ({operation,...args})=>{
  const result=invoke({op:operation==='confirm'?'import':operation,...args});
  if(operation==='status'){const idle=!result.target_leases.length;result.maintenance={targetChange:idle,confirm:idle&&result.state==='migration-pending',restore:idle&&result.state==='ready',reason:idle?'':'Active or unknown download leases require verified cleanup.'};}
  return result;
 });
 fs.writeFileSync(path.join(root,'blank.html'),'<!doctype html><title>Host panel fixture</title>');
 await page.goto('file://'+path.join(root,'blank.html'));
 await page.evaluate(()=>{window.orcaToolkitConfiguration={call:input=>window.hostFixture(input)};});
 await page.setContent(fs.readFileSync(path.resolve(__dirname,'../plugins/toolkit/configuration-panel.html'),'utf8'));
 const button=id=>page.locator('#config-'+id);
 await page.waitForFunction(()=>document.getElementById('config-capabilities').textContent.includes('Preview both'));
 assert.equal(await button('confirm').isDisabled(),true);assert.equal(await button('restore').isDisabled(),true);
 await page.locator('#config-settings-file').setInputFiles({name:'legacy.json',mimeType:'application/json',buffer:Buffer.from('{"future":9007199254740993123456789}')});
 await page.locator('#config-preferences-defaults').check();await button('preview').click();
 await page.waitForFunction(()=>!document.getElementById('config-confirm').disabled);await button('confirm').click();
 await page.waitForFunction(()=>document.getElementById('config-capabilities').textContent.includes('ready'));
 await page.locator('#setting-gpu-enabled').uncheck();await button('save').click();
 await page.waitForFunction(()=>document.getElementById('config-result').textContent.includes('"operation": "patch"'));
 assert.ok(invoke({op:'read',namespace:'settings'}).raw_json.includes('9007199254740993123456789'));
 await button('backup').click();await page.waitForFunction(()=>document.getElementById('config-backup-id').value.startsWith('backup-'));
 await button('restore').click();await page.waitForFunction(()=>document.getElementById('config-result').textContent.includes('"operation": "restore"'));
 const status=invoke({op:'status'}),target={environment:'',sshHost:'',sshUser:'',sshPort:22};
 invoke({op:'task-create',task_id:'a'.repeat(32),owner:'b'.repeat(32),epoch:status.epoch,store_id:status.store_id,target,request_id:crypto.randomUUID()});
 await button('status').click();await page.waitForFunction(()=>document.getElementById('config-capabilities').textContent.includes('leases'));
 assert.equal(await button('restore').isDisabled(),true);assert.equal(await page.locator('#setting-downloads-sshHost').isDisabled(),true);
 await page.screenshot({path:'/tmp/toolkit-host-panel-7b8.png',fullPage:true});
 console.log('PASS: headless Chromium 1000x900, synthetic Host + real disposable SQLite: preview/import, settings patch, exact unknown integer, backup/restore, leased target disabled');
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{await browser?.close();fs.rmSync(root,{recursive:true,force:true});});
