#!/usr/bin/env bash
# Install (or remove with --uninstall) a macOS LaunchAgent that runs run_nightly.py every day at 06:00.
#   ./scripts/install_launchd.sh [HH:MM]      e.g. ./scripts/install_launchd.sh 06:00
#   ./scripts/install_launchd.sh --uninstall
# If the Mac is asleep at that time launchd runs the job when it wakes. To wake it automatically, run once:
#   sudo pmset repeat wakeorpoweron MTWRFSU 05:55:00
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.joobscript.nightly"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
UID_="$(id -u)"

if [ "${1:-}" = "--uninstall" ]; then
  launchctl bootout "gui/$UID_/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"; echo "Removed $LABEL"; exit 0
fi

TIME="${1:-06:00}"; HOUR=$((10#${TIME%%:*})); MIN=$((10#${TIME##*:}))
PY="$ROOT/.venv/bin/python"; [ -x "$PY" ] || PY="$(command -v python3)"
mkdir -p "$ROOT/logs" "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$ROOT/run_nightly.py</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MIN</integer></dict>
  <key>StandardOutPath</key><string>$ROOT/logs/launchd.log</string>
  <key>StandardErrorPath</key><string>$ROOT/logs/launchd.log</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/Library/TeX/texbin:/usr/bin:/bin:$HOME/.local/bin</string>
  </dict>
</dict></plist>
PL
launchctl bootout "gui/$UID_/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID_" "$PLIST"
echo "Installed $LABEL: runs daily at $(printf '%02d:%02d' $HOUR $MIN) using $PY"
echo "Logs: $ROOT/logs/nightly-YYYYMMDD.log   Summary: $ROOT/Q-Rattor/today.md"
echo "Optional (wakes the Mac): sudo pmset repeat wakeorpoweron MTWRFSU 05:55:00"
