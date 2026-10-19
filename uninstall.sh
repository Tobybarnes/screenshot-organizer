#!/bin/bash
set -euo pipefail

ROOT="${SCREENSHOT_ORGANIZER_ROOT:-$HOME/Documents/Screenshots}"
INBOX="$ROOT/_Inbox"
INSTALL_DIR="$HOME/.local/share/screenshot-organizer"
SCRIPT_NAME="Screenshot Organizer.scpt"
SCRIPT_DEST="$HOME/Library/Scripts/Folder Action Scripts/$SCRIPT_NAME"
PREVIOUS_LOCATION_FILE="$INSTALL_DIR/previous-screenshot-location"

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

# Put the screenshot save location back, so new screenshots don't pile up in
# an inbox that nothing is watching any more.
RESTORE_TO="$HOME/Desktop"
if [ -f "$PREVIOUS_LOCATION_FILE" ]; then
  RESTORE_TO="$(cat "$PREVIOUS_LOCATION_FILE")"
fi
if [ "$(defaults read com.apple.screencapture location 2>/dev/null || true)" = "$INBOX" ]; then
  mkdir -p "$RESTORE_TO"
  defaults write com.apple.screencapture location "$RESTORE_TO"
  killall SystemUIServer >/dev/null 2>&1 || true
  echo "Screenshot save location restored to: $RESTORE_TO"
fi

rm -f "$SCRIPT_DEST"
rm -rf "$INSTALL_DIR"

echo "Screenshot Organizer uninstalled. Organised screenshots were left in place."
API_KEY_FILE="$HOME/.config/screenshot-organizer/anthropic_api_key"
if [ -f "$API_KEY_FILE" ]; then
  echo "Your API key is still saved at $API_KEY_FILE (delete it with: rm \"$API_KEY_FILE\")"
fi
if [ -n "$(ls -A "$INBOX" 2>/dev/null)" ]; then
  echo "Note: $INBOX still has unprocessed files."
fi
