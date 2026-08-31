#!/usr/bin/env bash
# Construit le backend en binaire autonome (PyInstaller, mode onedir), macOS/Linux.
# Équivalent de build-backend.ps1, qui reste la version Windows de référence.
set -euo pipefail

DESKTOP="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DESKTOP"

if [ ! -d .venv ]; then
  echo ">> Creation du venv de build..."
  python3 -m venv .venv
fi

PY="$DESKTOP/.venv/bin/python"

echo ">> Installation des dependances..."
"$PY" -m pip install --upgrade pip
"$PY" -m pip install -r "$DESKTOP/../requirements.txt"
"$PY" -m pip install pyinstaller

echo ">> Build PyInstaller..."
# --workpath : sinon PyInstaller ecrit dans desktop/build/, ou vit l'icone de
# l'installeur (dossier suivi par git via une exception du .gitignore).
"$PY" -m PyInstaller backend.spec --noconfirm --distpath dist-backend --workpath build-pyinstaller

# Verification du bundle : on lance le binaire en mode selftest (sqlite3 + WAL,
# backend.library, app FastAPI, faster_whisper, yt_dlp). Il est construit en mode
# windowed, sa sortie va donc dans <AVT_DATA_DIR>/backend.log -- on isole les
# donnees de test dans un dossier temporaire pour ne pas toucher a celles de
# l'utilisateur.
echo ">> Verification du bundle (selftest)..."
TEST_DATA="$(mktemp -d "${TMPDIR:-/tmp}/avt-selftest-XXXXXXXX")"
BIN="$DESKTOP/dist-backend/AVT-Backend/AVT-Backend"

set +e
AVT_SELFTEST=1 AVT_DATA_DIR="$TEST_DATA" "$BIN"
CODE=$?
set -e

TEST_LOG="$TEST_DATA/backend.log"
if [ "$CODE" -ne 0 ]; then
  [ -f "$TEST_LOG" ] && tail -40 "$TEST_LOG"
  echo "Selftest du backend echoue ($CODE). Detail : $TEST_LOG" >&2
  exit 1
fi
[ -f "$TEST_LOG" ] && tail -10 "$TEST_LOG"
rm -rf "$TEST_DATA"

echo ">> OK : dist-backend/AVT-Backend/AVT-Backend"
