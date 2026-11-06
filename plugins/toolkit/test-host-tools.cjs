'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {create}=require('./host-tools.cjs');
function fixture(t){
 const dataDir=fs.mkdtempSync(path.join(os.tmpdir(),'host-tools-'));t.after(()=>fs.rmSync(dataDir,{recursive:true,force:true}));
 let active='a',fail=false,onList=()=>{},settings={accounts:{enabled:true,environment:'fixture'},gpu:{enabled:true}};const calls=[];
 const runtime={loadSdk:()=>({RuntimeClient:class{constructor(){this.isRemote=true;}async call(method,params){calls.push(method);if(fail)throw {code:'disconnected'};if(method==='accounts.selectClaude')active=params.accountId;else onList();return {ok:true,_meta:{runtimeId:'fixture'},result:{claude:{activeAccountId:active,accounts:[{id:'a',email:'a@example.test'},{id:'b',email:'b@example.test'}]}}};}}})};
 const service=create({runtime,dataDir,readSettings:async()=>structuredClone(settings)});t.after(()=>service.close());
 return {service,calls,settings,set fail(v){fail=v;},set onList(v){onList=v;}};
}
test('Host accounts restore selection, confirm switches, persist disable preferences and recover failed snapshots',async t=>{
 const f=fixture(t),call=x=>f.service.call(x);
 assert.equal((await call({operation:'accounts'})).activeAccountId,'a');
 const state=await call({operation:'accounts-refresh'});assert.equal(state.accounts[0].email,'a***@example.test');
 await call({operation:'accounts-disable',accountId:'b',disabled:true});
 await assert.rejects(call({operation:'accounts-select',accountId:'b',expectedActiveAccountId:'a'}),/disabled/);
 await call({operation:'accounts-disable',accountId:'b',disabled:false});
 assert.equal((await call({operation:'accounts-select',accountId:'b',expectedActiveAccountId:'a'})).activeAccountId,'b');
 f.fail=true;await assert.rejects(call({operation:'accounts-refresh'}),/disconnected/);
 await assert.rejects(call({operation:'accounts'}),/disconnected/);
 f.fail=false;assert.equal((await call({operation:'accounts-refresh'})).activeAccountId,'b');
});
test('changed settings during remote preflight deny switching and revoked services reject work',async t=>{
 const f=fixture(t);f.onList=()=>{f.settings.accounts.environment='changed';};
 await assert.rejects(f.service.call({operation:'accounts-select',accountId:'b',expectedActiveAccountId:'a'}),/connection changed/);
 assert.equal(f.calls.includes('accounts.selectClaude'),false);
 f.service.close();await assert.rejects(f.service.call({operation:'accounts-refresh'}),/closed/);
});
