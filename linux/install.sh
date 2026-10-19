#!/bin/bash
# LiveTranslate for Linux (x86_64, e.g. Ubuntu 22.04/24.04). Per-user install, no root needed for the app itself.
#   tar xzf LiveTranslate-linux-*.tar.gz && cd LiveTranslate && ./install.sh
set -e
SRC="$(cd "$(dirname "$0")" && pwd)"; DEST="$HOME/.local/opt/LiveTranslate"
[ "$(uname -m)" = x86_64 ] || { echo "LiveTranslate for Linux needs an x86_64 (Intel/AMD) computer."; exit 1; }
pkill -f "LiveTranslate/app/tray.py" 2>/dev/null || true
mkdir -p "$DEST" "$HOME/.local/bin" "$HOME/.local/share/applications" "$HOME/.local/share/icons"
rm -rf "$DEST/app"; cp -R "$SRC/app" "$SRC/livetranslate" "$SRC/uv" "$DEST/"
ln -sf "$DEST/livetranslate" "$HOME/.local/bin/livetranslate"
cp "$SRC/app/static/icon.png" "$HOME/.local/share/icons/livetranslate.png"
cat > "$HOME/.local/share/applications/livetranslate.desktop" <<D
[Desktop Entry]
Type=Application
Name=LiveTranslate
Comment=Live speech translation and subtitles
Exec=$DEST/livetranslate
Icon=$HOME/.local/share/icons/livetranslate.png
Categories=AudioVideo;Audio;
D

# system libraries the app relies on (only ones that are missing are listed)
need=()
ldconfig -p 2>/dev/null | grep -q libportaudio.so.2 || need+=(libportaudio2)
command -v chromium >/dev/null || command -v chromium-browser >/dev/null || command -v google-chrome >/dev/null || need+=("chromium   (only for NDI output)")
command -v nvidia-smi >/dev/null || echo "Note: no NVIDIA driver found — speech recognition will run on the CPU with a smaller model."
ls /usr/lib/libndi.so* /usr/local/lib/libndi.so* /usr/lib/x86_64-linux-gnu/libndi.so* >/dev/null 2>&1 || \
  echo "Note: NDI output needs the NDI runtime (NDI SDK for Linux from ndi.video) — optional."
if [ ${#need[@]} -gt 0 ]; then
  echo; echo "Please install: ${need[*]}"; echo "  e.g.  sudo apt install libportaudio2 chromium-browser"; echo
fi
echo "Installed. Starting setup (downloads the models once)…"
exec "$DEST/livetranslate" --setup "$@"
