'use strict';
// Included only by a reviewed, sealed Orca main process; never an IPC registrar.
const cp=require('node:child_process'),net=require('node:net'),fs=require('node:fs');
const {validate}=require('./commands.cjs');
const BRIDGE='/Library/PrivilegedHelperTools/OrcaToolkit/config-bridge';
const OFFLINE={ok:false,error:'Configuration service unavailable; result may be unknown. Retry with the same request_id.'};
// createSocketPair MUST be an in-process native binding. An external helper
// would make the helper, not the Host, the kernel-authenticated socket creator.
function requestBridge(request,createSocketPair){
 return new Promise(resolve=>{
  let child,channel,timer,settled=false,hostFd,bridgeFd;
  const closeFd=fd=>{if(Number.isInteger(fd))try{fs.closeSync(fd);}catch{}};
  const finish=result=>{if(settled)return;settled=true;clearTimeout(timer);channel?.destroy();closeFd(hostFd);closeFd(bridgeFd);resolve(result);};
  const fail=()=>{child?.kill();finish(OFFLINE);};
  try{
   if(typeof createSocketPair!=='function')return finish(OFFLINE);
   ({hostFd,bridgeFd}=createSocketPair());
   if(!Number.isInteger(hostFd)||!Number.isInteger(bridgeFd)||hostFd<0||bridgeFd<0||hostFd===bridgeFd)throw Error('Invalid channel');
   channel=new net.Socket({fd:hostFd,readable:true,writable:true});hostFd=undefined;
   channel.on('error',fail);
   child=cp.spawn(BRIDGE,[],{env:{PATH:'/usr/bin:/bin'},stdio:['ignore','pipe','ignore',bridgeFd]});
   closeFd(bridgeFd);bridgeFd=undefined;
   const chunks=[];let size=0;
   timer=setTimeout(fail,31000);
   child.on('error',fail);
   child.stdout.on('error',fail);
   child.stdout.on('data',chunk=>{size+=chunk.length;if(size>2*1024*1024)fail();else chunks.push(chunk);});
   child.on('close',code=>{
    if(code!==0)return finish(OFFLINE);
    try{const r=JSON.parse(Buffer.concat(chunks).toString('utf8'));finish(r&&typeof r.ok==='boolean'?r:OFFLINE);}catch{finish(OFFLINE);}
   });
   // No end(): keep the creator and endpoint alive through authentication,
   // execution and the asynchronous reply. Timeout means outcome unknown.
   channel.write(JSON.stringify(request)+'\n');
  }catch{fail();}
 });
}
function createHostHandler(authorizeSession,{createSocketPair}={}){
 if(typeof authorizeSession!=='function')throw Error('Host-owned sealed Toolkit session authorizer required');
 return async function handle(envelope,session){
  // Session is supplied by the Host connection table, never by the payload.
  if(await authorizeSession(session)!==true)return {ok:false,error:'Unauthorized Toolkit configuration session.'};
  try{
   if(!envelope||envelope.version!==1||Object.keys(envelope).some(k=>!['version','request'].includes(k)))throw Error();
   return await requestBridge(validate(envelope.request),createSocketPair);
  }catch{return {ok:false,error:'Invalid Toolkit configuration request.'};}
 };
}
module.exports={createHostHandler,requestBridge};
