#!/usr/bin/env node
'use strict';
const fs=require('node:fs'),client=require('./client.cjs');

function source(file){if(file==='--defaults')return null;const fd=fs.openSync(file,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW);try{const a=fs.fstatSync(fd);if(!a.isFile()||a.nlink!==1||a.size>1024*1024)throw Error('Unsafe source');const b=fs.readFileSync(fd),z=fs.fstatSync(fd);if(a.ino!==z.ino||a.size!==z.size||a.mtimeMs!==z.mtimeMs)throw Error('Source changed');return b.toString('base64');}finally{fs.closeSync(fd);}}
function run(op,args=[]){
if(!Array.isArray(args)||args.some(x=>typeof x!=='string'))throw Error('Configuration command args must be strings.');
if(op==='preview'&&args.length===2)return client.mutate({op,sources:{settings:source(args[0]),'downloads-preferences':source(args[1])}});
else if(op==='confirm'&&args.length===1){const preview=JSON.parse(fs.readFileSync(args[0],'utf8'));return client.mutate({op:'import',preview_id:preview.preview_id,hashes:preview.hashes});}
else if(op==='backup')return client.request({op});
else if(op==='restore'&&args.length===1)return client.mutate({op,backup_id:args[0]});
else if(op==='status')return client.request({op});
else throw Error('Usage: migrate.cjs preview <settings.json|--defaults> <preferences.json|--defaults> | confirm <preview-file> | backup | restore <backup-id> | status');

}
module.exports={run};
if(require.main===module){const [op,...args]=process.argv.slice(2);console.log(JSON.stringify(run(op,args),null,2));}
