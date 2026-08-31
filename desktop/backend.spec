# -*- mode: python ; coding: utf-8 -*-
# Spec PyInstaller du backend AI Video Transcriber (mode onedir).
# Build : desktop/scripts/build-backend.ps1  →  desktop/dist-backend/AVT-Backend/
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).parent          # racine du dépôt
BACKEND = ROOT / "backend"

datas = [(str(ROOT / "static"), "static")]
binaries = []
# sqlite3 : la bibliothèque de transcriptions (backend/library.py) s'appuie sur
# l'extension native _sqlite3. On la déclare explicitement au lieu de compter sur
# la détection automatique ; le build la vérifie ensuite (voir build-backend.ps1,
# étape « Verification du bundle » qui lance l'exe avec AVT_SELFTEST=1).
hiddenimports = ["sqlite3", "_sqlite3"]

# Paquets avec binaires natifs / données chargées dynamiquement
for pkg in ("faster_whisper", "ctranslate2", "av"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# Chargements dynamiques (extracteurs yt-dlp, protocoles uvicorn/websockets)
hiddenimports += collect_submodules("yt_dlp")
hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("websockets")

a = Analysis(
    ["backend_entry.py"],
    pathex=[str(BACKEND)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "mcp"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="AVT-Backend",
    debug=False,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="AVT-Backend",
)
