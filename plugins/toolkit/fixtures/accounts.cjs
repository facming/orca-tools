'use strict';
// Synthetic data and SDK only. Never load the installed Orca or user profile.
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {startServer}=require('../server.cjs'),settings=require('../settings.cjs');
function snapshot(active='a'){
 const usage=(id,used,age=0)=>({status:'ok',updatedAt:Date.now()-age,usageMetadata:{authProvenance:'managed:'+id},session:{usedPercent:used,resetsAt:Date.now()+3600000},weekly:{usedPercent:used,resetsAt:null}});
 return {claude:{activeAccountId:active,accounts:[{id:'a',email:'alice@example.invalid'},{id:'b',email:'bob@example.invalid'},{id:'c',email:'carol@example.invalid',needsSignIn:true}]},rateLimits:{claude:usage(active,24),inactiveClaudeAccounts:[{accountId:active==='a'?'b':'a',rateLimits:usage(active==='a'?'b':'a',66,600000)}]}};
}
function fixture(){
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'orca-mock-accounts-'));
 const f={root,calls:[],constructors:[],loads:0,value:snapshot(),failure:null,reply:null,gate:null,confirm:true,remote:true};
 class RuntimeClient{
  constructor(...args){f.constructors.push(args);this.isRemote=f.remote;}
  async call(method,params){
   f.calls.push({method,params});if(f.gate)await f.gate;
   if(f.failure)throw Object.assign(Error('synthetic private diagnostic'),{code:f.failure});
   if(f.reply)return f.reply;
   if(method==='accounts.selectClaude'&&f.confirm)f.value=snapshot(params.accountId);
   return {ok:true,_meta:{runtimeId:'mock-runtime'},result:structuredClone(f.value)};
  }
 }
 f.runtime={platform:'darwin',home:root,configDir:path.join(root,'config'),userDataPath:path.join(root,'empty-profile'),cli:path.join(root,'mock-cli'),loadSdk(){f.loads++;return {RuntimeClient};}};
 f.start=async()=>{
  fs.mkdirSync(f.runtime.configDir,{recursive:true,mode:0o700});
  const config=settings.defaults();config.accounts.environment='Mock remote';settings.save(f.runtime.configDir,config,settings.load(f.runtime.configDir,null,'darwin'),'darwin');
  f.server=await startServer({runtime:f.runtime,port:0,gpu:{async status(){return {online:false};}}});
  const html=await(await fetch(f.server.url)).text();
  f.headers={'X-Toolkit-Token':html.match(/const token='([a-f0-9]+)'/)[1],Origin:new URL(f.server.url).origin,'Content-Type':'application/json'};
  f.request=async(route,body)=>{
   const r=await fetch(f.server.url+'api/'+route,{headers:f.headers,method:body===undefined?'GET':'POST',...(body===undefined?{}:{body:JSON.stringify(body)})});
   return {status:r.status,body:await r.json()};
  };
  return f;
 };
 f.close=async()=>{await f.server?.close();fs.rmSync(root,{recursive:true,force:true});};
 return f;
}
module.exports={fixture,snapshot};
