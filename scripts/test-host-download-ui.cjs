'use strict';
const assert=require('node:assert/strict'),path=require('node:path');
const {createRequire}=require('node:module');
const [orca,stage]=process.argv.slice(2).map(p=>path.resolve(p));
const {chromium}=createRequire(path.join(orca,'package.json'))('playwright');
(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
 const page=await browser.newPage({viewport:{width:1000,height:900}});
 await page.addInitScript(()=>{
  const job={id:'a'.repeat(32),name:'fixture.bin',destination:'/tmp/fixture.bin',status:'downloading',bytes:1048576,size:2097152,speed:1024,percent:50,retries:1};
  window.fixture={job,calls:[],offline:false,creates:[]};window.confirm=()=>true;
  window.orcaToolkitConfiguration={call:async()=>{throw Error('Settings fixture unavailable');}};
  window.orcaToolkitTools={call:async()=>({accounts:[],requiresRefresh:true})};
  window.orcaToolkitDownloads={call:async input=>{
   window.fixture.calls.push(input);if(window.fixture.offline)throw Error('Host disconnected');
   if(input.operation==='list'&&window.fixture.failNextList){window.fixture.failNextList=false;throw Error('Synthetic task refresh timeout');}if(input.operation==='list')return {jobs:[job],enabled:true,recovery:[],defaultFolder:'/tmp/downloads',remoteLocation:{source:'',directory:''}};
   if(input.operation==='pause-all')job.status='paused';
   if(input.operation==='action'){if(input.action==='pause')job.status='paused';if(input.action==='resume')job.status='downloading';if(input.action==='cancel')job.status='cancelled';if(input.action==='cleanup')job.cleanupWarning=null;}
   if(input.operation==='create'){window.fixture.creates.push(input.requestId);if(window.fixture.failRefreshAfterCreate){window.fixture.failRefreshAfterCreate=false;window.fixture.failNextList=true;}if(localStorage.getItem('drop-create'))throw Error('Synthetic lost create acknowledgement');return {job};}
   return {};
  }};
 });
 await page.goto(require('node:url').pathToFileURL(path.join(stage,'panel.html')).href);
 await page.waitForFunction(()=>document.getElementById('host-download-jobs').textContent.includes('MiB'));
 assert.match(await page.locator('#host-download-jobs').textContent(),/1.0 MiB \/ 2.0 MiB.*1.0 KiB\/s.*18m remaining/);
 await page.getByRole('button',{name:'Pause',exact:true}).click();await page.getByRole('button',{name:'Resume',exact:true}).click();
 await page.locator('#host-download-pause-all').click();await page.getByRole('button',{name:'Cancel',exact:true}).click();
 await page.evaluate(()=>{window.fixture.job.cleanupWarning='Revocation pending';});await page.locator('#host-download-refresh').click();
 assert.match(await page.locator('#host-download-jobs').textContent(),/Revocation pending/);
 await page.getByRole('button',{name:'Clear access',exact:true}).click();assert.doesNotMatch(await page.locator('#host-download-jobs').textContent(),/Revocation pending/);
 await page.evaluate(()=>{window.fixture.job.status='verifying';});await page.locator('#host-download-refresh').click();assert.match(await page.locator('#host-download-jobs').textContent(),/Verifying checksum/);
 await page.evaluate(()=>{window.fixture.job.status='complete';window.fixture.job.verified=true;window.fixture.job.bytes=2097152;window.fixture.job.percent=100;window.fixture.job.speed=0;});await page.locator('#host-download-refresh').click();assert.match(await page.locator('#host-download-jobs').textContent(),/Checksum verified/);
 await page.evaluate(()=>{window.fixture.offline=true;});await page.waitForFunction(()=>document.getElementById('host-download-create').disabled);
 assert.match(await page.locator('#host-download-error').textContent(),/Host disconnected/);
 await page.evaluate(()=>{window.fixture.offline=false;});await page.locator('#host-download-refresh').click();
 await page.locator('#host-download-source').fill('/project/fixture.bin');await page.locator('#host-download-create').click();await page.waitForFunction(()=>!document.getElementById('host-download-create').disabled);
 await page.locator('#host-download-create').click();await page.waitForFunction(()=>window.fixture.creates.length===2);
 const ids=await page.evaluate(()=>window.fixture.creates);assert.notEqual(ids[0],ids[1]);
 await page.evaluate(()=>{window.fixture.failRefreshAfterCreate=true;});
 await page.locator('#host-download-create').click();await page.waitForFunction(()=>!document.getElementById('host-download-create').disabled);
 const refreshUnknown=await page.evaluate(()=>window.fixture.creates.at(-1));
 await page.locator('#host-download-create').click();await page.waitForFunction(()=>!document.getElementById('host-download-create').disabled);
 assert.equal(await page.evaluate(()=>window.fixture.creates.at(-1)),refreshUnknown);
 await page.evaluate(()=>localStorage.setItem('drop-create','yes'));
 await page.locator('#host-download-create').click();await page.waitForFunction(()=>!document.getElementById('host-download-create').disabled);
 const unknown=await page.evaluate(()=>window.fixture.creates.at(-1));
 await page.reload();await page.waitForFunction(()=>!document.getElementById('host-download-create').disabled);
 await page.locator('#host-download-source').fill('/project/fixture.bin');
 await page.locator('#host-download-create').click();await page.waitForFunction(()=>window.fixture.creates.length===1);
 assert.equal(await page.evaluate(()=>window.fixture.creates[0]),unknown);
 await page.evaluate(()=>localStorage.removeItem('drop-create'));
 await page.locator('#host-downloads').screenshot({path:'/tmp/toolkit-downloads-7b11.png'});
 console.log('PASS: production staged UI at 1000x900; progress/speed/ETA, pause/resume/pause-all/cancel, cleanup warning, verification, offline/retry, fresh create IDs. Synthetic task backend; no transfer or remote connection.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
