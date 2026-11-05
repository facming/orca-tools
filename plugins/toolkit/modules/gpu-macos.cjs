'use strict';
const fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const {ToolkitError}=require('../platform/errors.cjs');
const url='http://127.0.0.1:18773/';
const checks=['installed','launch_agent','tailscale','token','peers','comfyui'];
function privatePath(file,directory=false){
 const info=fs.lstatSync(file);
 if(info.isSymbolicLink()||info.uid!==process.getuid()||(info.mode&0o777)!==(directory?0o700:0o600)||!(directory?info.isDirectory():info.isFile()))throw Error();
 for(let p=path.dirname(file);p!==path.dirname(p);p=path.dirname(p))if(fs.lstatSync(p).isSymbolicLink())throw Error();
}
class MacGpu {
 constructor(runtime){this.runtime=runtime;}
 credentials(){
  const root=this.runtime.gpuDir;privatePath(root,true);
  for(const name of ['config.json','launcher.json','token'])privatePath(path.join(root,name));
  const config=JSON.parse(fs.readFileSync(path.join(root,'config.json'),'utf8'));
  const launcher=JSON.parse(fs.readFileSync(path.join(root,'launcher.json'),'utf8'));
  const token=fs.readFileSync(path.join(root,'token'),'utf8').trim();
  if(config.platform!=='darwin'||config.data_dir!==root||launcher.platform!=='darwin'||launcher.label!=='org.orca.local-gpu-bridge'||!/^[a-f0-9]{64}$/.test(token))throw Error();
  return token;
 }
 async request(route='/api/health'){
  if(!['/api/health','/api/workflows'].includes(route))throw new ToolkitError('Only ComfyUI is supported on macOS.');
  let token;try{token=this.credentials();}catch{throw new ToolkitError('Install the macOS ComfyUI Bridge with private configuration first.');}
  return new Promise((resolve,reject)=>{
   const fail=()=>reject(new ToolkitError('macOS ComfyUI Bridge is offline. Check its LaunchAgent and local log.'));
   const req=http.get(url.slice(0,-1)+route,{timeout:10000,headers:{Authorization:'Bearer '+token}},res=>{
    let text='';res.setEncoding('utf8');res.on('data',chunk=>{text+=chunk;if(text.length>4*1024*1024)res.destroy();});
    res.on('error',fail);res.on('end',()=>{try{if(res.statusCode!==200)throw Error();resolve(JSON.parse(text));}catch{fail();}});
   });req.on('error',fail);req.on('timeout',()=>{req.destroy();fail();});
  });
 }
 async status(){
  try{
   const value=await this.request();
   const online=value.platform==='darwin'&&value.capability==='comfyui'&&value.ready===true&&checks.every(key=>value.checks?.[key]===true);
   return {online,status:online?'ready':'offline',capability:'comfyui',checks:value.checks};
  }catch{return {online:false,status:'offline',capability:'comfyui'};}
 }
 async open(){await this.request();return {url,ready:(await this.status()).online};}
}
module.exports={MacGpu};
