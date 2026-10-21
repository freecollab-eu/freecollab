'use strict';
const { createHash } = require('node:crypto');
function deploymentOrigin(value) {
  if (typeof value !== 'string' || value.length > 2048 || /\s/.test(value)) throw new Error('Enter an HTTPS installation address.');
  const url = new URL(value);
  if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash || !['', '/'].includes(url.pathname))
    throw new Error('Enter the HTTPS origin of your FreeCollab installation, without a path.');
  return url.origin;
}
function sameOrigin(value, origin) { try { return new URL(value).origin === origin; } catch { return false; } }
function externalUrl(value) {
  try { const url = new URL(value); return url.protocol === 'https:' && !url.username && !url.password && value.length <= 8192 ? url.href : null; }
  catch { return null; }
}
function partitionFor(origin) { return 'persist:freecollab-' + createHash('sha256').update(deploymentOrigin(origin)).digest('hex'); }
function trustedSender(event, contents, origin) {
  return event.sender === contents && event.senderFrame === contents.mainFrame && sameOrigin(event.senderFrame.url, origin);
}
module.exports = { deploymentOrigin, sameOrigin, externalUrl, partitionFor, trustedSender };
