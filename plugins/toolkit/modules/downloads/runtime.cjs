'use strict';
// Keep the legacy Windows entry point for existing callers.
const {createRuntime, launch} = require('../../platform/index.cjs');
const windowsRuntime = (env, execPath, home) => createRuntime({platform: 'win32', env, execPath, home});
function checkDownloadsRuntime(runtime){
  if(runtime.platform!=='darwin')return;
  const fs=require('node:fs'),path=require('node:path');
  try{for(const executable of [runtime.cli,runtime.ssh,runtime.keygen]){if(!path.isAbsolute(executable)||!fs.statSync(executable).isFile())throw Error();fs.accessSync(executable,fs.constants.X_OK);}
    if(typeof runtime.loadSdk!=='function'||typeof runtime.loadSdk().RuntimeClient!=='function'||!runtime.childEnv)throw Error();
  }catch{throw new (require('../../platform/errors.cjs').ToolkitError)('Downloads requires a verified Orca runtime and executable OpenSSH tools.');}
}
module.exports = {createRuntime, windowsRuntime, launch, checkDownloadsRuntime};
