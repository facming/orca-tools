'use strict';
// Storage/API fixture only. Permission isolation is tested separately with real UIDs.
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),cp=require('node:child_process');
const client=require('../config-service/client.cjs'),{ToolkitError}=require('../platform/errors.cjs');
function install(t){
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'orca-config-api-')),original=client.request;
 const script=path.join(__dirname,'../config-service/fixture.py');
 cp.execFileSync('python3',[script,root,'init']);
 client.request=body=>{const reply=JSON.parse(cp.execFileSync('python3',[script,root],{input:JSON.stringify(body),encoding:'utf8'}));if(!reply.ok)throw new ToolkitError(reply.error);return reply.result;};
 t.after(()=>{client.request=original;fs.rmSync(root,{force:true,recursive:true});});
 return client;
}
function hooks(test){test.beforeEach(t=>install(t));}
module.exports={install,hooks};
