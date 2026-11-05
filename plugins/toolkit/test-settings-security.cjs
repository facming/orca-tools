'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os'),cp=require('node:child_process');
const settings=require('./settings.cjs'),prefs=require('./download-preferences.cjs').forPlatform('darwin'),client=require('./config-service/client.cjs');
const fixture=require('./fixtures/config-service.cjs');
function temp(t){const root=fs.mkdtempSync(path.join(os.tmpdir(),'orca-retired-json-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));return root;}
test('macOS missing service fails closed for reads/writes without touching retired JSON',t=>{
 const root=temp(t);fs.writeFileSync(path.join(root,'settings.json'),'{broken');t.mock.method(cp,'spawnSync',()=>({status:70}));
 assert.throws(()=>settings.load(root,null,'darwin'),/not installed/);assert.throws(()=>settings.save(root,settings.defaults(),undefined,'darwin'),/Reload/);
 assert.throws(()=>prefs.rememberFolder(root,'/synthetic'),/not installed/);assert.equal(fs.readFileSync(path.join(root,'settings.json'),'utf8'),'{broken');assert.equal(fs.existsSync(path.join(root,'downloads-preferences.json')),false);
});
test('macOS unrelated fields merge, same field conflicts and JSON edits have no authority',t=>{
 fixture.install(t);const root=temp(t),a=settings.load(root,null,'darwin'),b=settings.load(root,null,'darwin'),c=settings.load(root,null,'darwin');
 a.gpu.enabled=false;settings.save(root,a,a,'darwin');b.accounts.environment='Other';settings.save(root,b,b,'darwin');
 c.gpu.enabled=false;assert.throws(()=>settings.save(root,c,c,'darwin'),/conflict/);
 fs.writeFileSync(path.join(root,'settings.json'),'{"gpu":{"enabled":true}}');const current=settings.load(root,null,'darwin');assert.equal(current.gpu.enabled,false);assert.equal(current.accounts.environment,'Other');
});
test('macOS service ignores retired JSON links and preserves remote unknown fields',t=>{
 fixture.install(t);const root=temp(t),victim=path.join(root,'victim');fs.writeFileSync(victim,'KEEP');
 for(const name of ['settings.json','downloads-preferences.json','settings.json.tmp','downloads-preferences.json.tmp'])fs.symlinkSync(victim,path.join(root,name));
 const base=client.read('downloads-preferences');client.save('downloads-preferences',{remoteLocations:{one:{source:'/old',future:{keep:true}}}},base);
 prefs.rememberRemote(root,'one',{source:'/new'});prefs.rememberFolder(root,'/local');
 assert.deepEqual(client.read('downloads-preferences').remoteLocations.one.future,{keep:true});assert.equal(fs.readFileSync(victim,'utf8'),'KEEP');
});
test('Windows retains JSON without starting config service or Python',t=>{
 const root=temp(t);t.mock.method(cp,'spawnSync',()=>{throw Error('No helper on Windows');});settings.save(root,settings.defaults(),undefined,'win32');assert.deepEqual(settings.load(root,null,'win32'),settings.defaults());
 const p=require('./download-preferences.cjs').forPlatform('win32');assert.equal(p.rememberFolder(root,'/synthetic'),true);assert.equal(p.defaultFolder(root,root),'/synthetic');
});
test('two macOS Controllers merge independent changes and reject stale overlapping saves',async t=>{
 fixture.install(t);const {Controller}=require('./controller.cjs'),root=temp(t);
 const runtime={platform:'darwin',configDir:root,downloadsDir:path.join(root,'downloads'),home:root};
 const options={runtime,gpu:{async status(){return {online:false};}},accounts:{}};
 const a=new Controller(options),b=new Controller(options),stale=new Controller(options);
 t.after(async()=>{await a.close();await b.close();await stale.close();});
 const one=structuredClone(a.settings);one.gpu.enabled=false;await a.update(one);
 const two=structuredClone(b.settings);two.accounts.environment='Other';await b.update(two);
 assert.equal(b.settings.gpu.enabled,false);assert.equal(b.settings.accounts.environment,'Other');
 const conflict=structuredClone(stale.settings);conflict.gpu.enabled=false;await assert.rejects(stale.update(conflict),/conflict/);
});
test('sparse migration supplies UI defaults while preserving exact unknown JSON numbers',t=>{
 const raw='{"future":{"big":9007199254740993123456789,"fraction":0.123456789012345678901},"gpu":{"enabled":true}}';
 fixture.install(t,{settings:Buffer.from(raw).toString('base64'),'downloads-preferences':null});const root=temp(t);
 const value=settings.load(root,null,'darwin');assert.equal(value.downloads.sshPort,22);assert.equal(value.accounts.enabled,true);
 const next=settings.defaults();next.gpu.enabled=false;settings.save(root,next,value,'darwin');
 const saved=client.request({op:'read',namespace:'settings'}).raw_json;
 assert.ok(saved.includes('9007199254740993123456789'));assert.ok(saved.includes('0.123456789012345678901'));assert.equal(JSON.parse(saved).gpu.enabled,false);
});
