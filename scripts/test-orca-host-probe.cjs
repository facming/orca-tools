'use strict';
// Read-only installed SDK probe; starts a disposable worker, never the desktop
// profile. Usage: node scripts/test-orca-host-probe.cjs /path/to/Orca.app
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),cp=require('node:child_process'),assert=require('node:assert/strict');
(async()=>{
 if(process.platform!=='darwin')throw Error('Requires macOS');
 const app=fs.realpathSync(process.argv[2]),entry=path.join(app,'Contents/Resources/app.asar.unpacked/out/main/plugin-host-entry.js');
 const home=fs.mkdtempSync(path.join(os.tmpdir(),'orca-sdk-probe-'));
 const plugin=path.join(home,'Library/Application Support/orca/plugins/local-tools.toolkit/probe');
 fs.cpSync(path.join(__dirname,'../plugins/toolkit'),plugin,{recursive:true});
 // Only the port and GPU test double differ. Production main, configuration
 // client, commands, server and the installed SDK worker are executed unchanged.
 fs.writeFileSync(path.join(plugin,'probe.mjs'),`import activate,{deactivate} from './main.mjs';
import hub from './server.cjs';
const start=hub.startServer;hub.startServer=options=>start({...options,port:0,gpu:{status:async()=>({online:false})},accounts:{}});
export default activate;export {deactivate};`);
 let child,timer;
 try{
  child=cp.fork(entry,[],{execPath:path.join(app,'Contents/MacOS/Orca'),execArgv:[],env:{HOME:home,TMPDIR:home,PATH:'/usr/bin:/bin',ELECTRON_RUN_AS_NODE:'1',ORCA_BACKGROUND_LAUNCH:'1'},serialization:'json',stdio:['ignore','ignore','pipe','ipc']});
  child.stderr.resume();let next=1;const waiting=new Map();
  const ready=new Promise((resolve,reject)=>{
   timer=setTimeout(()=>{child.kill();reject(Error('SDK probe timed out'));},20000);
   child.on('error',reject);child.on('exit',code=>{reject(Error('Worker exited: '+code));for(const callback of waiting.values())callback({error:{message:'Worker exited'}});waiting.clear();});
   child.on('message',message=>{
    if(message.event==='fatal')reject(Error('Worker activation failed in isolated profile'));
    if(message.event==='ready')resolve(message.payload);
    if(message.kind==='req')reject(Error('Unexpected host call from isolated probe'));
    if(message.kind==='res'){const callback=waiting.get(message.id);waiting.delete(message.id);callback?.(message);}
   });
  });
  child.send({orca:1,kind:'evt',event:'init',payload:{pluginId:'local-tools.toolkit',pluginRoot:plugin,mainEntry:'probe.mjs',grantedCapabilities:[]}});
  const registered=await ready;assert.ok(registered.commands.includes('toolkit.configuration'));
  const invoke=(commandId,args)=>new Promise(resolve=>{const id=next++;waiting.set(id,resolve);child.send({orca:1,kind:'req',id,method:'command.invoke',params:{commandId,args}});});
  const status=await invoke('toolkit.configuration',{operation:'status'});
  assert.match(status.error?.message,/Host menu/);
  const opened=await invoke('toolkit.open',{openBrowser:false});assert.equal(opened.result.ready,true);
  const url=opened.result.url,html=await (await fetch(url)).text(),token=html.match(/const token='([a-f0-9]+)'/)[1];
  const state=await (await fetch(url+'api/status',{headers:{'X-Toolkit-Token':token}})).json();
  assert.equal(state.configService,'host-required');assert.equal(state.configurationVersion,null);
  assert.equal(fs.existsSync(path.join(home,'Library/Application Support/OrcaTools/settings.json')),false);
  console.log('PASS: installed Orca SDK command dispatch -> production Toolkit -> host-required; isolated worker/UI stay available, no Bridge/config JSON fallback');
  child.send({orca:1,kind:'evt',event:'shutdown'});
  await new Promise(resolve=>child.once('exit',resolve));
 }finally{clearTimeout(timer);if(child&&child.exitCode===null&&child.signalCode===null){child.kill();await new Promise(resolve=>child.once('exit',resolve));}fs.rmSync(home,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exitCode=1;});
