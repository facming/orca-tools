'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os'),cp=require('node:child_process'),Module=require('node:module');
const settings=require('./settings.cjs'),prefs=require('./download-preferences.cjs');
const mac={skip:process.platform!=='darwin'};
function fixture(t){const root=fs.mkdtempSync(path.join(os.tmpdir(),'orca-settings-security-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));return root;}
for(const [name,write] of [['settings.json',root=>settings.save(root,settings.defaults())],['downloads-preferences.json',root=>prefs.rememberFolder(root,'/synthetic/Downloads')]]){
 test(name+': baseline fixed tmp symlink overwrites victim',mac,t=>{
  const root=fixture(t),victim=path.join(root,'victim');fs.writeFileSync(victim,'KEEP');fs.symlinkSync(victim,path.join(root,name+'.tmp'));
  const source=cp.execFileSync('git',['show','fe0a0157c1e0d686fe06fe9eed6846abc715cd06:plugins/toolkit/'+(name==='settings.json'?'settings.cjs':'download-preferences.cjs')],{encoding:'utf8'});
  const legacy=new Module(path.join(__dirname,'legacy-fixture.cjs'));legacy.filename=path.join(__dirname,'legacy-fixture.cjs');legacy.paths=module.paths;legacy._compile(source,legacy.filename);
  if(name==='settings.json')legacy.exports.save(root,legacy.exports.defaults());else legacy.exports.rememberFolder(root,'/synthetic/Downloads');
  assert.notEqual(fs.readFileSync(victim,'utf8'),'KEEP');assert.ok(fs.lstatSync(path.join(root,name)).isSymbolicLink());
 });
 test(name+': old tmp symlink is untouched; normal write and recovery',mac,t=>{
  const root=fixture(t),victim=path.join(root,'victim');fs.writeFileSync(victim,'KEEP');fs.symlinkSync(victim,path.join(root,name+'.tmp'));write(root);write(root);
  assert.equal(fs.readFileSync(victim,'utf8'),'KEEP');assert.equal(fs.statSync(path.join(root,name)).mode&0o777,0o600);assert.ok(!fs.lstatSync(path.join(root,name)).isSymbolicLink());
 });
 for(const attack of ['target-symlink','target-hardlink','parent-symlink','corrupt'])test(name+': rejects '+attack,mac,t=>{
  const root=fixture(t),target=path.join(root,name),victim=path.join(root,'victim');fs.writeFileSync(victim,'{"preserve":true}',{mode:0o600});let selected=root;
  if(attack==='target-symlink')fs.symlinkSync(victim,target);
  if(attack==='target-hardlink')fs.linkSync(victim,target);
  if(attack==='parent-symlink'){selected=path.join(root,'alias');fs.symlinkSync(root,selected);}
  if(attack==='corrupt')fs.writeFileSync(target,'{broken',{mode:0o600});
  assert.throws(()=>write(selected),/settings|configuration/i);assert.equal(fs.readFileSync(victim,'utf8'),'{"preserve":true}');
  if(attack==='corrupt')assert.equal(fs.readFileSync(target,'utf8'),'{broken');
 });
}
test('settings rejects external modification and stale independent consumers; reload recovers',mac,t=>{
 const root=fixture(t);settings.save(root,settings.defaults());const first=settings.load(root),second=settings.load(root);first.gpu.enabled=false;settings.save(root,first,first);
 assert.throws(()=>settings.save(root,second,second),/changed/);
 const current=settings.load(root);fs.writeFileSync(path.join(root,'settings.json'),JSON.stringify({...current,gpu:{enabled:true}}));assert.throws(()=>settings.save(root,current,current),/changed/);
 const reloaded=settings.load(root);settings.save(root,reloaded,reloaded);
});
test('preferences reject external change between read and write, preserving both fields',mac,t=>{
 const root=fixture(t);prefs.rememberFolder(root,'/first');const file=path.join(root,'downloads-preferences.json'),spawn=cp.spawnSync;
 t.mock.method(cp,'spawnSync',(exe,args,options)=>{const input=JSON.parse(options.input);if(input.operation==='write')fs.writeFileSync(file,JSON.stringify({folder:'/external',remoteLocations:{manual:{source:'/keep'}}}));return spawn(exe,args,options);});
 assert.throws(()=>prefs.rememberRemote(root,'remote',{source:'/new'}),/changed/);
 assert.deepEqual(JSON.parse(fs.readFileSync(file)),{folder:'/external',remoteLocations:{manual:{source:'/keep'}}});
});
test('schema-corrupt settings are not repaired by save',mac,t=>{
 const root=fixture(t),file=path.join(root,'settings.json');fs.writeFileSync(file,'{"unknown":"KEEP"}',{mode:0o600});assert.throws(()=>settings.load(root));assert.throws(()=>settings.save(root,settings.defaults()));assert.equal(fs.readFileSync(file,'utf8'),'{"unknown":"KEEP"}');
});
test('Windows settings and preferences preserve legacy behavior without Python',t=>{
 const root=fixture(t);t.mock.method(cp,'spawnSync',()=>{throw Error('Windows must not use POSIX helper');});
 settings.save(root,settings.defaults(),undefined,'win32');assert.deepEqual(settings.load(root,undefined,'win32'),settings.defaults());
 const windows=prefs.forPlatform('win32');assert.equal(windows.rememberFolder(root,'/synthetic'),true);assert.equal(windows.defaultFolder(root,root),'/synthetic');
});
