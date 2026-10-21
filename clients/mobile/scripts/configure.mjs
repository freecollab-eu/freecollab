import fs from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
export function installationOrigin(value) {
  const url = new URL(value);
  if (typeof value !== 'string' || value.length > 2048 || /\s/.test(value) || url.protocol !== 'https:' || url.username || url.password
      || url.search || url.hash || !['', '/'].includes(url.pathname)) throw new Error('FREECOLLAB_APP_URL must be an HTTPS installation origin without credentials, path, query or fragment.');
  return url.origin;
}
export async function configure(root = fileURLToPath(new URL('../', import.meta.url))) {
  const file = path.join(root, 'capacitor.config.json'), config = JSON.parse(await fs.readFile(file, 'utf8'));
  const origin = installationOrigin(process.env.FREECOLLAB_APP_URL || config.server.url);
  config.server = { url: origin, cleartext: false, allowNavigation: [], errorPath: 'index.html' };
  config.loggingBehavior = 'none'; config.android = { ...config.android, allowMixedContent: false, webContentsDebuggingEnabled: false };
  await fs.writeFile(file, JSON.stringify(config, null, 2) + '\n');
  await fs.writeFile(path.join(root, 'www/installation.js'), 'document.getElementById("retry").addEventListener("click",()=>location.assign(' + JSON.stringify(origin + '/') + '));\n');
  const plist = path.join(root, 'ios/App/App/Info.plist');
  try {
    let contents = await fs.readFile(plist, 'utf8');
    const domains = '<key>WKAppBoundDomains</key>\n\t<array><string>' + new URL(origin).hostname + '</string></array>';
    contents = /<key>WKAppBoundDomains<\/key>\s*<array>[\s\S]*?<\/array>/.test(contents)
      ? contents.replace(/<key>WKAppBoundDomains<\/key>\s*<array>[\s\S]*?<\/array>/, domains)
      : contents.replace(/<\/dict>\s*<\/plist>/, '\t' + domains + '\n</dict>\n</plist>');
    await fs.writeFile(plist, contents);
  } catch (failure) { if (failure.code !== 'ENOENT') throw failure; }
  console.log('FreeCollab mobile installation: ' + origin);
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await configure();
