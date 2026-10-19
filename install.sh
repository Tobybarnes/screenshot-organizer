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
PREVIOUS_LOCATION_FILE="$INSTALL_DIR/previous-screenshot-location"
MODEL="${SCREENSHOT_ORGANIZER_MODEL:-haiku}"
STRIP_ENV="${SCREENSHOT_ORGANIZER_STRIP_ENV:-}"

# --- Find tools -------------------------------------------------------------

if ! PYTHON_BIN="$(command -v python3 2>/dev/null)"; then
  echo "python3 was not found. Install Xcode Command Line Tools: xcode-select --install"
  exit 1
fi

# The Claude CLI is optional (an API key works on its own). Look on PATH and
# in the usual install locations, since the native installer's ~/.local/bin
# is often not on PATH.
CLAUDE_BIN=""
for candidate in "$(command -v claude 2>/dev/null || true)" \
    "$HOME/.local/bin/claude" "$HOME/.claude/local/claude" \
    /opt/homebrew/bin/claude /usr/local/bin/claude; do
  if [ -n "$candidate" ] && [ -x "$candidate" ] && "$candidate" --version >/dev/null 2>&1; then
    CLAUDE_BIN="$candidate"
    break
  fi
done

# Folder Actions run with a bare PATH (/usr/bin:/bin:/usr/sbin:/sbin).
# Build a PATH that includes everything Claude needs, including node if the
# CLI was installed via npm (its shebang is `#!/usr/bin/env node`).
RUN_PATH=""
if [ -n "$CLAUDE_BIN" ]; then
  RUN_PATH="$(dirname "$CLAUDE_BIN"):"
  CLAUDE_REAL="$("$PYTHON_BIN" -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$CLAUDE_BIN")"
  if head -c 64 "$CLAUDE_REAL" 2>/dev/null | grep -q "node"; then
    if NODE_BIN="$(command -v node 2>/dev/null)"; then
      RUN_PATH="$RUN_PATH$(dirname "$NODE_BIN"):"
    fi
  fi
fi
RUN_PATH="${RUN_PATH}/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# --- Anthropic API key (required if there is no Claude CLI) -----------------
# With a key, screenshots are titled through the API directly (billed per call
# to your API account). Without one, the worker uses your Claude CLI login.

API_KEY_DIR="$HOME/.config/screenshot-organizer"
API_KEY_FILE="$API_KEY_DIR/anthropic_api_key"
mkdir -p "$API_KEY_DIR"

if [ -f "$API_KEY_FILE" ]; then
  echo "Using saved API key: $API_KEY_FILE"
elif [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  ( umask 077; printf '%s\n' "$ANTHROPIC_API_KEY" > "$API_KEY_FILE" )
  echo "Saved ANTHROPIC_API_KEY from your environment to $API_KEY_FILE"
elif [ -t 0 ]; then
  echo ""
  if [ -n "$CLAUDE_BIN" ]; then
    echo "Optional: paste an Anthropic API key (console.anthropic.com/settings/keys)"
    echo "to title screenshots via the API. Press Enter to use your Claude CLI login instead."
  else
    echo "No Claude Code CLI found, so an Anthropic API key is needed."
    echo "Create one at https://console.anthropic.com/settings/keys and paste it here."
    echo "(Typing is hidden. Or press Enter to quit and install Claude Code instead.)"
  fi
  read -r -s -p "API key: " input_key
  echo ""
  if [ -n "$input_key" ]; then
    ( umask 077; printf '%s\n' "$input_key" > "$API_KEY_FILE" )
    echo "API key saved to $API_KEY_FILE"
  fi
fi

if [ -z "$CLAUDE_BIN" ] && [ ! -f "$API_KEY_FILE" ]; then
  echo ""
  echo "Nothing to talk to Claude with. Either:"
  echo "  - rerun ./install.sh and paste an Anthropic API key, or"
  echo "  - install Claude Code (https://docs.claude.com/en/docs/claude-code), run 'claude' once to log in, then rerun ./install.sh"
  exit 1
fi

# --- Install worker ---------------------------------------------------------

mkdir -p "$INBOX" "$REVIEW" "$INSTALL_DIR" "$FOLDER_ACTION_DIR"
cp "$(dirname "$0")/organize_screenshots.py" "$INSTALL_DIR/organize_screenshots.py"
chmod +x "$INSTALL_DIR/organize_screenshots.py"

# Remember the current screenshot location so uninstall can restore it.
if [ ! -f "$PREVIOUS_LOCATION_FILE" ]; then
  CURRENT_LOCATION="$(defaults read com.apple.screencapture location 2>/dev/null || echo "$HOME/Desktop")"
  if [ "$CURRENT_LOCATION" != "$INBOX" ]; then
    printf '%s\n' "$CURRENT_LOCATION" > "$PREVIOUS_LOCATION_FILE"
  fi
fi

# Small wrapper so the Folder Action has one thing to call, with the right
# environment, running in the background so it never blocks Finder.
RUNNER="$INSTALL_DIR/run.sh"
cat > "$RUNNER" <<RUNNER_SH
#!/bin/bash
export PATH=$(printf '%q' "$RUN_PATH")
export SCREENSHOT_ORGANIZER_MODEL=$(printf '%q' "$MODEL")
export SCREENSHOT_ORGANIZER_STRIP_ENV=$(printf '%q' "$STRIP_ENV")
exec $(printf '%q' "$PYTHON_BIN") $(printf '%q' "$INSTALL_DIR/organize_screenshots.py") \\
  --screenshots-root $(printf '%q' "$ROOT") \\
  --claude-bin $(printf '%q' "${CLAUDE_BIN:-claude}") \\
  "\$@"
RUNNER_SH
chmod +x "$RUNNER"

APPLESCRIPT_SRC="$INSTALL_DIR/screenshot-organizer-folder-action.applescript"
cat > "$APPLESCRIPT_SRC" <<APPLESCRIPT
property runnerPath : "$RUNNER"

on adding folder items to this_folder after receiving added_items
  repeat with added_item in added_items
    set itemPath to POSIX path of added_item
    do shell script quoted form of runnerPath & " " & quoted form of itemPath & " > /dev/null 2>&1 &"
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
echo "Inbox:         $INBOX"
echo "Destination:   $ROOT/YYYY-MM/"
echo "Folder Action: $SCRIPT_DEST"
echo "Claude CLI:    ${CLAUDE_BIN:-not installed (using API key)} (model: $MODEL)"
echo ""

# --- Smoke test under the same bare environment a Folder Action gets --------

echo "Checking Claude works from a Folder Action environment..."
if env -i HOME="$HOME" USER="$USER" LOGNAME="${LOGNAME:-$USER}" TMPDIR="${TMPDIR:-/tmp}" \
    "$RUNNER" --check; then
  echo ""
  echo "All set. Take a screenshot (Cmd+Shift+4) and it should land in $ROOT/$(date +%Y-%m)/"
else
  echo ""
  echo "WARNING: the check failed, so screenshots will go to _Needs Review."
  if [ ! -f "$API_KEY_FILE" ]; then
    echo "If your Claude CLI login doesn't work from Folder Actions, save an API key:"
    echo "  mkdir -p $API_KEY_DIR && (umask 077; pbpaste > $API_KEY_FILE)"
  fi
  echo "Then re-run: $RUNNER --check"
fi

echo ""
echo "If Folder Actions did not attach, open Folder Actions Setup and attach:"
echo "  $SCRIPT_DEST"
echo "to:"
echo "  $INBOX"
