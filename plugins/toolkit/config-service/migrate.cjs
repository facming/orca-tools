#!/usr/bin/env node
'use strict';
const fs=require('node:fs'),crypto=require('node:crypto'),client=require('./client.cjs');
const [op,...args]=process.argv.slice(2);
function source(file){if(file==='--defaults')return null;const fd=fs.openSync(file,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW);try{const a=fs.fstatSync(fd);if(!a.isFile()||a.nlink!==1||a.size>1024*1024)throw Error('Unsafe source');const b=fs.readFileSync(fd),z=fs.fstatSync(fd);if(a.ino!==z.ino||a.size!==z.size||a.mtimeMs!==z.mtimeMs)throw Error('Source changed');return b.toString('base64');}finally{fs.closeSync(fd);}}
if(op==='preview'&&args.length===2)console.log(JSON.stringify(client.mutate({op,sources:{settings:source(args[0]),'downloads-preferences':source(args[1])}}),null,2));
else if(op==='confirm'&&args.length===1){const preview=JSON.parse(fs.readFileSync(args[0],'utf8'));console.log(JSON.stringify(client.mutate({op:'import',preview_id:preview.preview_id,hashes:preview.hashes})));}
else if(op==='backup')console.log(JSON.stringify(client.request({op})));
else if(op==='restore'&&args.length===1)console.log(JSON.stringify(client.mutate({op,backup_id:args[0]})));
else if(op==='status')console.log(JSON.stringify(client.request({op})));
else throw Error('Usage: migrate.cjs preview <settings.json|--defaults> <preferences.json|--defaults> | confirm <preview-file> | backup | restore <backup-id> | status');
