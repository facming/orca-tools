'use strict';
const http=require('node:http');
const {MacGpu}=require('./modules/gpu-macos.cjs');
module.exports={create(runtime){
 const gpu=new MacGpu(runtime),active=new Set();let closed=false;
 return {
  close(){closed=true;for(const req of active)req.destroy();active.clear();},
  async call(command){
   if(closed)throw Error('Workbench session closed');
   let token;try{token=gpu.credentials();}catch{throw Error('Local Bridge setup is missing or its private permissions are invalid. Complete local Bridge setup before using project tools.');}
   const body=Buffer.from(JSON.stringify(command));
   if(body.length>1024*1024)throw Error('Workbench request exceeds 1 MB.');
   return new Promise((resolve,reject)=>{
    const req=http.request({hostname:'127.0.0.1',port:18773,path:'/api/workbench',method:'POST',timeout:65000,headers:{Authorization:'Bearer '+token,Origin:'http://127.0.0.1:18773','Content-Type':'application/json','Content-Length':body.length}},res=>{
     let text='';res.setEncoding('utf8');res.on('data',chunk=>{text+=chunk;if(text.length>4*1024*1024)res.destroy();});res.on('error',()=>reject(Error('Bridge response interrupted. Refresh tasks before retrying.')));
     res.on('end',()=>{try{const value=JSON.parse(text);if(res.statusCode!==200)throw Error(res.statusCode===400&&typeof value.error==='string'?value.error:'Bridge access denied or unavailable.');resolve(value);}catch(e){reject(e instanceof SyntaxError?Error('Bridge returned an invalid response. Refresh tasks before retrying.'):e);}});
    });active.add(req);req.on('close',()=>active.delete(req));req.on('error',()=>reject(Error('Local Bridge unavailable. Check its LaunchAgent, then refresh tasks before retrying.')));req.on('timeout',()=>req.destroy());req.end(body);
   });
  }
 };
}};
