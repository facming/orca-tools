'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {execFile} = require('node:child_process');
const execute = require('node:util').promisify(execFile);
const {DirectoryPicker} = require('./directory-picker.cjs');

test('macOS AppleScript resolves selected aliases and handles cancellation and errors', {
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
  const result=await picker.choose(selected);assert.equal(result.cancelled,false);assert.equal(fs.realpathSync(result.path),selected);
  outcome='cancel';assert.deepEqual(await picker.choose(selected),{cancelled:true});
  outcome='fail';await assert.rejects(picker.choose(selected),/could not finish/);
  assert.equal(picker.pending,null);
});
