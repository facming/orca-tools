'use strict';
const {Accounts}=require('./modules/accounts.cjs');
const {AccountPreferences}=require('./account-preferences.cjs');
const {MacGpu}=require('./modules/gpu-macos.cjs');
module.exports={create({runtime,readSettings,dataDir}){
 const accounts=new Accounts(runtime),gpu=new MacGpu(runtime);
 const workbench=require('./host-workbench.cjs').create(runtime);
 let snapshot=null,binding='',closed=false;
 return {
  close(){closed=true;workbench.close();accounts.clients.clear();snapshot=null;},
  async call(command){
   if(closed)throw Error('Toolkit session closed');
   const settings=await readSettings(),key=JSON.stringify(settings.accounts);
   if(key!==binding){snapshot=null;binding=key;}
   const current=async()=>!closed&&key===JSON.stringify((await readSettings()).accounts);
   if(command.operation.startsWith('accounts')){
    if(!settings.accounts.enabled)throw Error('Accounts are disabled. Enable them in Host settings.');
    if(command.operation==='accounts')return snapshot||{accounts:[],activeAccountId:null,requiresRefresh:true,message:'Refresh usage to connect to the configured remote environment.'};
    try{
     const preferences=new AccountPreferences(dataDir,{privateFiles:true});
     let result;
     if(command.operation==='accounts-select'){
      if(preferences.disabled(settings.accounts.environment,command.accountId))throw Error('Account is disabled. Enable it before switching.');
      result=await accounts.select(settings.accounts,{accountId:command.accountId,expectedActiveAccountId:command.expectedActiveAccountId},current);
     }else{
      result=await accounts.list(settings.accounts,command.operation==='accounts-refresh');
      if(!await current())throw Error('Remote connection changed. Refresh accounts.');
      if(command.operation==='accounts-disable'){
       if(!result.accounts.some(a=>a.id===command.accountId))throw Error('Account not found. Refresh accounts.');
       preferences.set(settings.accounts.environment,command.accountId,command.disabled);
      }
     }
     if(!await current())throw Error('Remote connection changed. Refresh accounts.');
     snapshot=preferences.apply(settings.accounts.environment,result);return snapshot;
    }catch(error){snapshot=null;throw error;}
   }
   if(!settings.gpu.enabled)throw Error('GPU tools are disabled. Enable them in Host settings.');
   if(command.operation==='workbench')return workbench.call(command.command);
   if(command.operation==='gpu-status')return gpu.status();
   if(command.operation==='gpu-workflows')return gpu.request('/api/workflows');
   throw Error('Unsupported Toolkit operation');
  }
 };
}};
