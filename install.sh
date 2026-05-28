#!/bin/bash
set -euo pipefail

ROOT="${SCREENSHOT_ORGANIZER_ROOT:-$HOME/Documents/Screenshots}"
INBOX="$ROOT/_Inbox"
REVIEW="$ROOT/_Needs Review"
INSTALL_DIR="$HOME/.local/share/screenshot-organizer"
SCRIPT_NAME="Screenshot Organizer.scpt"
FOLDER_ACTION_DIR="$HOME/Library/Scripts/Folder Action Scripts"
SCRIPT_DEST="$FOLDER_ACTION_DIR/$SCRIPT_NAME"
LEGACY_PLIST="$HOME/Library/LaunchAgents/com.toby.screenshot-organizer.plist"

if ! CLAUDE_BIN="$(command -v claude 2>/dev/null)"; then
  echo "Claude CLI was not found in PATH."
  echo "Install and authenticate Claude Code, then rerun: claude"
  exit 1
fi

if ! "$CLAUDE_BIN" --version >/dev/null 2>&1; then
  echo "Claude CLI exists at $CLAUDE_BIN but did not run cleanly."
  echo "Open Claude once or run: claude"
  exit 1
fi

mkdir -p "$INBOX" "$REVIEW" "$INSTALL_DIR" "$FOLDER_ACTION_DIR"
cp "$(dirname "$0")/organize_screenshots.py" "$INSTALL_DIR/organize_screenshots.py"
chmod +x "$INSTALL_DIR/organize_screenshots.py"

APPLESCRIPT_SRC="$INSTALL_DIR/screenshot-organizer-folder-action.applescript"
cat > "$APPLESCRIPT_SRC" <<APPLESCRIPT
property workerPath : "$INSTALL_DIR/organize_screenshots.py"
property screenshotsRoot : "$ROOT"
property claudeBin : "$CLAUDE_BIN"

on adding folder items to this_folder after receiving added_items
	repeat with added_item in added_items
		set itemPath to POSIX path of added_item
		do shell script "/usr/bin/python3 " & quoted form of workerPath & " --screenshots-root " & quoted form of screenshotsRoot & " --claude-bin " & quoted form of claudeBin & " " & quoted form of itemPath
	end repeat
end adding folder items to
APPLESCRIPT

osacompile -o "$SCRIPT_DEST" "$APPLESCRIPT_SRC"

osascript <<APPLESCRIPT
tell application "System Events"
	set folder actions enabled to true
	set inboxPath to "$INBOX"
	set actionName to "$SCRIPT_NAME"
	set targetAction to missing value
	repeat with candidate in folder actions
		if (path of candidate) is inboxPath then
			set targetAction to candidate
			exit repeat
		end if
	end repeat
	if targetAction is missing value then
		set targetAction to make new folder action at end of folder actions with properties {path:inboxPath, enabled:true}
	else
		set enabled of targetAction to true
	end if
	tell targetAction
		if not (exists script actionName) then
			make new script at end of scripts with properties {name:actionName}
		end if
		set enabled of script actionName to true
	end tell
end tell
APPLESCRIPT

if [ -f "$LEGACY_PLIST" ]; then
  launchctl bootout "gui/$(id -u)" "$LEGACY_PLIST" >/dev/null 2>&1 || true
  launchctl unload "$LEGACY_PLIST" >/dev/null 2>&1 || true
fi

defaults write com.apple.screencapture location "$INBOX"
killall SystemUIServer >/dev/null 2>&1 || true

echo "Screenshot Organizer installed."
echo "Inbox: $INBOX"
echo "Destination: $ROOT/YYYY-MM/"
echo "Folder Action: $SCRIPT_DEST"
echo "Claude CLI: $CLAUDE_BIN"
echo ""
echo "If Folder Actions did not attach, open Folder Actions Setup and attach:"
echo "  $SCRIPT_DEST"
echo "to:"
echo "  $INBOX"
