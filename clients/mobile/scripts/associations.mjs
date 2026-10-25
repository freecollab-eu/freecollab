import fs from 'node:fs/promises';
import path from 'node:path';
export function associationFiles({ origin, applicationId, appleTeamId, androidFingerprints = [] }) {
  const url = new URL(origin);
  if (url.protocol !== 'https:' || url.username || url.password || url.pathname !== '/' || url.search || url.hash)
    throw new Error('Native associations require the exact HTTPS installation origin.');
  if (!/^[A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z][A-Za-z0-9]*)+$/.test(applicationId))
    throw new Error('Use the configured native application identifier.');
  const result = {};
  if (appleTeamId) {
    if (!/^[A-Z0-9]{10}$/.test(appleTeamId)) throw new Error('Use the real ten-character Apple Team ID from the signing account.');
    result['apple-app-site-association'] = { applinks: { details: [{
      appIDs: [appleTeamId + '.' + applicationId],
      components: [{ '/': '/open', comment: 'Only explicit FreeCollab resource links open the app.' }],
    }] } };
  }
  if (!Array.isArray(androidFingerprints) || androidFingerprints.length > 8) throw new Error('Provide at most eight actual Android signing-certificate SHA-256 fingerprints.');
  if (androidFingerprints.length) {
    const fingerprints = [...new Set(androidFingerprints.map(value => {
      if (typeof value !== 'string' || !/^(?:[A-Fa-f0-9]{2}:){31}[A-Fa-f0-9]{2}$/.test(value))
        throw new Error('Android App Links need real colon-separated SHA-256 signing fingerprints.');
      return value.toUpperCase();
    }))].sort();
    result['assetlinks.json'] = [{ relation: ['delegate_permission/common.handle_all_urls'],
      target: { namespace: 'android_app', package_name: applicationId, sha256_cert_fingerprints: fingerprints } }];
  }
  return result;
}
export async function writeAssociationFiles(directory, configuration) {
  const values = associationFiles(configuration), target = path.join(directory, '.well-known');
  await fs.mkdir(target, { recursive: true });
  for (const name of ['apple-app-site-association', 'assetlinks.json'])
    if (!(name in values)) await fs.rm(path.join(target, name), { force: true });
  for (const [name, value] of Object.entries(values)) await fs.writeFile(path.join(target, name), JSON.stringify(value, null, 2) + '\n');
  return values;
}
