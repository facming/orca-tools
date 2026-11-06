'use strict';
const {ToolkitError} = require('../platform/errors.cjs');
const {createRemoteClient,remoteAccountError}=require('../remote-runtime.cjs');
function maskedEmail(value){if(typeof value!=='string')return '***';const match=value.match(/^([^@\s]+)@([^@\s]+)$/);return match?match[1][0]+'***@'+match[2]:'***';}
function timestamp(value){if(typeof value==='number'&&Number.isFinite(value))return value;if(typeof value==='string'&&/^\d{4}-\d\d-\d\dT/.test(value)&&Number.isFinite(Date.parse(value)))return new Date(value).toISOString();return null;}
function snapshotBinding(result,id){
 const state=result.claude,account=Array.isArray(state?.accounts)?state.accounts.find(a=>a.id===id):null,target=result.rateLimits?.claudeTarget;
 if(!account||state.activeAccountId!==id||!target)return false;
 if(account.managedAuthRuntime==='host')return target.runtime==='host'&&target.wslDistro==null&&state.activeAccountIdsByRuntime?.host===id;
 return account.managedAuthRuntime==='wsl'&&typeof account.wslDistro==='string'&&account.wslDistro.length>0&&target.runtime==='wsl'&&target.wslDistro===account.wslDistro&&state.activeAccountIdsByRuntime?.wsl?.[account.wslDistro]===id;
}
function missingProvenance(usage){return usage?.usageMetadata?.authProvenance==null;}
function usageDetails(result,id,verifiedActiveId,strictProvenance){
 const limits=result.rateLimits||{},active=id===result.claude.activeAccountId;
 const candidate=active?limits.claude:limits.inactiveClaudeAccounts?.find(a=>a.accountId===id)?.rateLimits;
 const updatedAt=timestamp(candidate?.updatedAt),fail=candidate?.usageMetadata?.failureKind;
 const empty=(code,message)=>({usage:null,usageStatus:{code,state:['unavailable','not-reported','unverified-account'].includes(code)?'unknown':'error',message,updatedAt}});
 if(!candidate)return empty('unavailable','Usage has not been reported yet. Refresh usage to check.');
 if(fail==='stale-token')return empty('stale-token','Usage credentials expired. Start Claude with this account; sign in again if needed.');
 if(fail==='rate-limited')return empty('rate-limited','Usage is temporarily rate limited. Try again later.');
 if(['missing-credentials','unreadable-credentials'].includes(fail))return empty('missing-credentials','Usage credentials are unavailable. Sign in to this account again if needed.');
 if(candidate.status==='error'||candidate.status==='unavailable')return empty('upstream-unavailable','Orca could not retrieve usage. Refresh usage or check this account in Orca.');
 const provenance=candidate.usageMetadata?.authProvenance;
 const explicit=['managed:'+id,'profile:'+id].includes(provenance);
 // Orca invalidates the target's usage cache on account changes. For older
 // snapshots lacking provenance, verify the selection/target mapping twice.
 const snapshot=active&&missingProvenance(candidate)&&verifiedActiveId===id&&snapshotBinding(result,id)&&candidate.status==='ok'&&!candidate.error;
 if((strictProvenance&&!explicit)||(active&&!explicit&&!snapshot)||(!active&&provenance!=null&&!explicit))return empty('unverified-account','Cannot confirm which account this usage belongs to. Refresh usage to retry.');
 const clean={updatedAt};let hasUsage=false;
 for(const key of ['session','weekly']){const w=candidate[key],used=w?.usedPercent;clean[key]=w?{usedPercent:typeof used==='number'&&Number.isFinite(used)?Math.max(0,Math.min(100,used)):null,resetsAt:timestamp(w.resetsAt)}:null;if(clean[key]?.usedPercent!=null)hasUsage=true;}
 if(!hasUsage)return empty('not-reported',candidate.status==='fetching'?'Usage is refreshing. Check again shortly.':'Orca has not reported usage windows for this account.');
 const ms=typeof updatedAt==='number'?(updatedAt<1e12?updatedAt*1000:updatedAt):Date.parse(updatedAt);
 const stale=!Number.isFinite(ms)||Date.now()-ms>5*60000;
 return {usage:clean,usageStatus:{code:'ready',state:stale?'stale':'ready',message:candidate.status==='fetching'?'Refreshing; showing the last reported usage.':stale?'Showing cached usage. Refresh for the latest values.':'',updatedAt,stale,source:snapshot?'runtime-snapshot':'account-bound'}};
}
function publicState(result,verifiedActiveId=null,strictProvenance=false){
 if(!result?.claude||!Array.isArray(result.claude.accounts))throw new ToolkitError('The remote account response is unavailable.');
 const active=result.claude.activeAccountId;
 return {activeAccountId:typeof active==='string'?active:null,accounts:result.claude.accounts.filter(a=>typeof a.id==='string').map(a=>({id:a.id,email:maskedEmail(a.email),current:a.id===active,needsSignIn:a.needsSignIn===true,...usageDetails(result,a.id,verifiedActiveId,strictProvenance)}))};
}
class Accounts {
 constructor(runtime,options={}){this.runtime=runtime;this.createClient=options.createClient||(env=>createRemoteClient(runtime,env));this.clients=new Map();this.switching=false;}
 async call(settings,method,params,identity){
  if(!settings.environment)throw new ToolkitError('Set the remote environment in Toolkit settings.');
  let client=this.clients.get(settings.environment);
  try{
   if(!client){client=this.createClient(settings.environment);if(this.clients.size>=16)this.clients.clear();this.clients.set(settings.environment,client);}
   if(client.isRemote!==true)throw new ToolkitError('Unable to reach remote accounts: the selected environment is not remote.');
   const r=await client.call(method,params);
   if(r?.ok!==true)throw remoteAccountError(r?.error);
   if(typeof r._meta?.runtimeId!=='string'||!r._meta.runtimeId.trim())throw new ToolkitError('Remote accounts RPC is incompatible: runtime identity is missing.');
   if(identity){if(identity.runtimeId&&identity.runtimeId!==r._meta.runtimeId)throw new ToolkitError('Remote runtime changed. Refresh to confirm the current account.');identity.runtimeId=r._meta.runtimeId;}
   return r.result;
  }catch(error){this.clients.delete(settings.environment);throw error instanceof ToolkitError?error:remoteAccountError(error);}
 }
 async list(settings,refreshUsage=false,identity={}){
  const first=await this.call(settings,'accounts.list',{refreshUsage},identity),id=first?.claude?.activeAccountId,candidate=first?.rateLimits?.claude;
  if(this.runtime.platform==='darwin')return publicState(first,null,true);
  if(typeof id==='string'&&missingProvenance(candidate)&&candidate?.status==='ok'&&!candidate.error&&snapshotBinding(first,id)){
   const second=await this.call(settings,'accounts.list',{refreshUsage:false},identity);
   return publicState(second,snapshotBinding(second,id)?id:null);
  }
  return publicState(first);
 }
 async select(settings,body,isCurrent=()=>true){
  if(Object.keys(body).some(k=>!['accountId','expectedActiveAccountId'].includes(k))||typeof body.accountId!=='string'||!body.accountId.trim()||body.accountId.length>200||!(typeof body.expectedActiveAccountId==='string'||body.expectedActiveAccountId===null))throw new ToolkitError('Invalid account selection.');
  if(this.switching)throw new ToolkitError('An account switch is in progress. Refresh shortly.');
  this.switching=true;
  try{
   const identity={};const state=await this.list(settings,false,identity);const account=state.accounts.find(a=>a.id===body.accountId);
   if(!await isCurrent())throw new ToolkitError('Remote connection changed. Refresh before switching.');
   if(!account)throw new ToolkitError('Account not found. Refresh to try again.');
   if(account.needsSignIn)throw new ToolkitError('Sign in again on the remote host to use this account.');
   if(body.expectedActiveAccountId!==state.activeAccountId)throw new ToolkitError('The active account has changed. Refresh before switching.');
   if(account.current)return state;
   await this.call(settings,'accounts.selectClaude',{accountId:account.id},identity);
   const after=await this.list(settings,true,identity);
   if(!await isCurrent())throw new ToolkitError('Remote connection changed. Refresh to check the selected account.');
   if(after.activeAccountId!==account.id)throw new ToolkitError('Switch not confirmed. Refresh to check the active account.');
   return after;
  }finally{this.switching=false;}
 }
}
module.exports={Accounts,publicState};
