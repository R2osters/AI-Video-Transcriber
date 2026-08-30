// Pont de l'écran de démarrage : état du moteur + passage à la fenêtre principale.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('splash', {
  status: () => ipcRenderer.invoke('avt:engine-status'),
  continue: () => ipcRenderer.invoke('avt:continue'),
  onServerReady: (cb) => ipcRenderer.on('avt:server-ready', () => cb()),
});
