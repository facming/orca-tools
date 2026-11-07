'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {fixture,snapshot}=require('./fixtures/accounts.cjs');
const {Accounts,publicState}=require('./modules/accounts.cjs');
const {AccountPreferences}=require('./account-preferences.cjs');
async function started(t){const f=fixture();t.after(()=>f.close());return f.start();}
const select={accountId:'b',expectedActiveAccountId:'a'};

test('macOS initialization, page opens, passive GETs and status never construct a client or call RPC',async t=>{
 const f=await started(t);
 for(let i=0;i<2;i++){
  await fetch(f.server.url+'accounts/');await f.request('accounts/open',{});
  assert.equal((await f.request('accounts')).body.requiresRefresh,true);
  const status=(await f.request('status')).body;
  assert.equal(status.modules.accounts.status,'preview');assert.equal(status.modules.downloads.status,'host-required');assert.equal(status.modules.gpu.status,'offline');
 }
 assert.equal(f.loads,0);assert.equal(f.calls.length,0);
 const result=await f.request('accounts/refresh',{});assert.equal(result.status,200);
 assert.deepEqual(f.constructors,[[f.runtime.userDataPath,30000,null,'Mock remote',f.runtime.cli]]);
 assert.equal((await f.request('status')).body.modules.accounts.status,'ready');
 assert.equal((await f.request('accounts')).body.accounts.length,3);assert.equal(f.calls.length,1);
 for(const route of ['downloads/jobs','gpu/open'])assert.equal((await f.request(route,{})).status,route.startsWith('downloads')?403:400);
});
test('only the configured environment is used, settings changes invalidate readiness without RPC',async t=>{
 const f=await started(t),c=f.server.controller;
 await c.accountState();const next=structuredClone(c.settings);next.accounts.environment='Other mock';await c.update(next);
 assert.equal((await c.status()).modules.accounts.status,'preview');assert.equal((await c.readAccounts()).accounts.length,0);assert.equal(f.calls.length,1);
 await c.accountState();assert.equal(f.constructors.at(-1)[3],'Other mock');
 next.accounts.environment='';await c.update(next);await assert.rejects(c.accountState(),/Set the remote environment/);assert.equal(f.calls.length,2);
});
test('macOS rejects local fallback, RPC ok=false and missing or invalid runtime identity',async t=>{
 const f=fixture();t.after(()=>f.close());const settings={environment:'Mock'};
 f.remote=false;await assert.rejects(new Accounts(f.runtime).list(settings),/not remote/);assert.equal(f.calls.length,0);
 f.remote=true;
 for(const reply of [{ok:false,error:{code:'permission_denied'}},{ok:true,result:snapshot()},{ok:true,_meta:{runtimeId:42},result:snapshot()},{ok:'true',_meta:{runtimeId:'r'},result:snapshot()}]){
  f.reply=reply;await assert.rejects(new Accounts(f.runtime).list(settings));
 }
 const a=new Accounts(f.runtime);f.reply=null;await a.list(settings);a.clients.get('Mock').isRemote=false;
 const n=f.calls.length;await assert.rejects(a.list(settings),/not remote/);assert.equal(f.calls.length,n);
});
test('failed SDK initialization, malformed account results and remote failures never report ready',async t=>{
 const f=await started(t);f.runtime.loadSdk=()=>{throw Error('synthetic private profile');};
 let r=await f.request('accounts/refresh',{});assert.equal(r.status,400);assert.match(r.body.error,/SDK and selected profile/);assert.ok(!JSON.stringify(r).includes('private profile'));
 assert.equal((await f.request('status')).body.modules.accounts.status,'error');
 f.runtime.loadSdk=()=>({RuntimeClient:class{constructor(){throw Object.assign(Error('synthetic private pairing'),{code:'pairing_failed'});}}});
 r=await f.request('accounts/refresh',{});assert.match(r.body.error,/pairing failed/);assert.ok(!JSON.stringify(r).includes('private pairing'));
});
test('structured permission, pairing, incompatible RPC, timeout and disconnect errors are redacted and revoke ready',async t=>{
 const f=await started(t);
 for(const [code,pattern] of [['permission_denied',/permission denied/],['pairing_failed',/pairing failed/],['incompatible_runtime',/incompatible/],['ETIMEDOUT',/timed out/],['ECONNRESET',/disconnected/]]){
  f.failure=null;assert.equal((await f.request('accounts/refresh',{})).status,200);
  f.failure=code;const r=await f.request('accounts/refresh',{});assert.equal(r.status,400);assert.match(r.body.error,pattern);assert.ok(!JSON.stringify(r).includes('private diagnostic'));
  assert.equal((await f.request('status')).body.modules.accounts.status,'error');assert.equal((await f.request('accounts')).body.requiresRefresh,true);
 }
 f.failure=null;f.reply={ok:true,_meta:{runtimeId:'r'},result:{}};
 assert.equal((await f.request('accounts/refresh',{})).status,400);assert.equal((await f.request('status')).body.modules.accounts.status,'error');
});
test('masked email, login, reset times and ready/stale/unknown/error usage retain provenance checks',()=>{
 const raw=snapshot(),state=publicState(raw);
 assert.equal(state.accounts[0].email,'a***@example.invalid');assert.equal(state.accounts[0].current,true);assert.equal(state.accounts[2].needsSignIn,true);
 assert.equal(state.accounts[0].usageStatus.state,'ready');assert.equal(state.accounts[1].usageStatus.state,'stale');assert.equal(state.accounts[2].usageStatus.state,'unknown');
 assert.equal(state.accounts[0].usage.session.resetsAt,raw.rateLimits.claude.session.resetsAt);assert.equal(state.accounts[0].usage.weekly.resetsAt,null);
 raw.rateLimits.claude.usageMetadata.authProvenance='managed:b';assert.equal(publicState(raw).accounts[0].usage,null);
 raw.rateLimits.inactiveClaudeAccounts[0].rateLimits.usageMetadata.authProvenance='managed:a';assert.equal(publicState(raw).accounts[1].usage,null);
 raw.rateLimits.claude.status='error';assert.equal(publicState(raw).accounts[0].usageStatus.state,'error');
});
test('macOS preferences persist per environment at 0600 inside 0700 and contain IDs only',async t=>{
 const f=await started(t);
 assert.equal((await f.request('accounts/disable',{accountId:'b',disabled:true})).status,200);
 const file=path.join(f.runtime.configDir,'account-preferences.json');
 assert.equal(fs.statSync(f.runtime.configDir).mode&0o777,0o700);assert.equal(fs.statSync(file).mode&0o777,0o600);
 assert.deepEqual(JSON.parse(fs.readFileSync(file,'utf8')),{'Mock remote':['b']});
 const p=new AccountPreferences(f.runtime.configDir,{privateFiles:true});assert.equal(p.disabled('Mock remote','b'),true);assert.equal(p.disabled('Other mock','b'),false);
 assert.equal((await f.request('accounts/select',select)).status,400);assert.equal(f.calls.filter(c=>c.method==='accounts.selectClaude').length,0);
 await f.request('accounts/disable',{accountId:'b',disabled:false});assert.equal(new AccountPreferences(f.runtime.configDir,{privateFiles:true}).disabled('Mock remote','b'),false);
});
test('corruption, external edits, symlinks and unsafe permissions fail closed without overwriting preferences',t=>{
 const f=fixture();t.after(()=>f.close());const root=f.runtime.configDir,p=new AccountPreferences(root,{privateFiles:true}),file=p.file;
 p.set('Mock','a',true);fs.writeFileSync(file,'broken');
 assert.throws(()=>new AccountPreferences(root,{privateFiles:true}),/Invalid/);assert.throws(()=>p.set('Mock','b',true),/could not be saved/);assert.equal(fs.readFileSync(file,'utf8'),'broken');
 fs.writeFileSync(file,'{}');fs.chmodSync(file,0o644);assert.throws(()=>new AccountPreferences(root,{privateFiles:true}),/Invalid/);assert.equal(fs.statSync(file).mode&0o777,0o644);
 fs.unlinkSync(file);const target=path.join(f.root,'target');fs.writeFileSync(target,'{}',{mode:0o600});fs.symlinkSync(target,file);assert.throws(()=>new AccountPreferences(root,{privateFiles:true}),/Invalid/);assert.equal(fs.readFileSync(target,'utf8'),'{}');
 fs.unlinkSync(file);fs.chmodSync(root,0o755);assert.throws(()=>new AccountPreferences(root,{privateFiles:true}),/Invalid/);assert.equal(fs.statSync(root).mode&0o777,0o755);
});
test('switch guards IDs, expected selection including current target, sign-in, and confirmed activeAccountId',async t=>{
 const f=await started(t);
 for(const body of [{accountId:'',expectedActiveAccountId:'a'},{accountId:'missing',expectedActiveAccountId:'a'},{accountId:'c',expectedActiveAccountId:'a'},{accountId:'a',expectedActiveAccountId:'old'},{...select,expectedActiveAccountId:'old'}])assert.equal((await f.request('accounts/select',body)).status,400);
 assert.equal(f.calls.filter(c=>c.method==='accounts.selectClaude').length,0);
 f.confirm=false;let r=await f.request('accounts/select',select);assert.match(r.body.error,/not confirmed/);assert.equal((await f.request('status')).body.modules.accounts.status,'error');
 f.confirm=true;r=await f.request('accounts/select',select);assert.equal(r.status,200);assert.equal(r.body.activeAccountId,'b');assert.equal(f.calls.at(-1).method,'accounts.list');
});
test('pending switch prevents duplicate operations and settings drift prevents mutation',async t=>{
 const f=await started(t),c=f.server.controller;let release;
 f.gate=new Promise(resolve=>release=resolve);
 const pending=c.selectAccount(select);
 await assert.rejects(c.selectAccount(select),/in progress/);await assert.rejects(c.setAccountDisabled({accountId:'b',disabled:true}),/in progress/);await assert.rejects(c.accountState(),/in progress/);
 release();await pending;assert.equal(c.accounts.switching,false);assert.equal(f.calls.filter(c=>c.method==='accounts.selectClaude').length,1);
 f.value=snapshot();f.gate=new Promise(resolve=>release=resolve);const drift=c.selectAccount(select);
 const next=structuredClone(c.settings);next.accounts.environment='New mock';await c.update(next);release();await assert.rejects(drift,/connection changed/);
 assert.equal(f.calls.filter(c=>c.method==='accounts.selectClaude').length,1);assert.equal((await c.status()).modules.accounts.status,'preview');
});

test('switch uncertainty is reported and failed verification restores the prior selection',async t=>{
 const f=fixture();t.after(()=>f.close());
 for(const phase of ['mutation','verification']){
  let selected=false,mutations=0;
  const accounts=new Accounts(f.runtime,{createClient:()=>({isRemote:true,async call(method){
   if(method==='accounts.selectClaude'){mutations++;selected=true;if(phase==='mutation')throw Object.assign(Error('synthetic private diagnostic'),{code:'ETIMEDOUT'});}
   else if(selected)throw Object.assign(Error('synthetic private diagnostic'),{code:'ECONNRESET'});
   return {ok:true,_meta:{runtimeId:'mock'},result:snapshot()};
  }})});
  await assert.rejects(accounts.select({environment:'Mock'},select),phase==='mutation'?/could not be confirmed or restored/:/previous account selection was restored/);
  assert.equal(mutations,phase==='mutation'?1:2);
 }
});

require('./fixtures/config-service.cjs').hooks(test);

test('switch confirmation rejects a changed remote runtime',async t=>{
 const f=fixture();t.after(()=>f.close());
 let selected=false;
 const accounts=new Accounts(f.runtime,{createClient:()=>({isRemote:true,async call(method,params){
  if(method==='accounts.selectClaude')selected=true;
  if(method==='accounts.list'&&selected)assert.equal(params.refreshUsage,true);
  return {ok:true,_meta:{runtimeId:selected?'replacement':'original'},result:snapshot()};
 }})});
 await assert.rejects(accounts.select({environment:'Mock'},select),/could not be confirmed or restored/);
});

test('macOS rejects unbound cached usage even when repeated runtime target snapshots agree',async t=>{
 const f=fixture();t.after(()=>f.close());
 const state=snapshot();state.claude.activeAccountIdsByRuntime={host:'a'};state.claude.accounts[0].managedAuthRuntime='host';state.rateLimits.claudeTarget={runtime:'host',wslDistro:null};
 delete state.rateLimits.claude.usageMetadata.authProvenance;delete state.rateLimits.inactiveClaudeAccounts[0].rateLimits.usageMetadata.authProvenance;
 f.value=state;const accounts=new Accounts(f.runtime);
 for(let i=0;i<2;i++){const result=await accounts.list({environment:'fixture'});assert.equal(result.accounts[0].usage,null);assert.equal(result.accounts[1].usage,null);assert.equal(result.accounts[0].usageStatus.code,'unverified-account');}
 state.rateLimits.claude.usageMetadata.authProvenance='managed:a';
 assert.equal((await accounts.list({environment:'fixture'})).accounts[0].usage.session.usedPercent,24);
 state.claude.activeAccountId='b';
 assert.equal((await accounts.list({environment:'fixture'})).accounts[1].usage,null);
});
