'use strict';
const fs=require('node:fs'),path=require('node:path');
function fragment(file){
 const html=fs.readFileSync(path.join(__dirname,file),'utf8');
 const style=html.match(/<style>([\s\S]*?)<\/style>/i)?.[1]||'';
 const script=html.match(/<script nonce="__TOKEN__">([\s\S]*?)<\/script>/i)?.[1];
 if(!script)throw Error('Management module has no script: '+file);
 const content=html.slice(html.indexOf('<main>'),html.indexOf('<script nonce='));
 return {style,script,content};
}
function compose(initial='overview',platform='win32'){
 let shell=fs.readFileSync(path.join(__dirname,'index.html'),'utf8').replace('__SHELL_STYLE__',fs.readFileSync(path.join(__dirname,'theme.css'),'utf8'));
 shell=shell.replace('__CONFIGURATION_PANEL__',platform==='darwin'?fs.readFileSync(path.join(__dirname,'configuration-panel.html'),'utf8'):'');
 if(platform==='darwin')shell=shell.replace('<main>', '<main><p role="status">macOS: use Help → Orca Toolkit in the Orca app for Accounts, Downloads, GPU &amp; native tools, and Settings. The Host menu authorizes these operations.</p>');
 const skin=fs.readFileSync(path.join(__dirname,'module-theme.css'),'utf8');
 const definitions=[['accounts','accounts.html','/api/','/api/'],['downloads','modules/downloads/index.html','/api/','/api/downloads/'],['gpu',platform==='darwin'?'modules/gpu-macos-panel.html':'modules/gpu-panel.html','/api/','/api/gpu/']];
 const scripts=definitions.map(([name,file,from,to])=>{
  const f=fragment(file);let script=f.script;
  if(name==='gpu')script=script.replaceAll("'X-Admin-Token'","'X-Toolkit-Token'");
  script=script.replaceAll("'"+from,"'"+to);
  if(name==='gpu'&&platform==='darwin')script=script.replace("'/api/gpu/status'","'/api/status'");
  // Static source assembled on the server, not evaluated or fetched as code.
  // Shadow roots isolate old IDs and module state without nested pages/iframes.
  const markup=JSON.stringify('<style>'+f.style+'\n'+skin+'</style>'+f.content).replaceAll('<','\\u003c');
  return `window.toolkitModules.${name}=()=>{const host=window.document.getElementById('${name}-module');const root=host.attachShadow({mode:'open'});root.innerHTML=${markup};const document={getElementById:id=>root.getElementById(id),querySelector:q=>root.querySelector(q),createElement:tag=>window.document.createElement(tag),createTextNode:text=>window.document.createTextNode(text),get visibilityState(){return !host.hidden&&window.document.visibilityState==='visible'?'visible':'hidden';}};${script}\n};`;
 }).join('\n');
 return shell.replace('__TOOLKIT_VERSION__',require('./orca-plugin.json').version).replace('__MODULE_SCRIPTS__',scripts).replace('__INITIAL_VIEW__',initial);
}
module.exports={compose,fragment};
