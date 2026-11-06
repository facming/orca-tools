'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const platform = require('./platform/index.cjs');
const {createRemoteClient} = require('./remote-runtime.cjs');
const {Gpu} = require('./modules/gpu.cjs');
const hub = require('./server.cjs');

function temporary(t) {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'orca-runtime-')));
  t.after(() => fs.rmSync(root, {recursive: true, force: true}));
  return root;
}
function file(name, contents = '', mode = 0o600) {
  fs.mkdirSync(path.dirname(name), {recursive: true});
  fs.writeFileSync(name, contents, {mode});
  return name;
}
const sdk = `exports.RuntimeClient = class {
  constructor(...args) { this.args = args; }
  call() { throw Error('fixture does not perform RPC'); }
};
exports.getDefaultUserDataPath = (platform, home) => {
  if (platform !== 'darwin') throw Error();
  return require('node:path').join(home, 'Library', 'Application Support', 'orca');
};`;
function mac(t, source = sdk) {
  const home = temporary(t);
  const applicationsDir = path.join(home, 'Apps'), bundle = path.join(applicationsDir, 'Orca.app');
  const execPath = file(path.join(bundle, 'Contents/MacOS/Orca'), '', 0o700);
  const cli = file(path.join(bundle, 'Contents/Resources/bin/orca'), '', 0o700);
  const sdkFile = file(path.join(bundle, 'Contents/Resources/app.asar.unpacked/out/cli/runtime-client.js'), source);
  const profile = path.join(home, 'Library/Application Support/orca');
  const pluginRoot = path.join(profile, 'plugins/local-tools.toolkit', 'a'.repeat(64));
  fs.mkdirSync(pluginRoot, {recursive: true});
  const options = {platform: 'darwin', home, execPath, applicationsDir, pluginRoot, env: {},
    run(exe, args) {
      if (exe === '/usr/bin/plutil') {
        assert.equal(args.at(-1), path.join(bundle, 'Contents/Info.plist'));
        return 'com.stablyai.orca\n';
      }
      assert.equal(exe, '/usr/bin/mdfind');
      return bundle + '\0';
    }};
  return {home, bundle, profile, cli, sdkFile, options, build: overrides => platform.createRuntime({...options, ...overrides})};
}
const macOnly = {skip: process.platform !== 'darwin' && 'requires macOS system tools and POSIX modes'};

test('Windows adapter preserves both installation layouts, tool paths, legacy directories and launch arguments', async t => {
  const home = temporary(t), execPath = path.join(home, 'host', 'Orca.exe');
  const installed = file(path.join(home, 'AppData/Local/Programs/orca/resources/bin/orca.exe'), '', 0o700);
  const env = {SystemRoot: path.join(home, 'Windows'), NODE_CHANNEL_FD: '3', ELECTRON_RUN_AS_NODE: '1'};
  const calls = [], options = {platform: 'win32', home, execPath, env, launch: (...args) => calls.push(args)};
  const r = platform.createRuntime(options);
  assert.equal(r.platform, 'win32');
  assert.equal(r.cli, installed);
  assert.equal(r.dataDir, path.join(r.local, 'OrcaTools'));
  assert.equal(r.downloadsDir, path.join(r.local, 'OrcaParallelDownloads'));
  assert.equal(r.userDataPath, path.join(home, 'AppData/Roaming/orca'));
  assert.equal(r.ssh, path.join(env.SystemRoot, 'System32/OpenSSH/ssh.exe'));
  assert.equal(r.keygen, path.join(path.dirname(r.ssh), 'ssh-keygen.exe'));
  assert.equal(r.childEnv.NODE_CHANNEL_FD, undefined);
  assert.equal(r.childEnv.ELECTRON_RUN_AS_NODE, undefined);
  assert.equal(env.NODE_CHANNEL_FD, '3');
  const url = 'https://example.invalid/';
  await r.openUrl(url);
  await r.openPath(home);
  assert.deepEqual(calls, [[r.browser, ['url.dll,FileProtocolHandler', url], r.childEnv], [r.explorer, [home], r.childEnv]]);
  assert.throws(() => r.loadSdk(), /SDK is missing or has incompatible exports/);
  file(path.join(path.dirname(installed), '../app.asar.unpacked/out/cli/runtime-client.js'), sdk);
  assert.deepEqual(createRemoteClient(r, 'fixture').args, [r.userDataPath, 30000, null, 'fixture', installed]);
  const hosted = file(path.join(path.dirname(execPath), 'resources/bin/orca.exe'), '', 0o700);
  assert.equal(platform.createRuntime(options).cli, hosted);
  fs.unlinkSync(hosted);
  fs.unlinkSync(installed);
  assert.throws(() => platform.createRuntime(options), /Cannot locate the Orca CLI/);
  assert.throws(() => platform.createRuntime({platform: 'linux'}), /requires Windows or macOS/);
});

test('running macOS bundle resolves the SDK, private directories, OpenSSH and a clean child environment', macOnly, t => {
  const f = mac(t);
  const env = {HOME: f.home, ELECTRON_RUN_AS_NODE: '1', NODE_CHANNEL_FD: '3',
    NODE_UNIQUE_ID: 'worker', NODE_CHANNEL_SERIALIZATION_MODE: 'advanced', APPDATA: 'unused', OTHER_PRIVATE_VAR: 'unused'};
  const r = f.build({env});
  assert.equal(r.platform, 'darwin');
  assert.equal(r.cli, f.cli);
  assert.equal(r.local, path.join(f.home, 'Library/Application Support'));
  assert.equal(r.configDir, path.join(r.local, 'OrcaTools'));
  assert.equal(r.dataDir, r.configDir);
  assert.equal(r.cacheDir, path.join(f.home, 'Library/Caches/OrcaTools'));
  assert.equal(r.logDir, path.join(f.home, 'Library/Logs/OrcaTools'));
  assert.deepEqual(r.childEnv, {HOME: f.home, ORCA_USER_DATA_PATH: f.profile});
  assert.equal(env.NODE_CHANNEL_FD, '3');
  assert.deepEqual(createRemoteClient(r, 'fixture').args, [f.profile, 30000, null, 'fixture', f.cli]);
  assert.deepEqual([r.ssh, r.keygen], ['/usr/bin/ssh', '/usr/bin/ssh-keygen']);
  for (const dir of [r.dataDir, r.cacheDir, r.logDir]) {
    r.ensurePrivateDirectory(dir);
    assert.equal(fs.statSync(dir).mode & 0o777, 0o700);
  }
  fs.chmodSync(r.configDir, 0o755);
  assert.throws(() => r.ensurePrivateDirectory(r.configDir), /private, writable/);
  const link = path.join(f.home, 'link');
  fs.symlinkSync(r.cacheDir, link);
  assert.throws(() => r.ensurePrivateDirectory(link), /private, writable/);
});

test('missing, non-executable, ambiguous or incompatible installs fail before client construction', macOnly, t => {
  const f = mac(t);
  const other = path.join(f.home, 'Applications/Orca.app');
  fs.mkdirSync(other, {recursive: true});
  assert.throws(() => f.build(), /Multiple Orca installations/);
  fs.rmdirSync(other);
  assert.throws(() => f.build({execPath: process.execPath, run: () => ''}), /identify the running/);
  assert.throws(() => f.build({run: () => 'unexpected.bundle'}), /identify the running/);
  fs.chmodSync(f.cli, 0o600);
  assert.throws(() => f.build(), /CLI is missing or not executable/);
  fs.unlinkSync(f.cli);
  assert.throws(() => f.build(), /CLI is missing or not executable/);
  file(f.cli, '', 0o700);
  fs.unlinkSync(f.sdkFile);
  assert.throws(() => f.build(), /SDK is unavailable/);
  fs.symlinkSync(file(path.join(f.home, 'outside.js')), f.sdkFile);
  assert.throws(() => f.build(), /SDK is unavailable/);
  assert.throws(() => mac(t, 'exports.RuntimeClient = class {};').build(), /incompatible exports/);
});

test('profile override is explicit and accessible, with fail-closed default profile checks', macOnly, t => {
  const f = mac(t, sdk.replace('exports.getDefaultUserDataPath =', 'exports.unused ='));
  assert.equal(f.build().userDataPath, f.profile);
  const alternate = path.join(f.home, '配置 空间');
  fs.mkdirSync(alternate);
  assert.throws(() => f.build({pluginRoot: alternate}), /Cannot verify the Orca profile/);
  const r = f.build({env: {ORCA_USER_DATA_PATH: alternate}});
  assert.equal(r.userDataPath, alternate);
  assert.equal(r.childEnv.ORCA_USER_DATA_PATH, alternate);
  for (const value of ['', 'relative', path.join(f.home, 'missing'), f.cli]) {
    assert.throws(() => f.build({env: {ORCA_USER_DATA_PATH: value}}), {message: 'Cannot verify the Orca profile. Specify an accessible ORCA_USER_DATA_PATH.'});
  }
});

test('open and folder selection use argument arrays, preserve Unicode, redact errors and handle cancellation', macOnly, async t => {
  const f = mac(t), selected = path.join(f.home, "中文 'folder' $x");
  fs.mkdirSync(selected);
  const calls = [], scripts = [];
  let reply = 'selected:' + selected + '\n';
  const r = f.build({launch: (...args) => calls.push(args), execute: async (exe, args, options) => {
    assert.equal(exe, '/usr/bin/osascript');
    assert.equal(options.env.ORCA_USER_DATA_PATH, undefined);
    scripts.push(args);
    if (reply instanceof Error) throw reply;
    return {stdout: reply};
  }});
  const url = 'https://example.invalid/?a=b&c=d';
  await r.openUrl(url);
  await r.openPath(selected);
  assert.deepEqual(calls, [['/usr/bin/open', [url], r.childEnv], ['/usr/bin/open', [selected], r.childEnv]]);
  assert.throws(() => r.openUrl('file:///'), /Invalid browser URL/);
  assert.throws(() => r.openPath(f.sdkFile), /folder is unavailable/);
  assert.deepEqual(await r.chooseDirectory(selected), {cancelled: false, path: selected});
  assert.deepEqual(scripts[0], [path.join(__dirname, 'choose-directory.applescript'), selected]);
  reply = 'cancelled\n';
  assert.deepEqual(await r.chooseDirectory(path.join(f.home, 'not created')), {cancelled: true});
  assert.deepEqual(scripts[1], scripts[0].slice(0, 1));
  reply = new Error(selected);
  await assert.rejects(r.chooseDirectory(), e => !e.message.includes(selected) && /could not finish/.test(e.message));
  reply = 'selected:' + path.join(f.home, 'missing');
  await assert.rejects(r.chooseDirectory(), /selected folder is unavailable/);
  if (process.getuid() !== 0) {
    reply = 'selected:' + selected;
    fs.chmodSync(selected, 0o500);
    try { await assert.rejects(r.chooseDirectory(), /selected folder is unavailable/); }
    finally { fs.chmodSync(selected, 0o700); }
  }
});

test('macOS activation shares one runtime, serves local previews and cannot start GPU or remote work', macOnly, async t => {
  require('./fixtures/config-service.cjs').install(t);
  const f = mac(t), r = f.build();
  const launched = [];
  r.openUrl = async url => launched.push(url);
  const commands = new Map();
  const plugin = await import('./main.mjs');
  t.mock.method(platform, 'createRuntime', () => r);
  const openGpu = t.mock.method(Gpu.prototype, 'open', async () => {});
  const start = hub.startServer;
  let service;
  t.mock.method(hub, 'startServer', async opts => {
    assert.equal(opts.runtime, r);
    return service = await start({...opts, port: 0});
  });
  await plugin.default({host:{has:()=>true,async call(method,{request}){return {ok:true,result:require('./config-service/client.cjs').request(request)};}},commands: {register: (name, fn) => commands.set(name, fn)}, log() {}});
  t.after(() => plugin.deactivate());
  assert.equal(openGpu.mock.callCount(), 0);
  assert.equal(service.controller.accounts.runtime, r);
  assert.equal(service.controller.gpu.runtime, r);
  assert.equal(commands.size, 6);
  assert.equal(typeof commands.get('toolkit.configuration'), 'function');
  assert.throws(()=>commands.get('toolkit.configuration')({operation:'status'}));
  const {url} = service;
  assert.equal(new URL(url).hostname, '127.0.0.1');
  for (const name of ['open', 'accounts', 'downloads', 'gpu', 'pause-downloads']) {
    await assert.rejects(commands.get('toolkit.' + name)({}));
  }
  assert.deepEqual(launched, []);
  const html = await (await fetch(url)).text();
  const headers = {'X-Toolkit-Token': html.match(/const token='([a-f0-9]+)'/)[1], Origin: new URL(url).origin, 'Content-Type': 'application/json'};
  const state = await (await fetch(url + 'api/status', {headers})).json();
  assert.deepEqual(Object.values(state.modules).map(m => m.status), ['preview', 'host-required', 'offline']);
  openGpu.mock.restore();
  for (const [route, method] of [['downloads/jobs', 'POST'], ['gpu/open', 'POST'], ['gpu/native', 'GET']]) {
    const response = await fetch(url + 'api/' + route, {method, headers, body: method === 'POST' ? '{}' : undefined});
    assert.equal(response.status, route.startsWith('downloads')?403:400);
    assert.match((await response.json()).error, route.startsWith('downloads')?/Host/:/macOS/);
  }
  assert.equal((await (await fetch(url + 'api/accounts', {headers})).json()).requiresRefresh, true);
  const post = (route, body) => fetch(url + 'api/' + route, {method: 'POST', headers, body});
  const malformed = await post('settings', 'fixture-sensitive-input');
  assert.deepEqual(await malformed.json(), {error: 'Toolkit could not complete this request.'});
  const jobs = await (await fetch(url + 'api/downloads/jobs', {headers})).json();
  assert.deepEqual(jobs.jobs, []);
  assert.equal(jobs.availability.status, 'host-required');
  assert.equal(service.controller.downloads, null);
  assert.equal(openGpu.mock.callCount(), 0);
  await plugin.deactivate();
  await assert.rejects(fetch(url), /fetch failed/);
});
