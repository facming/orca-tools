'use strict';
// Synthetic Host dispatch only. Uses production main.mjs/commands/HTTP/client,
// then an ad-hoc native caller -> production relay -> dual Security checks -> SQLite.
// NOT evidence of an authorized Orca Host (see test-orca-host-probe.cjs).
const assert=require('node:assert/strict'),cp=require('node:child_process'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const runtimeModule=require('../../platform/index.cjs'),hub=require('../../server.cjs');
const {METHOD}=require('../host-client.cjs');
(async()=>{
 const [caller,bridge,impostor,phase]=process.argv.slice(2),root=fs.mkdtempSync(path.join(os.tmpdir(),'toolkit-host-contract-'));
 const runtime={platform:'darwin',home:root,configDir:root,downloadsDir:path.join(root,'downloads'),openUrl:async()=>{}};
 const commands=new Map();let service,principal=caller,granted=true,calls=0;
 const session=Object.freeze({fixture:true}),{createHostHandler}=require('../host-handler.cjs');
 const handler=createHostHandler(value=>granted&&value===session),originalSpawn=cp.spawn;
 cp.spawn=(file,args,options)=>{
  assert.equal(file,'/Library/PrivilegedHelperTools/OrcaToolkit/config-bridge');assert.deepEqual(args,[]);assert.deepEqual(options.env,{PATH:'/usr/bin:/bin'});
  return originalSpawn(principal,[],options); // fixture-only signed channel creator
 };
 const host={has:method=>method===METHOD,async call(method,envelope){assert.equal(method,METHOD);calls++;return handler(envelope,session);}};
 const originalRuntime=runtimeModule.createRuntime,originalStart=hub.startServer;
 runtimeModule.createRuntime=()=>runtime;
 hub.startServer=async options=>service=await originalStart({...options,port:0,gpu:{status:async()=>({online:false})},accounts:{}});
 const plugin=await import('../../main.mjs');
 try{
  await plugin.default({host,commands:{register:(id,fn)=>commands.set(id,fn)},log(){}});
  assert.equal(commands.size,6);const command=commands.get('toolkit.configuration');
  if(phase==='offline'){
   await assert.rejects(command({operation:'status'}),/unavailable/);assert.equal((await service.controller.status()).configService,'unavailable');
   console.log('PASS: command reports native service offline');return;
  }
  assert.equal((await command()).state,'migration-pending');
  const raw='{"future":9007199254740993123456789,"gpu":{"enabled":true}}';
  const preview=await command({operation:'preview',sources:{settings:Buffer.from(raw).toString('base64'),'downloads-preferences':null}});
  assert.ok(preview.documents.settings.raw_json.includes('9007199254740993123456789'));
  await assert.rejects(command({operation:'confirm',preview_id:preview.preview_id,hashes:{...preview.hashes,settings:'0'.repeat(64)}}),/mismatch/);
  const confirmed=await command({operation:'confirm',preview_id:preview.preview_id,hashes:preview.hashes});assert.equal(confirmed.state,'ready');
  assert.equal((await service.controller.status()).error,'');
  const html=await (await fetch(service.url)).text(),token=html.match(/const token='([a-f0-9]+)'/)[1];
  const headers={'X-Toolkit-Token':token,Origin:new URL(service.url).origin,'Content-Type':'application/json'};
  const post=async(body,overrides={})=>{const r=await fetch(service.url+'api/configuration',{method:'POST',headers:{...headers,...overrides},body:JSON.stringify(body)});return {status:r.status,body:await r.json()};};
  assert.equal((await post({operation:'backup'},{'X-Toolkit-Token':''})).status,403);
  assert.equal((await post({operation:'backup'},{Origin:'https://example.invalid'})).status,403);
  const version=r=>Object.fromEntries(['epoch','store_id','revision'].map(k=>[k,r[k]]));
  const read=()=>command({operation:'read',namespace:'settings'}),a=await read(),b=await read();
  const patch=(base,p,value)=>({operation:'patch',namespace:'settings',...version(base),patch:[{op:'set',path:p,value}]});
  // Separate windows sharing one HTTP server retain their own base versions.
  const [one,two]=await Promise.all([post(patch(a,['gpu','enabled'],false)),post(patch(b,['accounts','enabled'],false))]);
  assert.equal(one.status,200);assert.equal(two.status,200);
  assert.equal((await post(patch(b,['gpu','enabled'],false))).status,400);
  let current=await read();assert.equal(JSON.parse(current.raw_json).accounts.enabled,false);assert.equal(JSON.parse(current.raw_json).gpu.enabled,false);
  assert.ok(current.raw_json.includes('9007199254740993123456789'));
  const backup=await command({operation:'backup'});
  await command(patch(current,['gpu','enabled'],true));
  const restoration={operation:'restore',backup_id:backup.backup_id,request_id:'a'.repeat(32)};
  const restored=await command(restoration);assert.notEqual(restored.epoch,current.epoch);
  assert.deepEqual(await command(restoration),restored); // retry cannot advance epoch again
  await assert.rejects(command(patch(current,['gpu','enabled'],true)),/epoch changed/);
  assert.equal(service.controller.settings.gpu.enabled,false);
  const before=await read();granted=false;await assert.rejects(command({operation:'backup'}),/Unauthorized/);granted=true;
  principal=impostor;await assert.rejects(command(patch(before,['gpu','enabled'],true)),/Unauthorized signed/);principal=caller;
  assert.deepEqual(await read(),before);
  principal=bridge;await assert.rejects(command(),/Unauthorized signed/);principal=caller;
  const direct=cp.spawnSync(bridge,[],{input:'{"op":"status"}\n',encoding:'utf8'});assert.equal(JSON.parse(direct.stdout).ok,false);
  const count=calls;await assert.rejects(command({operation:'status',pid:1}),/fields/);await assert.rejects(command({operation:'exec',script:'no'}),/Unknown/);assert.equal(calls,count);
  host.has=()=>false;await assert.rejects(command(),/sealed Host/);assert.equal(calls,count);
  assert.equal(fs.existsSync(path.join(root,'settings.json')),false);
  console.log('PASS: Toolkit command/HTTP -> synthetic native Host -> Bridge -> signed peers -> SQLite; migration, concurrent windows, conflict, restore/retry, unauthorized, no fallback');
 }finally{await plugin.deactivate();cp.spawn=originalSpawn;runtimeModule.createRuntime=originalRuntime;hub.startServer=originalStart;fs.rmSync(root,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exitCode=1;});
