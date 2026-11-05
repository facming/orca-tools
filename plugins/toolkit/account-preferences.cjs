'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {ToolkitError}=require('./platform/errors.cjs');
class AccountPreferences {
 constructor(root,{privateFiles=false}={}){
  this.file=path.join(root,'account-preferences.json');this.privateFiles=privateFiles;
  this.value={};this.original=null;
  try{
   if(privateFiles)this.checkDirectory();
   this.original=this.read();
   if(this.original===null)return;
   const value=JSON.parse(this.original);
   if(!value||typeof value!=='object'||Array.isArray(value)||Object.values(value).some(ids=>!Array.isArray(ids)||ids.some(id=>typeof id!=='string')))throw Error();
   this.value=value;
  }catch{throw new ToolkitError('Invalid or inaccessible account preferences. Repair the local file before managing accounts.');}
 }
 checkDirectory(){
  const dir=path.dirname(this.file);fs.mkdirSync(dir,{recursive:true,mode:0o700});
  const stat=fs.lstatSync(dir);
  if(!stat.isDirectory()||stat.uid!==process.getuid()||(stat.mode&0o777)!==0o700)throw Error();
 }
 read(){
  let stat;try{stat=fs.lstatSync(this.file);}catch(e){if(e.code==='ENOENT')return null;throw e;}
  if(this.privateFiles&&(!stat.isFile()||stat.nlink!==1||stat.uid!==process.getuid()||(stat.mode&0o777)!==0o600))throw Error();
  return fs.readFileSync(this.file,'utf8');
 }
 disabled(environment,id){return Object.hasOwn(this.value,environment)&&this.value[environment].includes(id);}
 set(environment,id,disabled){
  const ids=new Set(Object.hasOwn(this.value,environment)?this.value[environment]:[]);if(disabled)ids.add(id);else ids.delete(id);
  const next={...this.value,[environment]:[...ids]},text=JSON.stringify(next,null,2);
  if(!this.privateFiles){fs.mkdirSync(path.dirname(this.file),{recursive:true});fs.writeFileSync(this.file+'.tmp',text);fs.renameSync(this.file+'.tmp',this.file);this.value=next;return;}
  let temp,created=false;
  try{
   this.checkDirectory();
   // Refuse external edits, including corruption after startup. Do not repair or
   // overwrite them silently, or follow a replaced preference symlink.
   if(this.read()!==this.original)throw Error();
   temp=this.file+'.'+crypto.randomBytes(12).toString('hex')+'.tmp';
   fs.writeFileSync(temp,text,{flag:'wx',mode:0o600});created=true;
   if(this.read()!==this.original)throw Error();
   fs.renameSync(temp,this.file);this.original=text;this.value=next;
  }catch{throw new ToolkitError('Account preferences could not be saved. Check local permissions or changes to the file.');}
  finally{if(created&&fs.existsSync(temp))fs.unlinkSync(temp);}
 }
 apply(environment,state){return {...state,accounts:state.accounts.map(a=>({...a,disabled:this.disabled(environment,a.id)}))};}
}
module.exports={AccountPreferences};
