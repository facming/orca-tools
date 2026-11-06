import {fileURLToPath} from 'node:url';
import runtimeModule from './platform/index.cjs';
import hub from './server.cjs';
import hostConfiguration from './config-service/host-client.cjs';

let service;
export default async function activate(api) {
  const runtime=runtimeModule.createRuntime({pluginRoot:fileURLToPath(new URL('.',import.meta.url))});
  if(runtime.platform==='darwin')runtime.configClient=hostConfiguration.createHostClient(api);
  service=await hub.startServer({runtime,
    async onComplete(job){try{await api.host.call('notifications.show',{title:'Download complete',body:job.name+' · SHA-256 verified'});}catch{}}});
  const hostRequired=()=>{throw Error('On macOS, open Help → Orca Toolkit → Accounts, Downloads, GPU & native tools, or Settings. These actions require the Orca Host window.');};
  const open=async(url,args={})=>{
    if(runtime.platform==='darwin')return hostRequired();
    if(args.openBrowser!==false)await runtime.openUrl(url);
    return {url,ready:true};
  };
  api.commands.register('toolkit.open',args=>open(service.url,args));
  api.commands.register('toolkit.downloads',args=>open(service.url+'#downloads',args));
  api.commands.register('toolkit.accounts',args=>open(service.url+'#accounts',args));
  api.commands.register('toolkit.gpu',async(args={})=>{
    if(runtime.platform==='win32')await service.controller.openGpu();
    return open(service.url+'#workflows',args);
  });
  api.commands.register('toolkit.configuration',args=>runtime.platform==='darwin'?hostRequired():open(service.url+'#settings',args));
  api.commands.register('toolkit.pause-downloads',async()=>{if(runtime.platform==='darwin')return hostRequired();await service.controller.requireDownloads().pauseAll();return {ok:true};});
  api.log('Orca Toolkit is ready. Ctrl+Alt+T opens the control center.');
  // open() starts both ComfyUI and the authenticated GPU bridge; it does not
  // open a browser. Starting only ComfyUI leaves every remote MCP offline.
  if(runtime.platform==='win32'&&service.controller.settings.gpu.enabled)service.controller.gpu.open().catch(()=>api.log('GPU bridge startup failed. Check the local GPU bridge log.'));
}
export async function deactivate(){if(service){await service.close();service=null;}}
