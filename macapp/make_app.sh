#!/bin/bash
# Builds dist/LiveTranslate.app (+ .dmg). Apple Silicon only (MLX).
#   bash macapp/make_app.sh            app + dmg
#   bash macapp/make_app.sh --no-dmg   app only
# The bundle carries the code + a uv binary; Python 3.11, packages and models are installed on first
# launch into ~/Library/Application Support/LiveTranslate (needs internet once).
set -e
cd "$(dirname "$0")/.."
ROOT=$PWD; APP=dist/LiveTranslate.app; VER=$(date +%Y.%m.%d)
UV=$(command -v uv || echo "$HOME/.local/bin/uv"); [ -x "$UV" ] || { echo "uv not found: brew install uv"; exit 1; }

rm -rf "$APP"; mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/app"
cp -R server.py menubar.py ndi_out.py ndi_lib.py tts.py mlx_srv.py argos_lite.py models.py requirements.txt static macapp/installer.py macapp/installer.html "$APP/Contents/Resources/app/"
cp "$UV" "$APP/Contents/Resources/uv"

# icon: PNG -> iconset -> icns
[ -f macapp/AppIcon.png ] || "$UV" run -q --with pillow python macapp/make_icon.py
IS=$(mktemp -d)/AppIcon.iconset; mkdir -p "$IS"
for s in 16 32 128 256 512; do
  sips -z $s $s macapp/AppIcon.png --out "$IS/icon_${s}x${s}.png" >/dev/null
  sips -z $((s*2)) $((s*2)) macapp/AppIcon.png --out "$IS/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$IS" -o "$APP/Contents/Resources/AppIcon.icns"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>LiveTranslate</string>
  <key>CFBundleDisplayName</key><string>LiveTranslate</string>
  <key>CFBundleIdentifier</key><string>local.tools.livetranslate</string>
  <key>CFBundleVersion</key><string>$VER</string>
  <key>CFBundleShortVersionString</key><string>$VER</string>
  <key>CFBundleExecutable</key><string>LiveTranslate</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSArchitecturePriority</key><array><string>arm64</string></array>
  <key>LSUIElement</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>LiveTranslate listens to the selected audio input to transcribe and translate speech.</string>
  <key>NSLocalNetworkUsageDescription</key><string>Phones, vMix and other operators connect to LiveTranslate over the local network.</string>
</dict></plist>
PLIST

cat > "$APP/Contents/MacOS/LiveTranslate" <<'LAUNCH'
#!/bin/bash
# LiveTranslate launcher. First launch (or --setup): the setup page installs everything into the support folder.
# Afterwards: the menu-bar app, fully offline. Every model and runtime lives in one folder — delete it to uninstall.
RES="$(cd "$(dirname "$0")/../Resources" && pwd)"; BUNDLE="$(cd "$RES/../.." && pwd)"
SUP="${LT_SUPPORT:-$HOME/Library/Application Support/LiveTranslate}"
LOG="$HOME/Library/Logs/LiveTranslate.log"
mkdir -p "$SUP" "$(dirname "$LOG")"
export LT_SUPPORT="$SUP" LT_DATA="$SUP" HF_HOME="$SUP/hf" \
  XDG_DATA_HOME="$SUP/xdg/data" XDG_CACHE_HOME="$SUP/xdg/cache" XDG_CONFIG_HOME="$SUP/xdg/config" \
  UV_PYTHON_INSTALL_DIR="$SUP/python" UV_CACHE_DIR="$SUP/uv-cache" UV_PYTHON_PREFERENCE=only-managed \
  OLLAMA_MODELS="$SUP/ollama/models"
fail() { osascript -e "display alert \"LiveTranslate\" message \"$1\" as critical" >/dev/null 2>&1; exit 1; }
[ "$(uname -m)" = arm64 ] || fail "LiveTranslate needs a Mac with Apple Silicon (M1 or later)."

if [ "$1" = "--setup" ] || [ ! -f "$SUP/installed.json" ]; then
  if pgrep -f "app/installer.py --sup" >/dev/null; then  # already open: just bring the page back
    URL=$(grep -o 'setup page: http[^ ]*' "$LOG" | tail -1 | cut -d' ' -f3); [ -n "$URL" ] && open "$URL"; exit 0
  fi
  echo "===== $(date) setup" >>"$LOG"
  "$RES/uv" python install 3.11 >>"$LOG" 2>&1 || fail "Setup needs an internet connection the first time, to download its components."
  PYB="$("$RES/uv" python find 3.11)"
  NB=""; [ -n "$LT_NO_BROWSER" ] && NB="--no-browser"
  exec "$PYB" "$RES/app/installer.py" --sup "$SUP" --app "$RES/app" --uv "$RES/uv" --bundle "$BUNDLE" $NB >>"$LOG" 2>&1
fi
# already running (its menu-bar icon may be hidden behind the notch): just bring Control up
PORT="${LT_PORT:-$(cat "$SUP/running-port" 2>/dev/null)}"; PORT="${PORT:-8180}"
if curl -s -m 2 "http://127.0.0.1:$PORT/api/state" >/dev/null 2>&1; then open "http://127.0.0.1:$PORT/control"; exit 0; fi
[ -x "$SUP/venv/bin/python" ] || fail "LiveTranslate is not fully installed. Open it again to finish setup."
cd "$RES/app" && exec "$SUP/venv/bin/python" menubar.py >>"$LOG" 2>&1
LAUNCH
chmod +x "$APP/Contents/MacOS/LiveTranslate"

# Signing: ad-hoc by default (recipients right-click → Open the first time). With an Apple Developer ID:
#   DEV_ID_APP="Developer ID Application: Name (TEAMID)" DEV_ID_INSTALLER="Developer ID Installer: Name (TEAMID)" \
#   NOTARY_PROFILE=<notarytool keychain profile> bash macapp/make_app.sh      → signed + notarized, opens with no warning
if [ -n "$DEV_ID_APP" ]; then
  codesign --force --deep --options runtime --timestamp -s "$DEV_ID_APP" "$APP" && echo "signed: $DEV_ID_APP"
else
  codesign --force --deep -s - "$APP" 2>/dev/null && echo "signed (ad-hoc)"
fi
echo "built $APP"

if [ "$1" != "--no-dmg" ]; then
  DMG=dist/LiveTranslate-$VER.dmg; STAGE=$(mktemp -d)
  cp -R "$APP" "$STAGE/"; ln -s /Applications "$STAGE/Applications"
  rm -f "$DMG"; hdiutil create -quiet -volname LiveTranslate -srcfolder "$STAGE" -ov -format UDZO "$DMG"
  echo "built $DMG ($(du -h "$DMG" | cut -f1))"

  # Installer package: the file to hand out. Installs to /Applications, then opens LiveTranslate for setup.
  PKG="dist/LiveTranslate Installer $VER.pkg"; ROOT=$(mktemp -d); WORK=$(mktemp -d)
  mkdir -p "$ROOT/Applications"; cp -R "$APP" "$ROOT/Applications/"
  pkgbuild --quiet --root "$ROOT" --install-location / --identifier local.tools.livetranslate --version "$VER" \
    --scripts macapp/pkg/scripts "$WORK/app.pkg"
  cat > "$WORK/distribution.xml" <<DIST
<?xml version="1.0" encoding="utf-8"?>
<installer-gui-script minSpecVersion="2">
  <title>LiveTranslate</title>
  <welcome file="welcome.html" mime-type="text/html"/>
  <conclusion file="conclusion.html" mime-type="text/html"/>
  <options customize="never" require-scripts="false" hostArchitectures="arm64"/>
  <domains enable_localSystem="true"/>
  <volume-check><allowed-os-versions><os-version min="13.0"/></allowed-os-versions></volume-check>
  <choices-outline><line choice="app"/></choices-outline>
  <choice id="app" visible="false"><pkg-ref id="local.tools.livetranslate"/></choice>
  <pkg-ref id="local.tools.livetranslate" version="$VER" onConclusion="none">app.pkg</pkg-ref>
</installer-gui-script>
DIST
  rm -f "$PKG"
  productbuild --quiet --distribution "$WORK/distribution.xml" --resources macapp/pkg --package-path "$WORK" "$WORK/unsigned.pkg"
  if [ -n "$DEV_ID_INSTALLER" ]; then productsign --sign "$DEV_ID_INSTALLER" "$WORK/unsigned.pkg" "$PKG"; else mv "$WORK/unsigned.pkg" "$PKG"; fi
  if [ -n "$NOTARY_PROFILE" ] && [ -n "$DEV_ID_INSTALLER" ]; then
    xcrun notarytool submit "$PKG" --keychain-profile "$NOTARY_PROFILE" --wait && xcrun stapler staple "$PKG"
  fi
  echo "built $PKG ($(du -h "$PKG" | cut -f1))"
fi
