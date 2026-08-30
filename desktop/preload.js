// Pont minimal entre l'UI web et Electron.
// L'UI reste fonctionnelle sans lui : elle doit toujours tester `window.avt`
// avant de s'en servir (dans un navigateur, l'objet n'existe pas).
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('avt', {
  isDesktop: true,
  version: () => ipcRenderer.invoke('avt:version'),
  // Ouvre les dossiers dans l'explorateur Windows (aucun accès disque exposé à l'UI).
  openLibrary: () => ipcRenderer.invoke('avt:open-library'),
  openDataFolder: () => ipcRenderer.invoke('avt:open-data-dir'),
  openLogs: () => ipcRenderer.invoke('avt:open-logs'),
  engineStatus: () => ipcRenderer.invoke('avt:engine-status'),
  // Barre de progression sur l'icône de la barre des tâches : ratio entre 0 et 1,
  // -1 pour l'effacer. Sans effet dans un navigateur (window.avt n'existe pas).
  setTaskbarProgress: (ratio) => ipcRenderer.invoke('avt:taskbar-progress', ratio),
});
