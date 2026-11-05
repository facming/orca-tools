'use strict';
const path=require('node:path'),cp=require('node:child_process'),{promisify}=require('node:util');
const {ToolkitError}=require('../../platform/errors.cjs');
function validRemote(value){return typeof value==='string'&&value.startsWith('/')&&value.length<=4096&&!/[\x00-\x1f\x7f\\]/.test(value)&&!value.split('/').some(p=>p==='..'||p==='.');}
function rootsFrom(repos){
 if(!Array.isArray(repos))throw new ToolkitError('Remote project list is incompatible.');
 const roots=[...new Set(repos.map(r=>r?.path).filter(p=>validRemote(p)&&path.posix.normalize(p)!=='/').map(p=>path.posix.normalize(p).replace(/\/$/,'')))];
 if(!roots.length)throw new ToolkitError('Open an authorized project on the remote server in Orca first.');
 return roots;
}
function contains(root,file){return validRemote(file)&&(file===root||file.startsWith(root+'/'));}
async function listProjects(runtime,environment){
 try{
  const {stdout}=await promisify(cp.execFile)(runtime.cli,['repo','list','--environment',environment,'--json'],{env:runtime.childEnv,windowsHide:true,timeout:30000,maxBuffer:2*1024*1024});
  const reply=JSON.parse(stdout);if(!reply.ok)throw Error();return rootsFrom(reply.result?.repos);
 }catch{throw new ToolkitError('Unable to list authorized remote projects. Check the Orca environment and connection.');}
}
module.exports={validRemote,rootsFrom,contains,listProjects};
