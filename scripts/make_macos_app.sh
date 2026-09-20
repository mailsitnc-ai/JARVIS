#!/bin/bash
# Build a native macOS app bundle (JARVIS.app) that runs the existing JARVIS daemon as a real app, so
# macOS grants it FULL, PERSISTENT hardware access (camera, microphone, folders) attributed to "JARVIS"
# instead of a faceless detached python process. No rewrite - it wraps the same Python engine.
#
# Uses `osacompile` to create a properly-structured, launchable app (a shell-script main executable is
# rejected by recent macOS with LaunchServices error -10669). The applet stays alive running the daemon
# as its child, so TCC attributes camera/mic/folder access to JARVIS.app.
#
# Usage:  bash scripts/make_macos_app.sh            (creates ~/Desktop/JARVIS.app)
set -e

if [ "$(uname)" != "Darwin" ]; then echo "Run this on the Mac."; exit 1; fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$(command -v python3.12 || command -v python3)"
APP="${1:-$HOME/Desktop/JARVIS.app}"
[ -n "$PY" ] || { echo "No python3.12/python3 found."; exit 1; }
echo "Repo: $ROOT"; echo "Python: $PY"; echo "App: $APP"

rm -rf "$APP"

# --- 1) a real launchable app via osacompile (Apple-signed applet runner as the executable) ----------
SCPT="$(mktemp -t jarvis-XXXX).applescript"
cat > "$SCPT" <<APPLESCRIPT
on run
	with timeout of 999999 seconds
		set logFile to (POSIX path of (path to library folder from user domain)) & "Logs/JARVIS.log"
		set cmd to "cd " & quoted form of "$ROOT" & " && " & quoted form of "$PY" & " jarvis.py stop >/dev/null 2>&1; sleep 1; exec " & quoted form of "$PY" & " jarvis.py daemon >> " & quoted form of logFile & " 2>&1"
		do shell script cmd
	end timeout
end run
APPLESCRIPT
osacompile -o "$APP" "$SCPT"
rm -f "$SCPT"

# --- 2) patch Info.plist: identity, background-agent, and the privacy usage strings -------------------
P="$APP/Contents/Info.plist"
set_key() { /usr/libexec/PlistBuddy -c "Add :$1 $2 $3" "$P" 2>/dev/null || /usr/libexec/PlistBuddy -c "Set :$1 $3" "$P"; }
set_key CFBundleIdentifier string com.jarvis.assistant
set_key CFBundleName string JARVIS
set_key CFBundleDisplayName string JARVIS
set_key LSUIElement bool true
/usr/libexec/PlistBuddy -c "Add :NSCameraUsageDescription string JARVIS uses the camera when you ask for a photo or to identify what you are holding." "$P" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :NSMicrophoneUsageDescription string JARVIS uses the microphone for voice features you enable." "$P" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :NSDesktopFolderUsageDescription string JARVIS reads and writes files on your Desktop when you ask it to." "$P" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :NSDocumentsFolderUsageDescription string JARVIS reads and writes files in your Documents when you ask it to." "$P" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :NSDownloadsFolderUsageDescription string JARVIS reads and writes files in your Downloads when you ask it to." "$P" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :NSAppleEventsUsageDescription string JARVIS controls apps like your browser and Terminal to carry out tasks you ask for." "$P" 2>/dev/null || true

# --- 3) re-sign ad-hoc so the edited bundle stays valid and permissions persist -----------------------
codesign --force --sign - "$APP" >/dev/null 2>&1 && echo "Signed (ad-hoc)." || echo "codesign skipped."

echo
echo "Built $APP"
echo "Next:"
echo "  1. Quit the terminal daemon:  $PY $ROOT/jarvis.py stop"
echo "  2. Launch it:                 open \"$APP\""
echo "  3. Grant JARVIS (prompts, or System Settings > Privacy & Security): Camera, Microphone,"
echo "     Accessibility (hotkey + docking), Screen Recording."
echo "  4. Summon (Ctrl+Shift+J) and try 'take a photo' - macOS prompts for JARVIS, then remembers it."
echo "  Logs: ~/Library/Logs/JARVIS.log"
