'use strict';
const fs = require('node:fs');
const {loadRuntimeSdk} = require('../remote-runtime.cjs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');

function discoverBundle(execPath, childEnv, home, run, applicationsDir) {
  // Start at the actual worker executable, including an Electron helper nested in Orca.app.
  // A standalone Node executable has no authoritative host to take an SDK from.
  let host;
  try {
    let dir = path.dirname(fs.realpathSync(execPath));
    while (dir !== path.dirname(dir)) {
      if (dir.endsWith('.app')) {
        const plist = path.join(dir, 'Contents', 'Info.plist');
        if (run('/usr/bin/plutil', ['-extract', 'CFBundleIdentifier', 'raw', '-o', '-', plist],
          {env: childEnv, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe']}).trim() === 'com.stablyai.orca') {
          host = dir;
          break;
        }
      }
      dir = path.dirname(dir);
    }
  } catch {}
  if (!host) throw Error('Unable to identify the running Orca app bundle.');

  let installations;
  try {
    const indexed = run('/usr/bin/mdfind', ['-0', 'kMDItemCFBundleIdentifier == "com.stablyai.orca"'],
      {env: childEnv, encoding: 'utf8', timeout: 5000, stdio: ['ignore', 'pipe', 'pipe']});
    installations = new Set([host]);
    for (const candidate of [...indexed.split('\0').filter(Boolean), path.join(applicationsDir, 'Orca.app'), path.join(home, 'Applications/Orca.app')]) {
      try { installations.add(fs.realpathSync(candidate)); } catch (error) { if (error.code !== 'ENOENT') throw error; }
    }
  } catch { throw Error('Unable to verify the Orca installation.'); }
  if (installations.size !== 1) throw Error('Multiple Orca installations found. Resolve the ambiguity before using Toolkit.');
  return host;
}

function bundleFile(bundle, relative, mode, message) {
  try {
    const file = fs.realpathSync(path.join(bundle, relative));
    if (!file.startsWith(bundle + path.sep) || !fs.statSync(file).isFile()) throw Error();
    fs.accessSync(file, mode);
    return file;
  } catch { throw Error(message); }
}

function profilePath(env, home, sdk, pluginRoot) {
  try {
    const override = env.ORCA_USER_DATA_PATH;
    const standard = path.join(home, 'Library', 'Application Support', 'orca');
    const value = override ?? sdk.getDefaultUserDataPath?.('darwin', home) ?? standard;
    if (!path.isAbsolute(value) || (!override && value !== standard)) throw Error();
    const profile = fs.realpathSync(value);
    if (!fs.statSync(profile).isDirectory()) throw Error();
    fs.accessSync(profile, fs.constants.R_OK | fs.constants.X_OK);
    if (!override && pluginRoot) {
      const installed = path.join(profile, 'plugins', 'local-tools.toolkit');
      // Orca installs each reviewed artifact in a content-hash child of its plugin key.
      if (path.dirname(fs.realpathSync(pluginRoot)) !== installed) throw Error();
    }
    return profile;
  } catch { throw Error('Cannot verify the Orca profile. Specify an accessible ORCA_USER_DATA_PATH.'); }
}

function ensurePrivateDirectory(dir) {
  try {
    fs.mkdirSync(dir, {recursive: true, mode: 0o700});
    const stat = fs.lstatSync(dir);
    if (!stat.isDirectory() || stat.uid !== process.getuid() || (stat.mode & 0o077)) throw Error();
    fs.accessSync(dir, fs.constants.R_OK | fs.constants.W_OK | fs.constants.X_OK);
  } catch { throw Error('Toolkit needs a private, writable data directory.'); }
}

function macos({platform, execPath, home, childEnv}, options) {
  const bundle = discoverBundle(execPath, childEnv, home, options.run || execFileSync, options.applicationsDir || '/Applications');
  const cli = bundleFile(bundle, 'Contents/Resources/bin/orca', fs.constants.X_OK, 'Orca CLI is missing or not executable.');
  const sdkFile = bundleFile(bundle, 'Contents/Resources/app.asar.unpacked/out/cli/runtime-client.js',
    fs.constants.R_OK, 'Orca RuntimeClient SDK is unavailable.');
  const sdk = loadRuntimeSdk(sdkFile);
  const userDataPath = profilePath(childEnv, home, sdk, options.pluginRoot);
  // Give CLI children the same verified profile as RuntimeClient.
  childEnv.ORCA_USER_DATA_PATH = userDataPath;
  const ssh = '/usr/bin/ssh', keygen = '/usr/bin/ssh-keygen';
  try {
    for (const file of [ssh, keygen]) {
      if (!fs.statSync(file).isFile()) throw Error();
      fs.accessSync(file, fs.constants.X_OK);
    }
  } catch { throw Error('The system OpenSSH tools are unavailable.'); }

  const library = path.join(home, 'Library'), local = path.join(library, 'Application Support');
  const dataDir = path.join(local, 'OrcaTools');
  return {platform, home, local, gpuDir: path.join(local, 'LocalGpuBridge'), dataDir, configDir: dataDir, cacheDir: path.join(library, 'Caches', 'OrcaTools'),
    logDir: path.join(library, 'Logs', 'OrcaTools'), downloadsDir: path.join(dataDir, 'downloads'),
    ensurePrivateDirectory, childEnv, cli, userDataPath, loadSdk: () => sdk, ssh, keygen,
    browser: '/usr/bin/open', explorer: '/usr/bin/open'};
}
module.exports = {macos};
