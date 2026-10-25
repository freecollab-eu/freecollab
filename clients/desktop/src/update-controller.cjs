'use strict';
const fs = require('node:fs/promises');
const path = require('node:path');
const updates = require('./updates.cjs');
function createDesktopUpdater({ app, dialog, net, getWindow }) {
  const root = path.join(app.getPath('userData'), 'updates');
  let config = null, staged = null, checking = false, installing = false, notified = '', timer;
  async function configured() {
    if (config) return config;
    let value;
    try { value = JSON.parse(await fs.readFile(path.join(__dirname, '../resources/update-config.json'), 'utf8')); }
    catch { value = { manifest_url: process.env.FREECOLLAB_UPDATE_MANIFEST_URL, public_key: process.env.FREECOLLAB_UPDATE_PUBLIC_KEY }; }
    config = updates.configuration(value); return config;
  }
  async function check(interactive = false) {
    if (checking || installing) return;
    checking = true;
    try {
      const selected = await configured();
      const response = await net.fetch(selected.manifest, { redirect: 'error', credentials: 'omit', signal: AbortSignal.timeout(20000), headers: { Accept: 'application/json' } });
      if (!response.ok || response.redirected) throw new Error('The update manifest could not be fetched directly.');
      let body = '';
      for await (const chunk of response.body) { body += Buffer.from(chunk).toString('utf8'); if (body.length > 24000) throw new Error('The update manifest is too large.'); }
      let sequence = 0;
      try { sequence = JSON.parse(await fs.readFile(path.join(root, 'sequence.json'), 'utf8')).sequence || 0; } catch {}
      let seen = null;
      try { seen = JSON.parse(await fs.readFile(path.join(root, 'seen.json'), 'utf8')); } catch {}
      const manifest = updates.verifyManifest(JSON.parse(body), selected, { platform: process.platform, arch: process.arch, currentVersion: app.getVersion(), sequence: Math.max(sequence, (seen?.sequence || 0) - 1) });
      if (seen && manifest.sequence === seen.sequence && (manifest.version !== seen.version || manifest.sha256 !== seen.sha256))
        throw new Error('A publisher reused an update sequence for different application bytes.');
      const download = await net.fetch(manifest.url, { redirect: 'error', credentials: 'omit', signal: AbortSignal.timeout(300000) });
      staged = await updates.stagePackage(download, manifest, root);
      await fs.writeFile(path.join(root, 'seen.json'), JSON.stringify({ sequence: manifest.sequence, version: manifest.version, sha256: manifest.sha256 }), { mode: 0o600 });
      if (notified !== manifest.version || interactive) {
        notified = manifest.version;
        const choice = await dialog.showMessageBox(getWindow(), { type: 'info', title: 'FreeCollab update ready', message: 'Version ' + manifest.version + ' has been verified and downloaded.', detail: 'Save your work before restarting. The previous application is restored if the new version cannot complete startup.', buttons: ['Later', 'Restart and install'], defaultId: 0, cancelId: 0 });
        if (choice.response === 1) await install();
      }
    } catch (failure) {
      if (interactive) await dialog.showMessageBox(getWindow(), { type: 'info', title: 'FreeCollab updates', message: failure instanceof Error ? failure.message : 'The update check could not be completed.', buttons: ['Close'] });
    } finally { checking = false; }
  }
  async function install() {
    if (!staged || installing) return;
    if (!app.isPackaged) throw new Error('Automatic installation requires a packaged desktop application.');
    installing = true;
    let target = process.platform === 'linux' ? process.env.APPIMAGE : process.platform === 'darwin' ? path.resolve(process.execPath, '../../..') : path.dirname(process.execPath);
    if (!target || !path.isAbsolute(target)) { installing = false; throw new Error('Use the packaged AppImage, macOS application or per-user Windows client for automatic updates.'); }
    const launcher = process.platform === 'linux' ? target : process.execPath;
    let plan;
    try { plan = await updates.prepareInstallation(staged, { platform: process.platform, target, launcher, root }); }
    catch (error) { installing = false; throw error; }
    let quitting = false;
    const apply = () => { quitting = true; plan.launch(); };
    app.once('will-quit', apply);
    app.quit();
    // A renderer's unsaved-work/beforeunload veto must not silently arm a
    // later unrelated quit. Only the explicitly requested restart installs.
    setTimeout(() => { if (!quitting) { app.removeListener('will-quit', apply); installing = false; void plan.cancel(); } }, 3000).unref();
  }
  async function ready() {
    const nonce = process.argv.find(value => value.startsWith('--freecollab-update-health='))?.split('=')[1];
    if (nonce) await updates.acknowledgeStartup(root, app.getVersion(), nonce);
  }
  function start() {
    if (timer || !app.isPackaged) return;
    timer = setTimeout(() => { void check(); timer = setInterval(() => void check(), 6 * 3600000); timer.unref(); }, 30000); timer.unref();
  }
  return { check, install, ready, start };
}
module.exports = { createDesktopUpdater };
