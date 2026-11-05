'use strict';
// Integration component for inclusion in a reviewed, sealed Orca main process.
// This file does NOT install an IPC route or trust the current generic worker.
const cp=require('node:child_process');
const {validate}=require('./commands.cjs');
const BRIDGE='/Library/PrivilegedHelperTools/OrcaToolkit/config-bridge';
function requestBridge(request){
 return new Promise(resolve=>{
  const child=cp.spawn(BRIDGE,[],{env:{PATH:'/usr/bin:/bin'},stdio:['pipe','pipe','ignore']});
  const chunks=[];let size=0,settled=false;
  const finish=result=>{if(settled)return;settled=true;clearTimeout(timer);child.stdin.destroy();resolve(result);};
  const offline={ok:false,error:'Configuration service unavailable; result may be unknown. Retry with the same request_id.'};
  const timer=setTimeout(()=>{child.kill();finish(offline);},31000);
  child.on('error',()=>finish(offline));child.stdin.on('error',()=>{});
  child.stdout.on('data',chunk=>{size+=chunk.length;if(size>2*1024*1024){child.kill();finish(offline);}else chunks.push(chunk);});
  child.on('close',code=>{if(code!==0)return finish(offline);try{const r=JSON.parse(Buffer.concat(chunks).toString('utf8'));finish(typeof r.ok==='boolean'?r:offline);}catch{finish(offline);}});
  // Keep the originating endpoint alive until authentication and reply. end()
  // closes Node's socket and macOS can no longer obtain its peer audit token.
  // The service reads one newline frame; it does not need EOF.
  child.stdin.write(JSON.stringify(request)+'\n');
 });
}
function createHostHandler(authorizeSession){
 if(typeof authorizeSession!=='function')throw Error('Host-owned sealed Toolkit session authorizer required');
 return async function handle(envelope,session){
  // The Host supplies session from its IPC connection table, never from payload.
  // It must bind the sealed builtin artifact, capability, lifecycle generation
  // and command origin; ordinary approved/user-editable plugins are insufficient.
  if(await authorizeSession(session)!==true)return {ok:false,error:'Unauthorized Toolkit configuration session.'};
  try{
   if(!envelope||envelope.version!==1||Object.keys(envelope).some(k=>!['version','request'].includes(k)))throw Error();
   const request=validate(envelope.request);
   return await requestBridge(request);
  }catch{return {ok:false,error:'Invalid Toolkit configuration request.'};}
 };
}
module.exports={createHostHandler};
