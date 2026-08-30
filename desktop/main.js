// AI Video Transcriber — coquille Electron.
// Démarre le backend FastAPI (exe PyInstaller en prod, python -m uvicorn en dev),
// affiche un écran de démarrage, attend /api/health puis charge l'UI web
// existante (static/ servie par FastAPI).
const { app, BrowserWindow, shell, dialog, ipcMain } = require('electron');
const { spawn } = require('child_process');
const crypto = require('crypto');
const path = require('path');
const http = require('http');
const net = require('net');
const fs = require('fs');

// Jeton de session : l'API écoute sur la boucle locale, donc n'importe quelle page
// web ouverte dans le navigateur de l'utilisateur pourrait l'appeler. Le backend
// exige ce jeton dès qu'AVT_TOKEN est défini et l'injecte lui-même dans l'index.html
// qu'il sert ; une page tierce ne l'a pas. Régénéré à chaque lancement, jamais
// écrit sur disque.
const SESSION_TOKEN = crypto.randomBytes(32).toString('hex');

// Port : 8765 par défaut, mais on bascule sur un port libre s'il est occupé
// (sinon uvicorn meurt au démarrage et l'app ne s'ouvre jamais).
const PREFERRED_PORT = Number(process.env.AVT_PORT || 8765);
let port = PREFERRED_PORT;
let baseUrl = `http://127.0.0.1:${port}`;

// Données utilisateur : même dossier que celui calculé par backend_entry.py.
// On le passe explicitement au backend pour que les deux côtés soient d'accord.
const DATA_DIR =
  process.env.AVT_DATA_DIR ||
  path.join(process.env.LOCALAPPDATA || app.getPath('home'), 'AI-Video-Transcriber');

let backendProc = null;
let mainWindow = null;
let splashWindow = null;
let quitting = false;

/* ── Port ─────────────────────────────────────────────── */

function portIsFree(candidate) {
  return new Promise((resolve) => {
    const srv = net.createServer();
    srv.once('error', () => resolve(false));
    srv.once('listening', () => srv.close(() => resolve(true)));
    srv.listen(candidate, '127.0.0.1');
  });
}

function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.once('error', reject);
    srv.listen(0, '127.0.0.1', () => {
      const chosen = srv.address().port;
      srv.close(() => resolve(chosen));
    });
  });
}

async function pickPort() {
  if (await portIsFree(PREFERRED_PORT)) return PREFERRED_PORT;
  return freePort();
}

/* ── Backend ──────────────────────────────────────────── */

function backendCommand() {
  if (app.isPackaged) {
    const exe = path.join(process.resourcesPath, 'backend', 'AVT-Backend', 'AVT-Backend.exe');
    return { cmd: exe, args: [], cwd: path.dirname(exe) };
  }
  // Mode dev : backend du dépôt, mêmes sources que la version web.
  const repoRoot = path.resolve(__dirname, '..');
  return {
    cmd: process.platform === 'win32' ? 'python' : 'python3',
    args: ['-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', String(port)],
    cwd: path.join(repoRoot, 'backend'),
  };
}

function startBackend() {
  const { cmd, args, cwd } = backendCommand();
  const env = {
    ...process.env,
    AVT_PORT: String(port),
    PORT: String(port),
    AVT_DATA_DIR: DATA_DIR,
    AVT_TOKEN: SESSION_TOKEN,
  };

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
        `Le moteur de transcription s'est arrêté (code ${code}).\n\n` +
          `Journal : ${path.join(DATA_DIR, 'backend.log')}`
      );
      app.quit();
    }
  });
  backendProc.on('error', (err) => {
    dialog.showErrorBox('AI Video Transcriber', `Impossible de démarrer le moteur : ${err.message}`);
    app.quit();
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

function waitForServer(timeoutMs = 90000) {
  const started = Date.now();
  return new Promise((resolve, reject) => {
    const tick = () => {
      const req = http.get(`${baseUrl}/api/health`, (res) => {
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

/* ── État du moteur (préchargement du modèle Whisper) ──── */

function engineStatus() {
  try {
    const raw = fs.readFileSync(path.join(DATA_DIR, 'engine-status.json'), 'utf-8');
    return JSON.parse(raw);
  } catch (_) {
    return { state: 'starting' };
  }
}

/* ── Fenêtres ─────────────────────────────────────────── */

function createSplash() {
  splashWindow = new BrowserWindow({
    // Assez haut pour la charte de l'app : bloc marque + égaliseur + message
    // sur deux lignes + bouton + mention. À 300 px le contenu était rogné.
    width: 520,
    height: 400,
    frame: false,
    resizable: false,
    show: true,
    backgroundColor: '#0f1115',
    icon: path.join(__dirname, 'build', 'icon.ico'),
    webPreferences: {
      preload: path.join(__dirname, 'preload-splash.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  splashWindow.loadFile(path.join(__dirname, 'splash.html'));
  splashWindow.on('closed', () => {
    splashWindow = null;
  });
}

function closeSplash() {
  if (splashWindow) {
    splashWindow.close();
    splashWindow = null;
  }
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 900,
    minHeight: 600,
    autoHideMenuBar: true,
    backgroundColor: '#0f1115',
    icon: path.join(__dirname, 'build', 'icon.ico'),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  // Liens externes -> navigateur système.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith(baseUrl)) return { action: 'allow' };
    shell.openExternal(url);
    return { action: 'deny' };
  });

  mainWindow.loadURL(baseUrl);
  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

function showMainWindow() {
  if (mainWindow) {
    mainWindow.focus();
    return;
  }
  createWindow();
  mainWindow.once('ready-to-show', closeSplash);
  // Filet de sécurité si l'évènement n'arrive pas (page servie très vite).
  setTimeout(closeSplash, 4000);
}

/* ── Pont IPC exposé à l'UI (preload.js / preload-splash.js) ── */

function registerIpc() {
  ipcMain.handle('avt:engine-status', () => engineStatus());
  ipcMain.handle('avt:continue', () => {
    showMainWindow();
    return true;
  });
  ipcMain.handle('avt:open-library', () => shell.openPath(path.join(DATA_DIR, 'library')));
  ipcMain.handle('avt:open-data-dir', () => shell.openPath(DATA_DIR));
  ipcMain.handle('avt:open-logs', () => shell.openPath(path.join(DATA_DIR, 'backend.log')));
  ipcMain.handle('avt:version', () => app.getVersion());
}

/* ── Cycle de vie ─────────────────────────────────────── */

// Une seule instance : deux backends sur le même dossier de données se
// marcheraient dessus (index SQLite, temp/).
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    } else if (splashWindow) {
      splashWindow.focus();
    }
  });

  app.whenReady().then(async () => {
    registerIpc();
    createSplash();

    try {
      fs.mkdirSync(DATA_DIR, { recursive: true });
    } catch (_) {
      /* le backend retentera et signalera l'erreur */
    }

    port = await pickPort();
    baseUrl = `http://127.0.0.1:${port}`;

    startBackend();
    try {
      await waitForServer();
    } catch (err) {
      closeSplash();
      dialog.showErrorBox('AI Video Transcriber', err.message);
      app.quit();
      return;
    }

    // Le serveur répond. Si le modèle est déjà en cache on entre directement ;
    // sinon l'écran de démarrage suit le téléchargement (avec bouton « Continuer »).
    const status = engineStatus();
    if (status.state === 'ready' || status.state === 'error') {
      showMainWindow();
    } else if (splashWindow) {
      splashWindow.webContents.send('avt:server-ready');
    } else {
      showMainWindow();
    }

    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) showMainWindow();
    });
  });
}

app.on('before-quit', () => {
  quitting = true;
  stopBackend();
});

app.on('window-all-closed', () => {
  quitting = true;
  stopBackend();
  app.quit();
});
