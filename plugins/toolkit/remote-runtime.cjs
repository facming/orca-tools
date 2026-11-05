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
  } catch (error) {
    if (typeof error?.code === 'string') throw remoteAccountError(error);
    throw new ToolkitError('Unable to initialize the Orca remote client. Check the SDK and selected profile, then the configured remote environment and pairing.');
  }
}
// Only structured, allowlisted error codes become public diagnostics. Never echo
// SDK messages: these can contain pairing material, profile paths or account data.
function remoteAccountError(error) {
  const code = typeof error?.code === 'string' ? error.code.toLowerCase() : '';
  let message;
  if (['timeout', 'request_timeout', 'etimedout'].includes(code)) message = 'Remote accounts request timed out. Check the connection and refresh to confirm the current account.';
  else if (['permission_denied', 'forbidden', 'unauthorized', 'access_denied'].includes(code)) message = 'Remote accounts permission denied. Check the remote accounts-admin authorization.';
  else if (['pairing_required', 'not_paired', 'pairing_failed', 'invalid_pairing'].includes(code)) message = 'Remote accounts pairing failed. Check the selected environment in Orca.';
  else if (['incompatible_runtime', 'method_not_found', 'unsupported_method'].includes(code)) message = 'Remote accounts RPC is incompatible. Check the remote Orca version.';
  else if (['disconnected', 'connection_closed', 'econnreset', 'econnrefused', 'enotfound', 'enetunreach'].includes(code)) message = 'Remote accounts disconnected. Reconnect in Orca and refresh.';
  else message = 'Unable to reach remote accounts. Check the selected environment, pairing, permissions and Orca connection, then refresh.';
  return new ToolkitError(message);
}
module.exports = {loadRuntimeSdk, createRemoteClient, remoteAccountError};
