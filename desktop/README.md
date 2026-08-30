# AI Video Transcriber — Version bureau (Windows)

Application installable construite au-dessus de la version web **sans la modifier** :
Electron sert de coquille, démarre le backend FastAPI packagé en exe (PyInstaller)
sur `127.0.0.1`, puis affiche la même UI (`static/`) que la version web.

```
┌───────────────────────────────────┐
│  Electron (main.js)               │
│   ├─ choisit un port libre        │  ← 8765, ou un port au hasard s'il est pris
│   ├─ écran de démarrage           │  ← splash.html (état du moteur)
│   ├─ spawn AVT-Backend.exe        │  ← backend/ + static/ packagés (PyInstaller)
│   ├─ attend /api/health           │
│   └─ BrowserWindow → UI web       │
└───────────────────────────────────┘
```

## Architecture

| Élément | Web | Bureau |
|---|---|---|
| Backend | `python start.py` (port 8000) | `AVT-Backend.exe` spawné par Electron (port 8765) |
| UI | `static/` servie par FastAPI | idem, embarquée dans l'exe |
| ffmpeg | dans le PATH / Docker | embarqué (`resources/bin`) |
| Données (temp, bibliothèque, modèles) | `<repo>/temp` | `%LOCALAPPDATA%\AI-Video-Transcriber` |

Contenu du dossier de données :

| Chemin | Rôle |
|---|---|
| `library/` | bibliothèque de transcriptions (une entrée par dossier + `index.db`) |
| `temp/` | fichiers de travail, jetables |
| `models/` | cache Hugging Face des modèles Whisper (`HF_HOME`) |
| `backend.log` | journal du backend (l'exe est fenêtré : rien ne va dans une console) |
| `engine-status.json` | état du préchargement du modèle, lu par l'écran de démarrage |

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

## Premier lancement

Au tout premier démarrage, le modèle Whisper `base` (~150 Mo) n'est pas encore en cache.
`backend_entry.py` le télécharge dans un thread de fond pendant que le serveur répond déjà,
et publie son état dans `engine-status.json` (`starting` → `downloading` → `ready` / `error`).
L'écran de démarrage suit cet état et propose **Continuer sans attendre** : le téléchargement
se poursuit en arrière-plan. Réglage : `AVT_PRELOAD_MODEL` (vide = pas de préchargement).

## Pont Electron exposé à l'UI

`preload.js` expose `window.avt` (absent dans un navigateur — toujours tester avant usage) :

| Appel | Effet |
|---|---|
| `avt.openLibrary()` | ouvre `…\AI-Video-Transcriber\library` dans l'explorateur |
| `avt.openDataFolder()` | ouvre le dossier de données |
| `avt.openLogs()` | ouvre `backend.log` |
| `avt.engineStatus()` | état du préchargement du modèle |
| `avt.version()` | version de l'application |

Aucun accès disque brut n'est exposé : uniquement `shell.openPath` sur des chemins calculés
côté processus principal.

## Désinstallation

Le désinstalleur **demande** s'il doit supprimer `%LOCALAPPDATA%\AI-Video-Transcriber`
(`build/installer.nsh`). La réponse par défaut est **Non** : la bibliothèque conserve les
audios d'origine et peut peser plusieurs Go, on ne l'efface jamais en silence. Lors d'une
mise à jour (`isUpdated`), aucune question n'est posée et rien n'est supprimé.

## Notes

- L'exe backend tourne fenêtre cachée ; Electron le tue à la fermeture.
- Une seule instance à la fois (`requestSingleInstanceLock`) : deux backends sur le même
  dossier de données se marcheraient dessus (index SQLite, `temp/`).
- `build:backend` termine par un **selftest** : l'exe est lancé avec `AVT_SELFTEST=1` et
  vérifie `sqlite3` (+ WAL), `backend.library`, `main:app`, `faster_whisper` et `yt_dlp`.
  Le build échoue si l'un manque — pas de bundle cassé livré en silence.
- Clé OpenAI : mêmes réglages que le web (panneau UI) ; le mode sans clé
  (Whisper local + résumé extractif + chat local) fonctionne hors ligne après
  téléchargement du modèle Whisper (cache dans `%LOCALAPPDATA%\AI-Video-Transcriber\models`).
- Diarization (pyannote/torch) non embarquée — trop lourde pour l'installeur.
- Port : `AVT_PORT` (défaut 8765, évite le 8000 de la version web). S'il est occupé,
  Electron bascule automatiquement sur un port libre — l'app démarre quand même.
