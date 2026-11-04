'use strict';
const fs = require('node:fs'), path = require('node:path'), cp = require('node:child_process');
const {ToolkitError} = require('./platform/errors.cjs');
const execFile = require('node:util').promisify(cp.execFile);

class DirectoryPicker {
  constructor(runtime, options = {}) {
    this.runtime = runtime;
    this.execute = options.execute || execFile;
    this.platform = options.platform || runtime.platform;
    this.pending = null;
  }
  async choose(initial = '') {
    if (this.pending) throw new ToolkitError('A folder selection window is already open.');
    if (typeof initial !== 'string' || initial.length > 4096 || /[\x00-\x1f]/.test(initial)) throw new ToolkitError('Invalid initial folder.');
    const mac = this.platform === 'darwin';
    let exe, args, env = {};
    if (mac) {
      if (initial && !path.isAbsolute(initial)) throw new ToolkitError('Invalid initial folder.');
      // The default download folder may not exist until the first download.
      args = [path.join(__dirname, 'choose-directory.applescript')];
      try { if (initial && fs.statSync(initial).isDirectory()) args.push(initial); }
      catch (error) { if (error.code !== 'ENOENT') throw new ToolkitError('The initial folder is unavailable.'); }
      exe = '/usr/bin/osascript';
      for (const key of ['HOME', 'TMPDIR', 'LANG', 'LC_CTYPE']) {
        if (this.runtime.childEnv[key]) env[key] = this.runtime.childEnv[key];
      }
    } else if (this.platform === 'win32') {
      const source = this.runtime.childEnv || {};
      for (const key of ['SystemRoot','WINDIR','TEMP','TMP','USERPROFILE','APPDATA','LOCALAPPDATA','HOMEDRIVE','HOMEPATH']) {
        if (source[key]) env[key] = source[key];
      }
      env.SystemRoot = env.SystemRoot || 'C:\\Windows';
      env.ORCA_TOOLKIT_FOLDER_BASE64 = Buffer.from(initial, 'utf8').toString('base64');
      exe = path.win32.join(env.SystemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe');
      args = ['-NoProfile','-NonInteractive','-STA','-ExecutionPolicy','Bypass','-File',path.join(__dirname, 'choose-directory.ps1')];
    } else throw new ToolkitError('Folder selection is unavailable on this platform.');

    try {
      let result;
      try {
        this.pending = this.execute(exe, args, {env, windowsHide: true, encoding: 'utf8', timeout: 600000, maxBuffer: 65536});
        const {stdout} = await this.pending;
        if (mac) {
          const value = stdout.replace(/\r?\n$/, '');
          if (value === 'cancelled') return {cancelled: true};
          if (!value.startsWith('selected:')) throw Error();
          result = {cancelled: false, path: path.normalize(value.slice('selected:'.length))};
        } else result = JSON.parse(stdout.replace(/^\uFEFF/, ''));
      } catch { throw new ToolkitError('The folder selection window could not finish. Try again or enter a folder path manually.'); }
      if (result?.cancelled === true) return {cancelled: true};
      if (result?.cancelled !== false || typeof result.path !== 'string' || !path.isAbsolute(result.path) ||
          result.path.length > 220 || /[\x00-\x1f]/.test(result.path)) throw new ToolkitError('Choose an absolute folder path shorter than 221 characters.');
      try {
        if (!fs.statSync(result.path).isDirectory()) throw Error();
        if (mac) fs.accessSync(result.path, fs.constants.W_OK | fs.constants.X_OK);
      } catch { throw new ToolkitError('The selected folder is unavailable.'); }
      return {cancelled: false, path: result.path};
    } finally { this.pending = null; }
  }
  close() { this.pending?.child?.kill(); }
}
module.exports = {DirectoryPicker};
