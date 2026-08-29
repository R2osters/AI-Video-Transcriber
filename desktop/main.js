// AI Video Transcriber — coquille Electron.
// Démarre le backend FastAPI (exe PyInstaller en prod, python -m uvicorn en dev),
// attend /api/health puis charge l'UI web existante (static/ servie par FastAPI).
const { app, BrowserWindow, shell, dialog } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const http = require('http');
const fs = require('fs');

const PORT = Number(process.env.AVT_PORT || 8765);
const BASE_URL = `http://127.0.0.1:${PORT}`;

let backendProc = null;
let mainWindow = null;
let quitting = false;

function backendCommand() {
  if (app.isPackaged) {
    const exe = path.join(process.resourcesPath, 'backend', 'AVT-Backend', 'AVT-Backend.exe');
    return { cmd: exe, args: [], cwd: path.dirname(exe) };
  }
  // Mode dev : backend du dépôt, mêmes sources que la version web.
  const repoRoot = path.resolve(__dirname, '..');
  return {
    cmd: process.platform === 'win32' ? 'python' : 'python3',
    args: ['-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', String(PORT)],
    cwd: path.join(repoRoot, 'backend'),
  };
}

function startBackend() {
  const { cmd, args, cwd } = backendCommand();
  const env = { ...process.env, AVT_PORT: String(PORT), PORT: String(PORT) };

  if (app.isPackaged) {
    const ffmpegDir = path.join(process.resourcesPath, 'bin');
    if (fs.existsSync(ffmpegDir)) {
      env.AVT_FFMPEG_DIR = ffmpegDir;
      env.PATH = ffmpegDir + path.delimiter + (env.PATH || '');
    }
  }

  backendProc = spawn(cmd, args, { cwd, env, windowsHide: true, stdio: 'ignore' });
  backendProc.on('exit', (code) => {
    backendProc = null;
    if (!quitting && code !== 0) {
      dialog.showErrorBox(
        'AI Video Transcriber',
        `Le moteur de transcription s'est arrêté (code ${code}). Relancez l'application.`
      );
      app.quit();
    }
  });
  backendProc.on('error', (err) => {
    dialog.showErrorBox('AI Video Transcriber', `Impossible de démarrer le moteur : ${err.message}`);
    app.quit();
  });
}

function waitForServer(timeoutMs = 90000) {
  const started = Date.now();
  return new Promise((resolve, reject) => {
    const tick = () => {
      const req = http.get(`${BASE_URL}/api/health`, (res) => {
        res.resume();
        if (res.statusCode === 200) return resolve();
        retry();
      });
      req.on('error', retry);
      req.setTimeout(2000, () => {
        req.destroy();
        retry();
      });
    };
    const retry = () => {
      if (Date.now() - started > timeoutMs) {
        return reject(new Error('Le serveur local ne répond pas.'));
      }
      setTimeout(tick, 500);
    };
    tick();
  });
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 900,
    minHeight: 600,
    autoHideMenuBar: true,
    backgroundColor: '#0f1115',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  // Liens externes -> navigateur système.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith(BASE_URL)) return { action: 'allow' };
    shell.openExternal(url);
    return { action: 'deny' };
  });

  mainWindow.loadURL(BASE_URL);
  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

function stopBackend() {
  if (backendProc) {
    try {
      backendProc.kill();
    } catch (_) {
      /* déjà arrêté */
    }
    backendProc = null;
  }
}

app.whenReady().then(async () => {
  startBackend();
  try {
    await waitForServer();
  } catch (err) {
    dialog.showErrorBox('AI Video Transcriber', err.message);
    app.quit();
    return;
  }
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('before-quit', () => {
  quitting = true;
  stopBackend();
});

app.on('window-all-closed', () => {
  quitting = true;
  stopBackend();
  app.quit();
});
