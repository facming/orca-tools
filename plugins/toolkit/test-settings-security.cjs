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
