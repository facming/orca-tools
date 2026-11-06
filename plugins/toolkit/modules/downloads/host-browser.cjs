'use strict';
const path=require('node:path');
const {RemoteBrowser}=require('../../remote-browser.cjs');
const {createClient}=require('../../config-service/client.cjs');
const {forPlatform}=require('../../download-preferences.cjs');
module.exports={create({runtime,request,dataDir}){
 const preferences=forPlatform('darwin',createClient(request)),browser=new RemoteBrowser(runtime);
 return {
  async snapshot(environment){return {defaultFolder:await preferences.defaultFolder(dataDir,runtime.home),remoteLocation:await preferences.remoteLocation(dataDir,environment)};},
  async browse(environment,options){
   const directory=options.directory??(await preferences.remoteLocation(dataDir,environment)).directory;
   const result=await browser.list(environment,{...options,directory});
   await preferences.rememberRemote(dataDir,environment,{directory:result.directory});return result;
  },
  async remember(environment,{folder,source}){
   if(folder)await preferences.rememberFolder(dataDir,folder);
   if(source)await preferences.rememberRemote(dataDir,environment,{source,directory:path.posix.dirname(source)});
  }
 };
}};
