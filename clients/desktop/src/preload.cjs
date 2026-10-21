'use strict';
const { contextBridge, ipcRenderer } = require('electron');
const origin = process.argv.find(value => value.startsWith('--freecollab-origin='))?.slice(20);
if (origin && location.origin === origin && window === window.top) {
  contextBridge.exposeInMainWorld('freecollabNative', Object.freeze({ kind: 'desktop', signIn: () => ipcRenderer.invoke('freecollab:native-signin'), cancelSignIn: () => ipcRenderer.invoke('freecollab:native-cancel') }));
} else if (location.protocol === 'file:' && location.pathname.endsWith('/setup.html') && window === window.top) {
  contextBridge.exposeInMainWorld('freecollabSetup', Object.freeze({ connect: value => ipcRenderer.invoke('freecollab:configure', value) }));
}
