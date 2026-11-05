'use strict';
// Only fixed, user-facing messages should cross the management HTTP boundary.
class ToolkitError extends Error {}
function publicMessage(error, fallback = 'Toolkit could not complete this request.') {
  return error instanceof ToolkitError ? error.message : fallback;
}
module.exports = {ToolkitError, publicMessage};
