#!/usr/bin/env bash
# Telecharge ffmpeg + ffprobe (builds statiques ffbinaries.com) et les place dans
# desktop/resources/bin, embarques ensuite dans le paquet macOS.
#
# ffbinaries ne publie que du x86_64 pour macOS (macos-64) : aucune source ne
# fournit a la fois ffmpeg ET ffprobe en arm64 statique. Sur Apple Silicon ces
# deux binaires passent donc par Rosetta 2. L'application elle-meme reste native.
set -euo pipefail

DESKTOP="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="$DESKTOP/resources/bin"
PLATFORM="${AVT_FFMPEG_PLATFORM:-macos-64}"
VERSION="${AVT_FFMPEG_VERSION:-6.1}"
BASE="https://github.com/ffbinaries/ffbinaries-prebuilt/releases/download/v${VERSION}"

if [ -x "$BIN_DIR/ffmpeg" ] && [ -x "$BIN_DIR/ffprobe" ]; then
  echo ">> ffmpeg deja present dans resources/bin - rien a faire."
  exit 0
fi

mkdir -p "$BIN_DIR"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/avt-ffmpeg-XXXXXXXX")"
trap 'rm -rf "$TMP"' EXIT

for tool in ffmpeg ffprobe; do
  url="$BASE/${tool}-${VERSION}-${PLATFORM}.zip"
  echo ">> Telechargement $url ..."
  curl -fsSL --retry 3 -o "$TMP/$tool.zip" "$url"
  unzip -q -o "$TMP/$tool.zip" -d "$TMP"
  # L'archive peut contenir le binaire a la racine ou dans un sous-dossier.
  found="$(find "$TMP" -type f -name "$tool" -perm -u+x -print -quit)"
  [ -n "$found" ] || found="$(find "$TMP" -type f -name "$tool" -print -quit)"
  if [ -z "$found" ]; then
    echo "$tool introuvable dans l'archive" >&2
    exit 1
  fi
  cp "$found" "$BIN_DIR/$tool"
  chmod +x "$BIN_DIR/$tool"
done

"$BIN_DIR/ffmpeg" -version | head -1
"$BIN_DIR/ffprobe" -version | head -1

echo ">> OK : resources/bin/ffmpeg + ffprobe"
