'use strict';
const path = require('node:path');
const fs = require('node:fs');
const {loadRuntimeSdk} = require('../remote-runtime.cjs');

function windows({platform, home, execPath, childEnv}) {
  const local = childEnv.LOCALAPPDATA || path.join(home, 'AppData', 'Local');
  const roaming = childEnv.APPDATA || path.join(home, 'AppData', 'Roaming');
  const root = childEnv.SystemRoot || childEnv.SYSTEMROOT || childEnv.WINDIR || 'C:\\Windows';
  const system = path.join(root, 'System32');
  const cli = [path.join(path.dirname(execPath), 'resources', 'bin', 'orca.exe'),
    path.join(local, 'Programs', 'orca', 'resources', 'bin', 'orca.exe')].find(file => {
    try { fs.accessSync(file, fs.constants.X_OK); return fs.statSync(file).isFile(); }
    catch { return false; }
  });
  if (!cli) throw Error('Cannot locate the Orca CLI. Reinstall Orca with its command-line tools.');
  Object.assign(childEnv, {USERPROFILE: childEnv.USERPROFILE || home, HOME: childEnv.HOME || home,
    LOCALAPPDATA: local, APPDATA: roaming, SystemRoot: root,
    PROGRAMDATA: childEnv.PROGRAMDATA || path.join(path.parse(root).root, 'ProgramData')});
  const dataDir = path.join(local, 'OrcaTools');
  return {platform, home, local, childEnv, cli, dataDir, configDir: dataDir,
    cacheDir: path.join(dataDir, 'cache'), logDir: path.join(dataDir, 'logs'),
    downloadsDir: path.join(local, 'OrcaParallelDownloads'), gpuDir: path.join(local, 'LocalGpuBridge'),
    userDataPath: childEnv.ORCA_USER_DATA_PATH || path.join(roaming, 'orca'),
    // Defer the SDK on Windows as before, so a missing Accounts SDK does not prevent Downloads.
    loadSdk: () => loadRuntimeSdk(path.join(path.dirname(cli), '..', 'app.asar.unpacked', 'out', 'cli', 'runtime-client.js')),
    ssh: path.join(system, 'OpenSSH', 'ssh.exe'), keygen: path.join(system, 'OpenSSH', 'ssh-keygen.exe'),
    browser: path.join(system, 'rundll32.exe'), explorer: path.join(root, 'explorer.exe')};
}
module.exports = {windows};
