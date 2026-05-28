#!/bin/bash
set -euo pipefail

ROOT="${SCREENSHOT_ORGANIZER_ROOT:-$HOME/Documents/Screenshots}"
INBOX="$ROOT/_Inbox"
INSTALL_DIR="$HOME/.local/share/screenshot-organizer"
SCRIPT_NAME="Screenshot Organizer.scpt"
SCRIPT_DEST="$HOME/Library/Scripts/Folder Action Scripts/$SCRIPT_NAME"

osascript <<APPLESCRIPT || true
tell application "System Events"
	set inboxPath to "$INBOX"
	repeat with candidate in folder actions
		if (path of candidate) is inboxPath then
			delete candidate
			exit repeat
		end if
	end repeat
end tell
APPLESCRIPT

rm -f "$SCRIPT_DEST"
rm -rf "$INSTALL_DIR"

echo "Screenshot Organizer uninstalled."
echo "Screenshot save location was not changed. Update it manually in Screenshot.app if needed."
