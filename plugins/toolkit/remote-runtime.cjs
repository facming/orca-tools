'use strict';
const {ToolkitError} = require('./platform/errors.cjs');
function loadRuntimeSdk(file) {
  try {
    const sdk = require(file);
    if (typeof sdk.RuntimeClient !== 'function' || typeof sdk.RuntimeClient.prototype.call !== 'function') throw Error();
    return sdk;
  } catch { throw new ToolkitError('Orca RuntimeClient SDK is missing or has incompatible exports.'); }
}
function createRemoteClient(runtime, environment) {
  try {
    const {RuntimeClient} = runtime.loadSdk();
    return new RuntimeClient(runtime.userDataPath, 30000, null, environment, runtime.cli);
  } catch { throw new ToolkitError('Unable to initialize the Orca remote client. Check the SDK and selected profile.'); }
}
module.exports = {loadRuntimeSdk, createRemoteClient};
