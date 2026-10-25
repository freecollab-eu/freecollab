import fs from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { writeAssociationFiles } from './associations.mjs';
export function installationOrigin(value) {
  const url = new URL(value);
  if (typeof value !== 'string' || value.length > 2048 || /\s/.test(value) || url.protocol !== 'https:' || url.username || url.password
      || url.search || url.hash || !['', '/'].includes(url.pathname)) throw new Error('FREECOLLAB_APP_URL must be an HTTPS installation origin without credentials, path, query or fragment.');
  return url.origin;
}
export function applicationId(value) {
  if (typeof value !== 'string' || value.length > 200 || !/^[A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z][A-Za-z0-9]*)+$/.test(value)) throw new Error('FREECOLLAB_APPLICATION_ID must be a reverse-DNS Android and iOS application identifier.');
  return value;
}
export async function configure(root = fileURLToPath(new URL('../', import.meta.url))) {
  const file = path.join(root, 'capacitor.config.json'), config = JSON.parse(await fs.readFile(file, 'utf8'));
  const origin = installationOrigin(process.env.FREECOLLAB_APP_URL || config.server.url);
  config.appId = applicationId(process.env.FREECOLLAB_APPLICATION_ID || config.appId);
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
  // Customer signing, Firebase package and APNs topic must select this same ID.
  for (const [relative, replace] of [
    ['android/app/build.gradle', text => text.replace(/applicationId\s+"[A-Za-z0-9_.]+"/, 'applicationId "' + config.appId + '"')],
    ['ios/App/App.xcodeproj/project.pbxproj', text => text.replace(/PRODUCT_BUNDLE_IDENTIFIER = [A-Za-z0-9_.]+;/g, 'PRODUCT_BUNDLE_IDENTIFIER = ' + config.appId + ';')],
  ]) {
    try { const target=path.join(root,relative); await fs.writeFile(target,replace(await fs.readFile(target,'utf8'))); }
    catch (failure) { if (failure.code !== 'ENOENT') throw failure; }
  }
  const host = new URL(origin).hostname;
  const manifest = path.join(root, 'android/app/src/main/AndroidManifest.xml');
  try {
    const contents = await fs.readFile(manifest, 'utf8');
    const filter = '<!-- FREECOLLAB_VERIFIED_LINKS_START -->\n            <intent-filter android:autoVerify="true">\n                <action android:name="android.intent.action.VIEW" />\n                <category android:name="android.intent.category.DEFAULT" />\n                <category android:name="android.intent.category.BROWSABLE" />\n                <data android:scheme="https" android:host="' + host + '" android:path="/open" />\n            </intent-filter>\n            <!-- FREECOLLAB_VERIFIED_LINKS_END -->';
    const updated = /<!-- FREECOLLAB_VERIFIED_LINKS_START -->[\s\S]*?<!-- FREECOLLAB_VERIFIED_LINKS_END -->/.test(contents)
      ? contents.replace(/<!-- FREECOLLAB_VERIFIED_LINKS_START -->[\s\S]*?<!-- FREECOLLAB_VERIFIED_LINKS_END -->/, filter)
      : contents.replace('</activity>', '    ' + filter + '\n        </activity>');
    await fs.writeFile(manifest, updated);
  } catch (failure) { if (failure.code !== 'ENOENT') throw failure; }
  const entitlements = path.join(root, 'ios/App/App/App.entitlements');
  try {
    let contents = await fs.readFile(entitlements, 'utf8');
    const domains = '<key>com.apple.developer.associated-domains</key><array><string>applinks:' + host + '</string></array>';
    contents = /<key>com.apple.developer.associated-domains<\/key>\s*<array>[\s\S]*?<\/array>/.test(contents)
      ? contents.replace(/<key>com.apple.developer.associated-domains<\/key>\s*<array>[\s\S]*?<\/array>/, domains)
      : contents.replace('</dict>', domains + '</dict>');
    await fs.writeFile(entitlements, contents);
  } catch (failure) { if (failure.code !== 'ENOENT') throw failure; }
  await writeAssociationFiles(path.join(root, 'association-files'), { origin, applicationId: config.appId,
    appleTeamId: process.env.FREECOLLAB_APPLE_TEAM_ID,
    androidFingerprints: (process.env.FREECOLLAB_ANDROID_CERT_SHA256 || '').split(',').map(value => value.trim()).filter(Boolean) });
  console.log('FreeCollab mobile installation: ' + origin);
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await configure();
