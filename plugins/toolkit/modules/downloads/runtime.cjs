'use strict';
// Keep the legacy Windows entry point for existing callers.
const {createRuntime, launch} = require('../../platform/index.cjs');
const windowsRuntime = (env, execPath, home) => createRuntime({platform: 'win32', env, execPath, home});
module.exports = {createRuntime, windowsRuntime, launch};
