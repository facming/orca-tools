'use strict';
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),crypto=require('node:crypto');
const {ToolkitError,publicMessage}=require('./platform/errors.cjs');
const {Controller}=require('./controller.cjs');
const {RemoteBrowser,validRemote}=require('./remote-browser.cjs');
const {GpuAdmin}=require('./gpu-admin.cjs'),{compose}=require('./compose-ui.cjs');
async function startServer(options){
 const preferences=require('./download-preferences.cjs').forPlatform(options.runtime.platform,options.runtime.configClient);
 const dataDir=options.dataDir??options.runtime.configDir;
 const controller=new Controller({...options,dataDir});await controller.initialize();
 const configuration=require('./config-service/commands.cjs').createCommands(controller.configClient,{reload:()=>controller.reloadConfiguration(),beforeMutation:r=>controller.prepareConfigurationChange(r)});const token=crypto.randomBytes(32).toString('hex');let port;
 const picker=options.directoryPicker||{choose:initial=>options.runtime.chooseDirectory(initial),close:()=>options.runtime.closePicker?.()};
 const remoteBrowser=options.remoteBrowser||new RemoteBrowser(options.runtime);
 const gpuAdmin=options.gpuAdmin||(options.runtime.platform==='darwin'?controller.gpu.mac:new GpuAdmin());
 const server=http.createServer(async(req,res)=>{
  const origin='http://127.0.0.1:'+port;
  const send=(code,body,type='application/json; charset=utf-8')=>{const bytes=Buffer.from(typeof body==='string'?body:JSON.stringify(body));res.writeHead(code,{'Content-Type':type,'Content-Length':bytes.length,'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','Content-Security-Policy':`default-src 'none'; connect-src 'self'; style-src 'unsafe-inline'; script-src 'nonce-${token}'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'`});res.end(bytes);};
  try{
   if(req.headers.host!==`127.0.0.1:${port}`)return send(403,{error:'Invalid Host.'});
   if(req.method==='GET'&&req.url==='/favicon.ico')return send(204,'');
   if(req.method==='GET'&&['/','/downloads/','/accounts/'].includes(req.url)){
    const html=compose(req.url==='/accounts/'?'accounts':req.url==='/downloads/'?'downloads':'overview',options.runtime.platform);
    return send(200,html.replaceAll('__TOKEN__',token),'text/html; charset=utf-8');
   }
   if(req.headers['x-toolkit-token']!==token&&req.headers['x-download-token']!==token)return send(403,{error:'Session changed. Reload this page.'});
   if(req.method==='GET'&&req.url==='/api/accounts')return send(200,await controller.readAccounts());
   if(options.runtime.platform==='darwin'&&['/api/config-service','/api/configuration'].includes(req.url))return send(403,{error:'Open configuration maintenance from the Orca Host menu. Localhost tokens do not authorize configuration access.'});
   if(req.method==='GET'&&req.url==='/api/status'){if(options.runtime.configClient)try{await controller.reloadConfiguration();}catch{}return send(200,await controller.status());}
   if(req.method==='GET'&&['/api/gpu/native','/api/gpu/workflows','/api/gpu/health'].includes(req.url)){
    controller.requireGpu();
    if(options.runtime.platform==='darwin'&&(req.method!=='GET'||!['/api/gpu/workflows','/api/gpu/health'].includes(req.url)))throw new ToolkitError('Only ComfyUI is supported on macOS.');
    return send(200,await gpuAdmin.request(req.url.replace('/api/gpu/','/api/')));
   }
   if(options.runtime.platform==='darwin'&&req.method==='POST'&&req.url.startsWith('/api/downloads/'))return send(403,{error:'Downloads require the Orca main Host maintenance window.'});
   if(req.method==='GET'&&req.url==='/api/downloads/jobs'){
    if(options.runtime.platform==='darwin')return send(200,{availability:(await controller.status()).modules.downloads,jobs:[]});
    const availability=(await controller.status()).modules.downloads;
    return send(200,{availability,jobs:controller.downloads?.list()||[],defaultFolder:await preferences.defaultFolder(dataDir,options.runtime.home),remoteLocation:await preferences.remoteLocation(dataDir,controller.settings.downloads.environment)});
   }
   if(req.method!=='POST')return send(404,{error:'Not found.'});
   if(req.headers.origin!==origin)return send(403,{error:'Invalid Origin.'});
   if(req.headers['content-type']?.split(';')[0]!=='application/json')return send(415,{error:'Expected JSON.'});
   let size=0;const limit=req.url==='/api/gpu/import'?4*1024*1024:req.url==='/api/configuration'?1900000:16384;const chunks=[];for await(const chunk of req){size+=chunk.length;if(size>limit)throw new ToolkitError('Request too large.');chunks.push(chunk);}
   const body=JSON.parse(Buffer.concat(chunks).toString('utf8'));if(!body||typeof body!=='object'||Array.isArray(body))throw new ToolkitError('Expected an object.');
   if(req.url==='/api/configuration'&&options.runtime.platform==='darwin')return send(200,await configuration(body));
   if(req.url==='/api/settings'){if(options.runtime.configClient)throw new ToolkitError('Use a versioned configuration patch; reload Toolkit.');return send(200,await controller.update(body));}
   if(req.url==='/api/accounts/open')return send(200,await controller.openAccounts());
   if(req.url==='/api/accounts/refresh'){if(Object.keys(body).length)throw new ToolkitError('Unexpected refresh option.');return send(200,await controller.accountState(true));}
   if(req.url==='/api/accounts/select')return send(200,await controller.selectAccount(body));
   if(req.url==='/api/accounts/disable')return send(200,await controller.setAccountDisabled(body));
   if(req.url==='/api/gpu/open')return send(200,await controller.openGpu());
   if(req.url.startsWith('/api/gpu/')){
    controller.requireGpu();
    if(options.runtime.platform==='darwin'&&(req.method!=='GET'||!['/api/gpu/workflows','/api/gpu/health'].includes(req.url)))throw new ToolkitError('Only ComfyUI is supported on macOS.');
    return send(200,await gpuAdmin.request(req.url.replace('/api/gpu/','/api/'),body));
   }
   if(req.url==='/api/downloads/remote-browse'){
    controller.requireDownloads();
    if(Object.keys(body).some(k=>!['directory','showHidden','query','offset'].includes(k)))throw new ToolkitError('Unexpected remote browser option.');
    const environment=controller.settings.downloads.environment;
    let result;try{result=await remoteBrowser.list(environment,{...body,directory:body.directory??(await preferences.remoteLocation(dataDir,environment)).directory});}
    catch(error){controller.downloadAvailability='error';controller.downloadError=publicMessage(error);throw error;}
    if(controller.settings.downloads.environment!==environment)throw new ToolkitError('Remote connection changed. Reopen the file browser.');
    controller.downloadAvailability=result.directory?'ready':'configured';controller.downloadError='';
    await preferences.rememberRemote(dataDir,environment,{directory:result.directory});
    return send(200,result);
   }
   if(req.url==='/api/downloads/remote-location'){
    controller.requireDownloads();
    if(Object.keys(body).length!==1||!Object.hasOwn(body,'source')||typeof body.source!=='string'||(body.source!==''&&!validRemote(body.source)))throw new ToolkitError('Use an absolute remote file path.');
    const changes={source:body.source};if(body.source)changes.directory=path.posix.dirname(body.source);
    const saved=await preferences.rememberRemote(dataDir,controller.settings.downloads.environment,changes);
    return send(200,{saved});
   }
   if(req.url==='/api/downloads/choose-folder'){
    if(!controller.settings.downloads.enabled)throw new ToolkitError('Downloads module is disabled.');
    if(Object.keys(body).some(k=>k!=='initialDirectory'))throw new ToolkitError('Unexpected folder selection setting.');
    const result=await picker.choose(body.initialDirectory??await preferences.defaultFolder(dataDir,options.runtime.home));
    if(!result.cancelled&&!await preferences.rememberFolder(dataDir,result.path))result.warning='Folder selected, but the preference could not be saved.';
    return send(200,result);
   }
   if(req.url==='/api/downloads/jobs'){
    const job=await controller.requireDownloads().create(body);await preferences.rememberFolder(dataDir,body.folder);
    await preferences.rememberRemote(dataDir,controller.settings.downloads.environment,{source:body.source,directory:path.posix.dirname(body.source)});return send(201,{job});
   }
   if(req.url==='/api/downloads/action')return send(200,{job:await controller.requireDownloads().action(body.id,body.action)});
   if(req.url==='/api/downloads/pause-all'){await controller.requireDownloads().pauseAll();return send(200,{ok:true});}
   if(req.url==='/api/downloads/open-folder'){
    const job=controller.requireDownloads().jobs.get(body.id);if(!job)throw new ToolkitError('Download not found.');
    await options.runtime.openPath(path.dirname(job.destination));return send(200,{ok:true});
   }
   return send(404,{error:'Not found.'});
  }catch(e){
   if(!res.headersSent)send(400,{error:publicMessage(e)});else res.destroy();
  }
 });
 try{await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(options.port??18774,'127.0.0.1',resolve);});}catch(e){await controller.close();throw Error('Toolkit could not bind its local management port.');}
 port=server.address().port;return {url:`http://127.0.0.1:${port}/`,controller,configuration,async close(){picker.close?.();await new Promise(resolve=>server.close(resolve));await controller.close();}};
}
module.exports={startServer};
