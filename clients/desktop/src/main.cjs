'use strict';
const { app, BrowserWindow, Menu, ipcMain, session, dialog, shell, desktopCapturer, net } = require('electron');
const fs = require('node:fs/promises');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { deploymentOrigin, sameOrigin, externalUrl, partitionFor, trustedSender } = require('./policy.cjs');
const { browserSignIn } = require('./sign-in.cjs');
const { PermissionGrants } = require('./permissions.cjs');
const { createDesktopUpdater } = require('./update-controller.cjs');
let window, origin = null, pendingSignIn = null, signInAbort = null;
const desktopUpdates = createDesktopUpdater({ app, dialog, net, getWindow: () => window });
const setupUrl = pathToFileURL(path.join(__dirname, 'setup.html')).href;
const configuredSessions = new Set();
async function askExternal(value) {
  const url = externalUrl(value); if (!url || !window) return;
  const answer = await dialog.showMessageBox(window, { type: 'question', buttons: ['Cancel', 'Open browser'], defaultId: 0, cancelId: 0,
    title: 'Open in your browser?', message: 'This link opens outside FreeCollab.', detail: new URL(url).origin });
  if (answer.response === 1) await shell.openExternal(url);
}
function privateSession(address) {
  const target = session.fromPartition(partitionFor(address));
  if (configuredSessions.has(target)) return target;
  configuredSessions.add(target);
  const grants = new PermissionGrants();
  const permitted = permission => ['media', 'notifications', 'fullscreen', 'display-capture', 'clipboard-sanitized-write'].includes(permission);
  target.setPermissionCheckHandler((contents, permission, requestingOrigin, details) => Boolean(contents === window?.webContents
    && details.isMainFrame && sameOrigin(contents.getURL(), address) && sameOrigin(requestingOrigin, address)
    && permitted(permission) && grants.check(permission, details)));
  target.setPermissionRequestHandler(async (contents, permission, callback, details) => {
    if (contents !== window?.webContents || !sameOrigin(contents.getURL(), address) || !permitted(permission)
      || details.isMainFrame === false || details.requestingUrl && !sameOrigin(details.requestingUrl, address)) { callback(false); return; }
    try {
      const answer = await dialog.showMessageBox(window, { type: 'question', title: 'FreeCollab permission', buttons: ['Deny', 'Allow'], defaultId: 0, cancelId: 0,
        message: `Allow ${permission === 'media' ? 'camera or microphone' : permission.replace(/-/g, ' ')} access?`, detail: address });
      const allowed = answer.response === 1 && contents === window?.webContents && sameOrigin(contents.getURL(), address);
      if (allowed) grants.approve(permission, details);
      callback(allowed);
    } catch { callback(false); }
  });
  target.setDisplayMediaRequestHandler(async (request, callback) => {
    if (!window || request.frame !== window.webContents.mainFrame || !sameOrigin(request.securityOrigin, address)) { callback({}); return; }
    try {
      const sources = await desktopCapturer.getSources({ types: ['window', 'screen'], thumbnailSize: { width: 0, height: 0 } });
      const choice = await dialog.showMessageBox(window, { title: 'Choose a screen or window to share', message: 'Only share what you intend your meeting to see.',
        buttons: ['Cancel', ...sources.map(source => source.name.slice(0, 100))], defaultId: 0, cancelId: 0 });
      const source = sources[choice.response - 1]; callback(source && sameOrigin(window.webContents.getURL(), address) ? { video: source } : {});
    } catch { callback({}); }
  }, { useSystemPicker: true });
  target.on('will-download', (event, _item, contents) => { if (contents !== window?.webContents || !sameOrigin(contents.getURL(), address)) event.preventDefault(); });
  return target;
}
async function createWindow(address = null) {
  signInAbort?.abort(); const previous = window, previousOrigin = origin;
  origin = address;
  window = new BrowserWindow({ width: 1280, height: 860, minWidth: 320, minHeight: 480, backgroundColor: '#f5f5ef', title: 'FreeCollab',
    icon: path.join(__dirname, '../resources/icon.png'),
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'), contextIsolation: true, nodeIntegration: false, sandbox: true,
      webSecurity: true, allowRunningInsecureContent: false, webviewTag: false, spellcheck: true,
      ...(address ? { session: privateSession(address), additionalArguments: ['--freecollab-origin=' + address] } : {}) } });
  const current = window;
  current.webContents.setWindowOpenHandler(details => { if (current === window && address && sameOrigin(current.webContents.getURL(), address)) void askExternal(details.url); return { action: 'deny' }; });
  current.webContents.on('will-attach-webview', event => event.preventDefault());
  for (const name of ['will-navigate', 'will-redirect']) current.webContents.on(name, (event, url) => {
    if (address && sameOrigin(url, address) || !address && url === setupUrl) return;
    event.preventDefault(); if (current === window && address) void askExternal(url);
  });
  current.on('closed', () => { if (window === current) { signInAbort?.abort(); window = null; } });
  try {
    if (address) await current.loadURL(address + '/'); else await current.loadURL(setupUrl);
  } catch {
    window = previous; origin = previousOrigin; current.close();
    throw new Error('This installation could not be opened. Check its address and HTTPS certificate, then try again.');
  }
  previous?.close();
}
ipcMain.handle('freecollab:configure', async (event, value) => {
  if (!window || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame || event.senderFrame.url !== setupUrl) throw new Error('Setup is unavailable.');
  const address = deploymentOrigin(value), file = path.join(app.getPath('userData'), 'installation.json');
  await createWindow(address);
  await fs.mkdir(path.dirname(file), { recursive: true });
  await fs.writeFile(file + '.tmp', JSON.stringify({ origin: address }), { mode: 0o600 }); await fs.rename(file + '.tmp', file);
});
ipcMain.handle('freecollab:native-signin', async event => {
  if (!window || !origin || !trustedSender(event, window.webContents, origin)) throw new Error('Sign-in is unavailable.');
  if (pendingSignIn) throw new Error('A browser sign-in is already open.');
  const contents = window.webContents, address = origin; signInAbort = new AbortController();
  pendingSignIn = browserSignIn(address, url => shell.openExternal(url), { signal: signInAbort.signal });
  try {
    const result = await pendingSignIn;
    if (contents !== window?.webContents || origin !== address || !trustedSender(event, contents, address)) throw new Error('The installation changed. Start again.');
    return result;
  } finally { pendingSignIn = null; signInAbort = null; }
});
ipcMain.handle('freecollab:native-cancel', event => {
  if (!window || !origin || !trustedSender(event, window.webContents, origin)) throw new Error('Sign-in is unavailable.');
  signInAbort?.abort();
});
async function start() {
  let address = null;
  try { address = deploymentOrigin(JSON.parse(await fs.readFile(path.join(app.getPath('userData'), 'installation.json'), 'utf8')).origin); } catch { /* First launch uses the explicit setup form. */ }
  try { await createWindow(address); }
  catch { await createWindow(); }
  await desktopUpdates.ready();
  desktopUpdates.start();
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    ...(process.platform === 'darwin' ? [{ role: 'appMenu' }] : []),
    { label: 'FreeCollab', submenu: [{ label: 'Choose another installation…', click: () => void createWindow() },
      { label: 'Check for updates…', click: () => void desktopUpdates.check(true) }, { role: 'quit' }] },
    { role: 'editMenu' }, { role: 'viewMenu' }, { role: 'windowMenu' },
  ]));
}
if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => { if (window) { if (window.isMinimized()) window.restore(); window.focus(); } });
  app.whenReady().then(start).catch(() => { dialog.showErrorBox('FreeCollab could not start', 'Check that this installation address is available, then reopen the app.'); app.quit(); });
  app.on('activate', () => { if (!window) void start(); });
  app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });
}
