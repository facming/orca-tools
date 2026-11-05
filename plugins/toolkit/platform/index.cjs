'use strict';
const os = require('node:os');
const path = require('node:path');
const fs = require('node:fs');
const cp = require('node:child_process');
const {DirectoryPicker} = require('../directory-picker.cjs');
const {windows} = require('./windows.cjs');
const {macos} = require('./macos.cjs');

async function launch(executable, args, env) {
  try {
    const child = cp.spawn(executable, args, {env, detached: true, stdio: 'ignore', windowsHide: true});
    await new Promise((resolve, reject) => {
      child.once('error', reject);
      child.once('spawn', resolve);
    });
    child.unref();
  } catch {
    throw Error('Could not launch the requested application.');
  }
}

function createRuntime(options = {}) {
  const {platform = process.platform, home = os.homedir(), env = process.env, execPath = process.execPath} = options;
  const childEnv = platform === 'darwin'
    ? Object.fromEntries(['HOME','PATH','LANG','LC_ALL','LC_CTYPE','TZ','TMPDIR','ORCA_USER_DATA_PATH']
      .filter(key => env[key] !== undefined).map(key => [key, env[key]]))
    : {...env};
  for (const key of Object.keys(childEnv)) {
    if (/^(ELECTRON_RUN_AS_NODE|NODE_CHANNEL_FD|NODE_CHANNEL_SERIALIZATION_MODE|NODE_UNIQUE_ID)$/i.test(key)) delete childEnv[key];
  }
  const base = {platform, home, childEnv, execPath};
  let runtime;
  if (platform === 'win32') runtime = windows(base);
  else if (platform === 'darwin') runtime = macos(base, options);
  else throw Error('Toolkit requires Windows or macOS.');

  runtime.spawnDetached = (executable, args) => (options.launch || launch)(executable, args, childEnv);
  runtime.openUrl = url => {
    let parsed;
    try { parsed = new URL(url); } catch {}
    if (!parsed || !['http:', 'https:'].includes(parsed.protocol)) throw Error('Invalid browser URL.');
    return runtime.spawnDetached(runtime.browser, platform === 'win32' ? ['url.dll,FileProtocolHandler', url] : [url]);
  };
  runtime.openPath = folder => {
    if (typeof folder !== 'string' || !path.isAbsolute(folder)) throw Error('An absolute folder path is required.');
    if (platform === 'darwin') {
      try {
        if (!fs.statSync(folder).isDirectory()) throw Error();
        fs.accessSync(folder, fs.constants.R_OK | fs.constants.X_OK);
      } catch { throw Error('The requested folder is unavailable.'); }
    }
    return runtime.spawnDetached(runtime.explorer, [folder]);
  };
  const picker = new DirectoryPicker(runtime, {execute: options.execute});
  runtime.chooseDirectory = initial => picker.choose(initial);
  runtime.closePicker = () => picker.close();
  return runtime;
}

module.exports = {createRuntime, launch};
