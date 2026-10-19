#!/bin/bash
# Builds dist/LiveTranslate-linux-x86_64-<date>.tar.gz on this Mac. Python, packages and models are downloaded by the
# setup page on the Linux machine (CUDA build when it has an NVIDIA card), like the Mac and Windows versions.
set -e
cd "$(dirname "$0")/.."
VER=$(date +%Y.%m.%d); UV_VER=$(uv --version | awk '{print $2}')
CACHE=linux/.cache; T=$(mktemp -d); P="$T/LiveTranslate"; mkdir -p "$CACHE" "$P/app" dist
if [ ! -f "$CACHE/uv-$UV_VER" ]; then
  curl -fsSL "https://github.com/astral-sh/uv/releases/download/$UV_VER/uv-x86_64-unknown-linux-gnu.tar.gz" | tar xz -C "$CACHE"
  mv "$CACHE/uv-x86_64-unknown-linux-gnu/uv" "$CACHE/uv-$UV_VER" && rm -rf "$CACHE/uv-x86_64-unknown-linux-gnu"
fi
cp "$CACHE/uv-$UV_VER" "$P/uv"; chmod +x "$P/uv"
cp server.py tray.py tts.py ndi_lib.py ndi_cdp.py argos_lite.py models.py requirements.txt macapp/installer.py macapp/installer.html "$P/app/"
cp -R static "$P/app/"; cp linux/livetranslate linux/install.sh "$P/"; chmod +x "$P/livetranslate" "$P/install.sh"
find "$P" -name .DS_Store -delete
OUT="dist/LiveTranslate-linux-x86_64-$VER.tar.gz"
COPYFILE_DISABLE=1 tar czf "$OUT" -C "$T" LiveTranslate
rm -rf "$T"
echo "built $OUT ($(du -h "$OUT" | cut -f1))"
