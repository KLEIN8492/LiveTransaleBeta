#!/bin/bash
# Builds the Windows installer on this Mac (NSIS cross-compiles):  dist/LiveTranslate Setup <date>.exe
#   brew install makensis        (once)
#   bash winapp/make_win.sh
# The installer carries the code + uv.exe + a small launcher; Python, packages and models are downloaded by the
# setup page on the target PC (GPU build when it has an NVIDIA card), exactly like the Mac app.
set -e
export LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8  # makensis reads the scripts as UTF-8 only under a UTF-8 locale
cd "$(dirname "$0")/.."
VER=$(date +%Y.%m.%d); VERNUM="$(date +%Y).$(date +%-m).$(date +%-d).0"
UV_VER=$(uv --version | awk '{print $2}')
command -v makensis >/dev/null || { echo "makensis not found: brew install makensis"; exit 1; }
W=winapp; STAGE=$W/stage; CACHE=$W/.cache
rm -rf "$STAGE"; mkdir -p "$STAGE/app" "$CACHE" dist

# uv for Windows (same version as this Mac's), cached
if [ ! -f "$CACHE/uv-$UV_VER.exe" ]; then
  curl -fsSL -o "$CACHE/uv.zip" "https://github.com/astral-sh/uv/releases/download/$UV_VER/uv-x86_64-pc-windows-msvc.zip"
  unzip -o -q "$CACHE/uv.zip" uv.exe -d "$CACHE" && mv "$CACHE/uv.exe" "$CACHE/uv-$UV_VER.exe" && rm "$CACHE/uv.zip"
fi
cp "$CACHE/uv-$UV_VER.exe" "$STAGE/uv.exe"

cp server.py tray.py tts.py ndi_lib.py ndi_cdp.py argos_lite.py models.py requirements.txt macapp/installer.py macapp/installer.html "$STAGE/app/"
cp -R static "$STAGE/app/"
find "$STAGE" -name .DS_Store -delete

makensis -INPUTCHARSET UTF8 -V2 -DOUT="stage/LiveTranslate.exe" -DVER="$VER" -DVERNUM="$VERNUM" $W/launcher.nsi
OUT="LiveTranslate Setup $VER.exe"
makensis -INPUTCHARSET UTF8 -V2 -DOUT="../dist/$OUT" -DVER="$VER" -DVERNUM="$VERNUM" $W/installer.nsi
rm -rf "$STAGE"
echo "built dist/$OUT ($(du -h "dist/$OUT" | cut -f1))"
