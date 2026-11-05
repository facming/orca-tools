'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {execFile} = require('node:child_process');
const execute = require('node:util').promisify(execFile);
const {DirectoryPicker} = require('./directory-picker.cjs');
const {startServer} = require('./server.cjs');

test('macOS AppleScript saves selected aliases and preserves preferences on cancellation and errors', {
  skip: process.platform !== 'darwin' && 'requires the macOS AppleScript interpreter',
}, async t => {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'toolkit-applescript-')));
  t.after(() => fs.rmSync(root, {recursive: true, force: true}));
  const selected = path.join(root, "中文 folder's $name");
  fs.mkdirSync(selected);
  const source = fs.readFileSync(path.join(__dirname, 'choose-directory.applescript'), 'utf8');
  const driver = path.join(root, 'fixture.applescript');
  // Inherit the production run handler and replace only the interactive command.
  // osascript still executes the real assignment, alias conversion and error handling.
  fs.writeFileSync(driver, `script picker
${source}
end script
script fixture
    property parent : picker
    on requestFolder(argv)
        if item 1 of argv is "cancel" then error number -128
        if item 1 of argv is "fail" then error "fixture failure" number -1700
        return POSIX file (item 2 of argv) as alias
    end requestFolder
end script
on run argv
    return run script fixture with parameters argv
end run
`);
  let outcome = 'select';
  const picker = new DirectoryPicker({platform: 'darwin', childEnv: {}}, {
    execute(exe, args, options) {
      assert.equal(exe, '/usr/bin/osascript');
      assert.equal(args[0], path.join(__dirname, 'choose-directory.applescript'));
      return execute(exe, [driver, outcome, selected], options);
    },
  });
  const configDir = path.join(root, 'settings');
  const options = {port: 0, runtime: {platform: 'darwin', home: root, configDir}, directoryPicker: picker};
  let service = await startServer(options);
  t.after(() => service.close());
  let headers;
  async function refreshSession() {
    const html = await (await fetch(service.url)).text();
    headers = {'X-Toolkit-Token': html.match(/const token='([a-f0-9]+)'/)[1],
      Origin: new URL(service.url).origin, 'Content-Type': 'application/json'};
  }
  const choose = () => fetch(service.url + 'api/downloads/choose-folder', {
    method: 'POST', headers, body: JSON.stringify({initialDirectory: selected}),
  });
  const jobs = async () => (await fetch(service.url + 'api/downloads/jobs', {headers})).json();
  await refreshSession();
  const response = await choose();
  assert.equal(response.status, 200);
  const result = await response.json();
  assert.equal(result.cancelled, false);
  assert.equal(fs.realpathSync(result.path), selected);
  assert.equal(result.warning, undefined);
  const preference = path.join(configDir, 'downloads-preferences.json');
  const readPreference=()=>require('./config-service/client.cjs').request({op:'read',namespace:'downloads-preferences'}).raw_json;
  const saved = readPreference();
  assert.equal(fs.existsSync(preference),false);
  assert.equal((await jobs()).defaultFolder, result.path);
  outcome = 'cancel';
  assert.deepEqual(await (await choose()).json(), {cancelled: true});
  assert.deepEqual(readPreference(), saved);
  outcome = 'fail';
  const failed = await choose();
  assert.equal(failed.status, 400);
  assert.deepEqual(await failed.json(), {error: 'The folder selection window could not finish. Try again or enter a folder path manually.'});
  assert.deepEqual(readPreference(), saved);
  assert.equal(picker.pending, null);
  await service.close();
  service = await startServer(options);
  await refreshSession();
  assert.equal((await jobs()).defaultFolder, result.path);
});

require('./fixtures/config-service.cjs').hooks(test);
