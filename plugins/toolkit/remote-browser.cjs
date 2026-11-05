'use strict';
const {ToolkitError} = require('./platform/errors.cjs');
const path=require('node:path');
const {validRemote,contains,listProjects}=require('./modules/downloads/projects.cjs');
class RemoteBrowser {
 constructor(runtime,options={}){this.runtime=runtime;this.clients=new Map();this.listProjects=options.listProjects||(environment=>listProjects(runtime,environment));this.createClient=options.createClient||(environment=>require('./remote-runtime.cjs').createRemoteClient(runtime,environment));}
 async list(environment,{directory='',showHidden=false,query='',offset=0}={}){
  if(typeof environment!=='string'||!environment.trim())throw new ToolkitError('Configure the remote Orca environment first.');
  if(directory!==''&&!validRemote(directory))throw new ToolkitError('Use an absolute Linux directory path.');
  if(typeof showHidden!=='boolean'||typeof query!=='string'||query.length>200||!Number.isInteger(offset)||offset<0||offset>1000000)throw new ToolkitError('Invalid remote browser options.');
  let client=this.clients.get(environment);
  if(!client){client=this.createClient(environment);if(!client.isRemote)throw new ToolkitError('The selected Orca connection is not remote.');if(this.clients.size>=16)this.clients.clear();this.clients.set(environment,client);}
  if(!client.isRemote){this.clients.delete(environment);throw new ToolkitError('The selected Orca connection is not remote.');}
  const roots=await this.listProjects(environment);
  if(!directory)return {directory:'',parent:null,offset:0,total:roots.length,hasMore:false,entries:roots.map(root=>({name:root,path:root,isDirectory:true,isSymlink:false}))};
  if(!roots.some(root=>contains(root,directory)))throw new ToolkitError('Choose a directory inside an authorized remote project.');
  const root=roots.filter(root=>contains(root,directory)).sort((a,b)=>b.length-a.length)[0];
  const segments=path.posix.normalize(directory).slice(root.length).split('/').filter(Boolean);
  let current=root,value;
  for(let index=0;index<=segments.length;index++){
   let response;
   try{response=await client.call('files.browseServerDir',{path:current});}
   catch{this.clients.delete(environment);throw new ToolkitError('Unable to list this remote directory. Check its path, permissions and the Orca connection.');}
   if(!response?.ok||typeof response._meta?.runtimeId!=='string'||!response._meta.runtimeId.trim())throw new ToolkitError('The remote directory request was not confirmed.');
   value=response.result;
   if(value?.pathFlavor!=='posix'||!validRemote(value.resolvedPath)||!Array.isArray(value.entries))throw new ToolkitError('The server did not return a supported Linux directory listing.');
   if(!contains(root,value.resolvedPath)||path.posix.normalize(value.resolvedPath)!==current)throw new ToolkitError('Resolved directory is outside the requested project path.');
   if(index<segments.length){
    const entry=value.entries.find(e=>e?.name===segments[index]);
    if(!entry?.isDirectory||entry.isSymlink!==false)throw new ToolkitError('Only confirmed project directories can be browsed; symbolic links are not followed.');
    current=path.posix.join(current,segments[index]);
   }
  }
  const needle=query.toLocaleLowerCase();
  const entries=value.entries.filter(e=>e&&typeof e.name==='string'&&e.name&&e.name!=='.'&&e.name!=='..'&&!e.name.includes('/')&&!/[\x00-\x1f\x7f\\]/.test(e.name)&&typeof e.isDirectory==='boolean')
   .filter(e=>(showHidden||!e.name.startsWith('.'))&&e.name.toLocaleLowerCase().includes(needle))
   .sort((a,b)=>Number(b.isDirectory)-Number(a.isDirectory)||a.name.localeCompare(b.name));
  const limit=200;
  return {directory:current,parent:roots.some(root=>contains(root,path.posix.dirname(current)))?path.posix.dirname(current):null,offset,total:entries.length,hasMore:offset+limit<entries.length,
   entries:entries.slice(offset,offset+limit).map(e=>({name:e.name,path:path.posix.join(current,e.name),isDirectory:e.isDirectory,isSymlink:e.isSymlink===true}))};
 }
}
module.exports={RemoteBrowser,validRemote};
