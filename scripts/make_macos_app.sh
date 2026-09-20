#!/bin/bash
# Build a native macOS app bundle (JARVIS.app) that runs the existing JARVIS daemon as a real app, so
# macOS grants it FULL, PERSISTENT hardware access (camera, microphone, folders) attributed to "JARVIS"
# instead of a faceless detached python process. No rewrite - it wraps the same Python engine.
#
# Usage:  bash scripts/make_macos_app.sh            (creates ~/Desktop/JARVIS.app)
#         bash scripts/make_macos_app.sh /path.app  (custom location)
set -e

if [ "$(uname)" != "Darwin" ]; then
  echo "This builds a macOS app; run it on the Mac."; exit 1
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"                 # the JARVIS repo directory
PY="$(command -v python3.12 || command -v python3)"      # the interpreter to run the daemon with
APP="${1:-$HOME/Desktop/JARVIS.app}"

if [ -z "$PY" ]; then echo "No python3.12/python3 found on PATH."; exit 1; fi
echo "Repo:   $ROOT"
echo "Python: $PY"
echo "App:    $APP"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# --- Info.plist: app identity + the privacy usage strings macOS shows when it asks for access ---------
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>JARVIS</string>
  <key>CFBundleDisplayName</key><string>JARVIS</string>
  <key>CFBundleIdentifier</key><string>com.jarvis.assistant</string>
  <key>CFBundleVersion</key><string>1.0.0</string>
  <key>CFBundleShortVersionString</key><string>1.0.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>JARVIS</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSUIElement</key><true/>
  <key>NSCameraUsageDescription</key><string>JARVIS uses the camera when you ask it to take a photo or identify what you are holding.</string>
  <key>NSMicrophoneUsageDescription</key><string>JARVIS uses the microphone for voice features you enable.</string>
  <key>NSDesktopFolderUsageDescription</key><string>JARVIS reads and writes files on your Desktop when you ask it to.</string>
  <key>NSDocumentsFolderUsageDescription</key><string>JARVIS reads and writes files in your Documents when you ask it to.</string>
  <key>NSDownloadsFolderUsageDescription</key><string>JARVIS reads and writes files in your Downloads when you ask it to.</string>
  <key>NSAppleEventsUsageDescription</key><string>JARVIS controls apps (like your browser and Terminal) to carry out tasks you ask for.</string>
</dict></plist>
PLIST

# --- launcher: this IS the app's process; it runs the daemon as a CHILD (no exec) so the running app
#     keeps the JARVIS identity/Info.plist that TCC attributes hardware access to. -------------------
cat > "$APP/Contents/MacOS/JARVIS" <<LAUNCH
#!/bin/bash
export JARVIS_NATIVE_APP=1
cd "$ROOT" || exit 1
# stop any daemon already running from the terminal so this app owns the one instance
"$PY" "$ROOT/jarvis.py" stop >/dev/null 2>&1 || true
sleep 1
exec_log="\$HOME/Library/Logs/JARVIS.log"
"$PY" "$ROOT/jarvis.py" daemon >>"\$exec_log" 2>&1
LAUNCH
chmod +x "$APP/Contents/MacOS/JARVIS"

# --- ad-hoc code signature so macOS gives the app a stable identity and REMEMBERS the permissions -----
codesign --force --deep --sign - "$APP" >/dev/null 2>&1 && echo "Signed (ad-hoc)." || echo "codesign skipped (not fatal)."

echo
echo "Built $APP"
echo "Next:"
echo "  1. Quit the terminal daemon:   $PY $ROOT/jarvis.py stop"
echo "  2. Launch the app:             open \"$APP\""
echo "  3. When JARVIS asks, or in System Settings > Privacy & Security, grant JARVIS:"
echo "       Camera, Microphone, Accessibility (for the hotkey + window docking), Screen Recording."
echo "  4. Try 'take a photo' - macOS will prompt for JARVIS the first time, then remember it."
echo "  Logs: ~/Library/Logs/JARVIS.log"
