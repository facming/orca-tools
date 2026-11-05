'use strict';
const fs=require('node:fs'),path=require('node:path');
const {validRemote}=require('./remote-browser.cjs');
const configService=require('./config-service/client.cjs');
function valid(folder){return typeof folder==='string'&&path.isAbsolute(folder)&&folder.length<=4096&&!/[\x00-\x1f]/.test(folder);}
function forPlatform(platform=process.platform){
 function read(root){
  if(platform==='darwin'){const value=configService.read('downloads-preferences');return {value,version:value};}
  try{const value=JSON.parse(fs.readFileSync(path.join(root,'downloads-preferences.json'),'utf8'));return {value:value&&typeof value==='object'&&!Array.isArray(value)?value:{}};}catch{return {value:{}};}
 }
 function write(root,value,version){
  if(platform==='darwin'){configService.save('downloads-preferences',value,version);return true;}
  try{fs.mkdirSync(root,{recursive:true});const file=path.join(root,'downloads-preferences.json');fs.writeFileSync(file+'.tmp',JSON.stringify(value,null,2),{mode:0o600});fs.renameSync(file+'.tmp',file);return true;}catch{return false;}
 }
 function location(value,environment){const locations=value.remoteLocations;const item=locations&&Object.hasOwn(locations,environment)?locations[environment]:{};return {directory:validRemote(item?.directory)?item.directory:'',source:validRemote(item?.source)?item.source:''};}
 function defaultFolder(root,home){const {value}=read(root);return valid(value.folder)?value.folder:path.join(home,'Downloads','Orca');}
 function rememberFolder(root,folder){if(!valid(folder))return false;const {value,version}=read(root);return write(root,{...value,folder},version);}
 function remoteLocation(root,environment){return location(read(root).value,environment);}
 function rememberRemote(root,environment,changes){
  if(typeof environment!=='string'||!environment||!changes||Object.keys(changes).some(k=>!['source','directory'].includes(k)))return false;
  if(Object.values(changes).some(v=>v!==''&&!validRemote(v)))return false;
  const {value,version}=read(root),previous=location(value,environment);
  const locations=value.remoteLocations&&typeof value.remoteLocations==='object'&&!Array.isArray(value.remoteLocations)?value.remoteLocations:{};
  return write(root,{...value,remoteLocations:{...locations,[environment]:{...(locations[environment]||{}),...previous,...changes}}},version);
 }
 return {defaultFolder,rememberFolder,remoteLocation,rememberRemote};
}
module.exports={...forPlatform(),forPlatform};
