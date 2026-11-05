'use strict';
const fs=require('node:fs'),path=require('node:path');
const {ToolkitError}=require('./platform/errors.cjs');
const settings=require('./settings.cjs'),{DownloadManager}=require('./modules/downloads/engine.cjs');
const {validate:validateDownload}=require('./modules/downloads/settings.cjs');
const {Accounts}=require('./modules/accounts.cjs'),{Gpu}=require('./modules/gpu.cjs');
const {AccountPreferences}=require('./account-preferences.cjs');
class Controller {
 constructor(options){
  this.options=options;this.runtime=options.runtime;this.root=options.dataDir??this.runtime.configDir;this.downloads=null;this.downloadError='';this.settingsError='';
  this.accounts=options.accounts||new Accounts(this.runtime);this.gpu=options.gpu||new Gpu(this.runtime);this.loading=null;
  this.runtime.ensurePrivateDirectory?.(this.root);
  this.accountSnapshot=null;this.accountAvailability='preview';this.accountError='';this.accountRequest=0;
  this.accountPreferences=new AccountPreferences(this.root,{privateFiles:this.runtime.platform==='darwin'});
  try{this.settings=settings.load(this.root,this.runtime.platform==='darwin'?null:this.runtime.downloadsDir,this.runtime.platform);}catch{this.settings=settings.defaults();this.settingsError='Settings could not be loaded. Original file retained; repair it manually before saving.';}
 }
 async initialize(){await this.configureDownloads();}
 async configureDownloads(){
  if(this.downloads){await this.downloads.close();this.downloads=null;}
  this.downloadError='';this.downloadAvailability='needs-setup';if(!this.settings.downloads.enabled||!this.settings.downloads.environment)return;
  try{
   require('./modules/downloads/runtime.cjs').checkDownloadsRuntime(this.runtime);
   const config=validateDownload(this.settings.downloads);
   const create=this.options.createDownloads||(opts=>new DownloadManager(opts));
   this.downloads=create({dataDir:this.runtime.downloadsDir,runtime:this.runtime,...config,
    onConnection:state=>{this.downloadAvailability=state;if(state==='ready')this.downloadError='';},defaultFolder:path.join(this.runtime.home,'Downloads','Orca'),onComplete:this.options.onComplete});
   this.downloadAvailability=this.runtime.platform==='darwin'?'configured':'ready';
  }catch(e){this.downloadAvailability='error';this.downloadError=e instanceof ToolkitError?e.message:'Cannot initialize Downloads. Check settings and directory access.';}
 }
 async update(value){
  if(this.loading)throw new ToolkitError('Settings update is in progress.');
  const next=settings.validate(value);
  this.loading=(async()=>{
   const changed=JSON.stringify(next.downloads)!==JSON.stringify(this.settings.downloads);
   // Never silently move a live/resumable task to another server.
   const old=this.settings.downloads;
   const changedTarget=['environment','sshHost','sshUser','sshPort'].some(k=>old[k]!==next.downloads[k]);
   if(changedTarget){
    const folder=path.join(this.runtime.downloadsDir,'jobs');
    const jobs=this.downloads?.list()||[];
    if(fs.existsSync(folder))for(const name of fs.readdirSync(folder)){
     if(!/^[a-f0-9]{32}\.json$/.test(name))continue;
     try{jobs.push(JSON.parse(fs.readFileSync(path.join(folder,name),'utf8')));}catch{throw new ToolkitError('A saved download could not be read. Repair it before changing the target.');}
    }
    if(jobs.some(j=>!['complete','cancelled'].includes(j.status)||j.cleanupWarning))throw new ToolkitError('Finish or cancel pending downloads and clear temporary access before changing the remote target.');
   }
   const accountsChanged=JSON.stringify(next.accounts)!==JSON.stringify(this.settings.accounts);
   this.settings=settings.save(this.root,next,this.settings,this.runtime.platform);this.settingsError='';
   if(accountsChanged){this.accountRequest++;this.accountSnapshot=null;this.accountAvailability='preview';this.accountError='';}if(changed)await this.configureDownloads();
  })();
  try{await this.loading;}finally{this.loading=null;}
  return this.status();
 }
 async status(){
  let gpu={online:false};try{gpu=await this.gpu.status();}catch{}
  const preview=this.runtime.platform==='darwin';
  return {settings:this.settings,error:this.settingsError,modules:{
   accounts:{enabled:this.settings.accounts.enabled,status:!this.settings.accounts.enabled?'disabled':preview?this.accountAvailability:this.settings.accounts.environment?'configured':'needs-setup',manualRefresh:preview,error:this.accountError,accountCount:this.accountSnapshot?.accounts.length??null},
   downloads:{enabled:this.settings.downloads.enabled,status:!this.settings.downloads.enabled?'disabled':this.downloadAvailability||'needs-setup',available:!!this.downloads,error:this.downloadError,jobs:this.downloads?.list().length||0},
   gpu:{...(this.runtime.platform==='darwin'?{godot:gpu.godot||{state:'error'}}:{}),enabled:this.settings.gpu.enabled,status:!this.settings.gpu.enabled?'disabled':gpu.online?'ready':'offline'}
  }};
 }
 requireDownloads(){if(!this.downloads||!this.settings.downloads.enabled)throw new ToolkitError(this.downloadError||'Configure and enable Downloads first.');return this.downloads;}
 requireAccounts(){if(!this.settings.accounts.enabled)throw new ToolkitError('Accounts module is disabled.');return this.settings.accounts;}
 async openAccounts(){this.requireAccounts();return {opened:true,url:'/accounts/'};}
 requireRemoteAccounts(){return this.requireAccounts();}
 // Passive macOS reads never construct RuntimeClient or touch pairing/profile data.
 async readAccounts(){
  if(this.runtime.platform!=='darwin')return this.accountState();
  this.requireAccounts();
  return {...(this.accountSnapshot||{activeAccountId:null,accounts:[]}),availability:this.accountAvailability,requiresRefresh:!this.accountSnapshot,message:this.accountError||(!this.accountSnapshot?'macOS preview: click Refresh usage to connect to your configured remote environment. This may update its recent-use state in Orca.':'')};
 }
 async accountOperation(settings,operation){
  const request=++this.accountRequest;
  try{
   const state=await operation();
   if(this.settings.accounts!==settings)throw new ToolkitError('Remote connection changed. Refresh accounts.');
   const result=this.accountPreferences.apply(settings.environment,state);
   if(request===this.accountRequest){this.accountSnapshot=result;this.accountAvailability='ready';this.accountError='';}
   return result;
  }catch(error){
   if(request===this.accountRequest&&this.settings.accounts===settings){this.accountSnapshot=null;this.accountAvailability='error';this.accountError=error instanceof ToolkitError?error.message:'Remote accounts request failed. Refresh to retry.';}
   throw error;
  }
 }
 async accountState(refreshUsage=false){
  const settings=this.requireRemoteAccounts();
  if(this.accounts.switching)throw new ToolkitError('An account switch is in progress. Refresh shortly.');
  return this.accountOperation(settings,()=>this.accounts.list(settings,refreshUsage));
 }
 async setAccountDisabled(body){
  if(Object.keys(body).some(k=>!['accountId','disabled'].includes(k))||typeof body.accountId!=='string'||!body.accountId||body.accountId.length>200||typeof body.disabled!=='boolean')throw new ToolkitError('Invalid account preference.');
  const settings=this.requireAccounts(),state=await this.accountState();
  if(this.settings.accounts!==settings)throw new ToolkitError('Remote connection changed. Refresh accounts.');
  if(this.accounts.switching)throw new ToolkitError('An account switch is in progress. Refresh shortly.');
  if(!state.accounts.some(a=>a.id===body.accountId))throw new ToolkitError('Account not found. Refresh to try again.');
  this.accountPreferences.set(settings.environment,body.accountId,body.disabled);
  const result=this.accountPreferences.apply(settings.environment,state);this.accountSnapshot=result;return result;
 }
 async selectAccount(body){
  if(this.accounts.switching)throw new ToolkitError('An account switch is in progress. Refresh shortly.');
  const settings=this.requireRemoteAccounts();
  if(this.accountPreferences.disabled(settings.environment,body.accountId))throw new ToolkitError('Account is disabled. Enable it before switching.');
  return this.accountOperation(settings,()=>this.accounts.select(settings,body,()=>this.settings.accounts===settings&&settings.enabled&&!this.accountPreferences.disabled(settings.environment,body.accountId)));
 }
 requireGpu(){if(!this.settings.gpu.enabled)throw new ToolkitError('GPU module is disabled.');}
 async openGpu(){this.requireGpu();return this.gpu.open();}
 async close(){if(this.loading)await this.loading.catch(()=>{});if(this.downloads)await this.downloads.close();}
}
module.exports={Controller};
