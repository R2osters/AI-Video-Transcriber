# -*- mode: python ; coding: utf-8 -*-
# Spec PyInstaller du backend AI Video Transcriber (mode onedir).
# Build : desktop/scripts/build-backend.ps1  →  desktop/dist-backend/AVT-Backend/
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).parent          # racine du dépôt
BACKEND = ROOT / "backend"

datas = [(str(ROOT / "static"), "static")]
binaries = []
hiddenimports = []

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
