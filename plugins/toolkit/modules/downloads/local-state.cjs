'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {ToolkitError}=require('../../platform/errors.cjs');
function privatePath(file,directory=false){
 const s=fs.lstatSync(file);
 if(!(directory?s.isDirectory():s.isFile())||s.uid!==process.getuid()||(s.mode&0o777)!==(directory?0o700:0o600)||(!directory&&s.nlink!==1))
  throw new ToolkitError('Downloads state has unsafe permissions or links.');
}
function privateDirectory(dir){fs.mkdirSync(dir,{recursive:true,mode:0o700});privatePath(dir,true);}
function writeJson(file,value){
 const temp=file+'.'+crypto.randomBytes(8).toString('hex')+'.tmp';let fd;
 try{fd=fs.openSync(temp,'wx',0o600);fs.writeFileSync(fd,JSON.stringify(value,null,2));fs.fsyncSync(fd);fs.closeSync(fd);fd=undefined;fs.renameSync(temp,file);if(process.platform!=='win32'){const parent=fs.openSync(path.dirname(file),'r');try{fs.fsyncSync(parent);}finally{fs.closeSync(parent);}}}
 finally{if(fd!==undefined)fs.closeSync(fd);fs.rmSync(temp,{force:true});}
}
function acquireLock(root){
 const file=path.join(root,'manager.lock'),reclaim=file+'.reclaim';
 const take=()=>{const fd=fs.openSync(file,'wx',0o600);try{fs.writeFileSync(fd,String(process.pid));fs.fsyncSync(fd);}finally{fs.closeSync(fd);}};
 try{take();}catch(e){
  if(e.code!=='EEXIST')throw e;
  // Serialize stale-owner recovery; an incomplete/ambiguous lock fails closed.
  try{fs.mkdirSync(reclaim,{mode:0o700});}catch{throw new ToolkitError('Downloads state is locked by another manager.');}
  try{
   privatePath(file);const pid=Number(fs.readFileSync(file,'utf8'));
   if(!Number.isSafeInteger(pid)||pid<1)throw new ToolkitError('Downloads lock needs manual inspection.');
   try{process.kill(pid,0);throw new ToolkitError('Downloads state is locked by another manager.');}catch(error){if(error.code!=='ESRCH')throw error;}
   fs.unlinkSync(file);take();
  }finally{fs.rmdirSync(reclaim);}
 }
 return ()=>fs.unlinkSync(file);
}
function localError(error){
 if(['EACCES','EPERM'].includes(error?.code))return new ToolkitError('Download folder access denied. On macOS, check Files and Folders permission in System Settings or choose another folder.');
 if(error?.code==='ENOSPC')return new ToolkitError('Not enough space in the download folder. Free space and resume.');
 return error;
}
module.exports={privatePath,privateDirectory,writeJson,acquireLock,localError};
