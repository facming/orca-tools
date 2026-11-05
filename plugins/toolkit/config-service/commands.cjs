'use strict';
const {ToolkitError}=require('../platform/errors.cjs');
const crypto=require('node:crypto');
// Both worker and sealed Host validate this closed protocol. No file paths,
// executable, environment, PID, signing requirement or claimed plugin identity.
const fields={status:[],read:['namespace'],patch:['namespace','store_id','epoch','revision','patch'],preview:['sources'],import:['preview_id','hashes'],backup:[],restore:['backup_id'],
 'lease-acquire':['task_id','target'],'lease-release':['task_id']};
const mutations=new Set(['patch','preview','import','restore','lease-acquire','lease-release']);
function validate(request){
 if(!request||typeof request!=='object'||Array.isArray(request)||typeof request.op!=='string'||!Object.hasOwn(fields,request.op))throw new ToolkitError('Unknown configuration operation.');
 const allowed=['op',...fields[request.op],...(mutations.has(request.op)?['request_id']:[])];
 if(Object.keys(request).some(k=>!allowed.includes(k))||fields[request.op].some(k=>!Object.hasOwn(request,k)))throw new ToolkitError('Invalid configuration request fields.');
 if(mutations.has(request.op)&&!(typeof request.request_id==='string'&&/^[a-f0-9-]{32,36}$/.test(request.request_id)))throw new ToolkitError('A stable configuration request_id is required.');
 if(['read','patch'].includes(request.op)&&!['settings','downloads-preferences'].includes(request.namespace))throw new ToolkitError('Unknown configuration namespace.');
 const object=v=>v&&typeof v==='object'&&!Array.isArray(v);
 const exact=(v,keys)=>object(v)&&Object.keys(v).length===keys.length&&keys.every(k=>Object.hasOwn(v,k));
 const names=['settings','downloads-preferences'];
 let valid=true;
 if(request.op==='preview')valid=exact(request.sources,names)&&Object.values(request.sources).every(v=>v===null||(typeof v==='string'&&v.length<=1400000&&/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(v)));
 if(request.op==='import')valid=typeof request.preview_id==='string'&&/^[a-f0-9]{32}$/.test(request.preview_id)&&exact(request.hashes,names)&&Object.values(request.hashes).every(v=>typeof v==='string'&&/^(?:[a-f0-9]{64}|explicit-defaults)$/.test(v));
 if(request.op==='restore')valid=typeof request.backup_id==='string'&&/^backup-[a-f0-9]{32}\.sqlite$/.test(request.backup_id);
 if(request.op==='patch')valid=['store_id','epoch'].every(k=>typeof request[k]==='string'&&/^[a-f0-9]{32}$/.test(request[k]))&&typeof request.revision==='string'&&/^[1-9][0-9]*$/.test(request.revision)&&Array.isArray(request.patch)&&request.patch.length>=1&&request.patch.length<=128&&request.patch.every(p=>exact(p,p?.op==='set'?['op','path','value']:['op','path'])&&['set','delete'].includes(p.op)&&Array.isArray(p.path)&&p.path.length>=1&&p.path.length<=16&&p.path.every(k=>typeof k==='string'&&k.length>0&&k.length<=256&&!['__proto__','prototype','constructor'].includes(k)));
 if(request.op.startsWith('lease-'))valid=typeof request.task_id==='string'&&/^[a-f0-9]{32}$/.test(request.task_id)&&(request.op==='lease-release'||exact(request.target,['environment','sshHost','sshUser','sshPort']));
 if(!valid)throw new ToolkitError('Invalid configuration operation payload.');
 if(Buffer.byteLength(JSON.stringify(request))>1900000)throw new ToolkitError('Configuration request too large.');
 return request;
}
function createCommands(client,{reload=async()=>{},beforeMutation=async()=>{}}={}){
 return async function command(input={}){
  if(!input||typeof input!=='object'||Array.isArray(input))throw new ToolkitError('Expected configuration command object.');
  if(Object.hasOwn(input,'op'))throw new ToolkitError('Use operation in configuration commands.');
  const {operation='status',...args}=input;
  const op=operation==='confirm'?'import':operation;
  const request=validate({op,...(mutations.has(op)?{request_id:crypto.randomUUID()}:{}),...args});
  await beforeMutation(request);
  const result=await client.request(request);
  // A committed result must remain visible even if refreshing runtime state fails.
  if(['patch','import','restore'].includes(op)){
   try{await reload();}catch{return {...result,runtime_refresh:'failed; reload Toolkit'};}
  }
  return result;
 };
}
module.exports={validate,createCommands};
