# AI Video Transcriber — Version bureau (Windows)

Application installable construite au-dessus de la version web **sans la modifier** :
Electron sert de coquille, démarre le backend FastAPI packagé en exe (PyInstaller)
sur `127.0.0.1`, puis affiche la même UI (`static/`) que la version web.

```
┌─────────────────────────────┐
│  Electron (main.js)         │
│   ├─ spawn AVT-Backend.exe  │  ← backend/ + static/ packagés (PyInstaller)
│   ├─ attend /api/health     │
│   └─ BrowserWindow → UI web │
└─────────────────────────────┘
```

## Architecture

| Élément | Web | Bureau |
|---|---|---|
| Backend | `python start.py` (port 8000) | `AVT-Backend.exe` spawné par Electron (port 8765) |
| UI | `static/` servie par FastAPI | idem, embarquée dans l'exe |
| ffmpeg | dans le PATH / Docker | embarqué (`resources/bin`) |
| Données (temp, tasks.json, modèles) | `<repo>/temp` | `%LOCALAPPDATA%\AI-Video-Transcriber` |

Le backend lit deux variables d'env (posées par `backend_entry.py` / Electron) :
- `AVT_STATIC_DIR` — dossier `static/` (embarqué dans l'exe en prod)
- `AVT_DATA_DIR` — dossier données utilisateur (défaut : `%LOCALAPPDATA%\AI-Video-Transcriber`)

Sans ces variables, la version web se comporte exactement comme avant.

## Développement

La version web reste le terrain de dev : nouvelle fonctionnalité → coder/tester en web
(`python start.py` ou Docker), puis rebuilder le bureau quand c'est stable.

Tester la coquille Electron sur le backend de dev (Python local, sans packaging) :

```bash
cd desktop && npm install && npm start
```

## Build de l'installeur

Prérequis : Python 3.11+ (avec `venv`), Node 18+, connexion internet.

```bash
cd desktop && npm run dist:full
```

Ce que fait `dist:full` :
1. `build:backend` — venv dédié (`desktop/.venv`), `pip install -r requirements.txt` + PyInstaller,
   build onedir → `dist-backend/AVT-Backend/`
2. `ffmpeg` — télécharge ffmpeg/ffprobe (gyan.dev, essentials) → `resources/bin/`
3. `electron-builder --win` — installeur NSIS → `dist-installer/AI Video Transcriber Setup <version>.exe`

Étapes relançables séparément : `npm run build:backend`, `npm run ffmpeg`, `npm run dist`.

## Notes

- L'exe backend tourne fenêtre cachée ; Electron le tue à la fermeture.
- Clé OpenAI : mêmes réglages que le web (panneau UI) ; le mode sans clé
  (Whisper local + résumé extractif + chat local) fonctionne hors ligne après
  téléchargement du modèle Whisper (cache dans `%LOCALAPPDATA%\AI-Video-Transcriber\models`).
- Diarization (pyannote/torch) non embarquée — trop lourde pour l'installeur.
- Port modifiable : `AVT_PORT` (défaut 8765, évite le 8000 de la version web).
