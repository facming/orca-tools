'use strict';
const cp=require('node:child_process'),crypto=require('node:crypto');
const {ToolkitError}=require('../platform/errors.cjs');
const bridge='/Library/PrivilegedHelperTools/OrcaToolkit/config-bridge';
const mapResult=(value,fn)=>value&&typeof value.then==='function'?value.then(fn):fn(value);
function request(body){
 const result=cp.spawnSync(bridge,[],{input:JSON.stringify(body)+'\n',encoding:'utf8',timeout:31000,maxBuffer:2*1024*1024,env:{PATH:'/usr/bin:/bin'}});
 if(result.error||result.status!==0)throw new ToolkitError('Configuration service not installed or unavailable. Install the isolated service; JSON fallback is disabled.');
 let r;try{r=JSON.parse(result.stdout);}catch{throw new ToolkitError('Invalid configuration service response.');}
 if(!r.ok)throw new ToolkitError(r.error);return r.result;
}
function diff(before,after,prefix=[]){
 const ops=[];for(const [k,v] of Object.entries(after)){
  if(['__proto__','constructor','prototype'].includes(k))throw new ToolkitError('Invalid configuration path.');
  const p=[...prefix,k],old=before?.[k];
  if(v&&typeof v==='object'&&!Array.isArray(v)&&old&&typeof old==='object'&&!Array.isArray(old))ops.push(...diff(old,v,p));
  else if(JSON.stringify(old)!==JSON.stringify(v))ops.push({op:'set',path:p,value:v});
 }return ops; // Missing UI fields never imply deletion; explicit delete uses patch API.
}
function createClient(request){
 const snapshots=new WeakMap();
 const remember=(namespace,r)=>{const value=JSON.parse(r.raw_json);snapshots.set(value,{...r,namespace,original:structuredClone(value)});return value;};
 const mutate=body=>request({request_id:crypto.randomUUID(),...body});
 const read=namespace=>mapResult(request({op:'read',namespace}),r=>remember(namespace,r));
 function save(namespace,value,previous){
 const base=snapshots.get(previous);if(!base||base.namespace!==namespace)throw new ToolkitError('Reload configuration before saving.');
 const patch=diff(base.original,value);if(!patch.length)return previous;
 const r=mutate({op:'patch',namespace,store_id:base.store_id,epoch:base.epoch,revision:base.revision,patch});
 return mapResult(r,r=>remember(namespace,r));
}
 const version=value=>{const r=snapshots.get(value);return r?{store_id:r.store_id,epoch:r.epoch,revision:r.revision}:null;};
 return {request,mutate,read,save,version};
}
module.exports={...createClient(body=>module.exports.request(body)),request,diff,createClient,mapResult};
