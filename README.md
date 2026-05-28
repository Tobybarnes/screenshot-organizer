# Screenshot Organizer

Mac-native screenshot filing with Claude-generated filenames.

The workflow uses a Folder Action attached to `~/Documents/Screenshots/_Inbox`.
New screenshots land in the inbox, Claude reads the image, and the script moves it
to a monthly folder with a readable filename.

Example:

```text
~/Documents/Screenshots/_Inbox/Screenshot 2026-05-28 at 11.24.51 AM.png
~/Documents/Screenshots/2026-05/2026-05-28-shopify-admin-campaign-budget.png
```

## What It Does

1. Creates `~/Documents/Screenshots/_Inbox`.
2. Sets macOS screenshots to save into that inbox.
3. Installs a Folder Action script for the inbox.
4. Uses Claude CLI vision to generate a short human-readable title.
5. Moves screenshots into `~/Documents/Screenshots/YYYY-MM/`.
6. Moves failures into `~/Documents/Screenshots/_Needs Review/`.

This does not use cron, launchd, the Anthropic Python SDK, or an Anthropic API key.

## Requirements

- macOS
- Python 3
- Claude Code CLI installed and authenticated

Check Claude first:

```bash
claude --version
```

If that opens a login or setup flow, finish it before installing.

## Install

```bash
git clone https://github.com/Tobybarnes/screenshot-organizer.git
cd screenshot-organizer
./install.sh
```

The installer:

- copies `organize_screenshots.py` to `~/.local/share/screenshot-organizer/`
- compiles a Folder Action script into `~/Library/Scripts/Folder Action Scripts/`
- attaches that script to `~/Documents/Screenshots/_Inbox`
- sets the macOS screenshot save location to the inbox
- unloads the old `com.toby.screenshot-organizer` LaunchAgent if it exists

If macOS blocks the scripted Folder Action attachment, open **Folder Actions
Setup** and attach:

```text
~/Library/Scripts/Folder Action Scripts/Screenshot Organizer.scpt
```

to:

```text
~/Documents/Screenshots/_Inbox
```

## Test

Copy an existing screenshot into the inbox:

```bash
cp ~/Desktop/Screenshot*.png ~/Documents/Screenshots/_Inbox/
```

Or take a new screenshot with the normal macOS shortcut. A processed file should
appear in:

```text
~/Documents/Screenshots/YYYY-MM/
```

Logs are written to:

```text
~/Documents/Screenshots/.automation/screenshot-automation.log
```

## Backfill Existing Desktop Screenshots

After install, process old Desktop screenshots through the same worker:

```bash
python3 ~/.local/share/screenshot-organizer/organize_screenshots.py ~/Desktop/Screenshot*.png
```

## Uninstall

```bash
./uninstall.sh
```

The uninstaller removes the Folder Action and installed worker. It does not
delete organised screenshots and does not change your current screenshot save
location.

## Troubleshooting

### Claude Fails

Run:

```bash
claude --version
claude
```

The script uses local Claude CLI auth. It does not require `ANTHROPIC_API_KEY`.

### Shopify Proxy Variables

The worker deliberately removes these variables from the Claude subprocess:

```text
OPENAI_BASE_URL
OPENAI_API_BASE
OPENAI_API_KEY
```

On a non-Shopify Mac, authenticate Claude normally with `claude`. Do not set
Shopify proxy values for this tool.

### Files Go To `_Needs Review`

That means the original screenshot was preserved but Claude title generation or
file processing failed. Check:

```text
~/Documents/Screenshots/.automation/screenshot-automation.log
```

### Folder Action Does Not Run

Open **Folder Actions Setup** and confirm:

- Folder Actions are enabled
- `~/Documents/Screenshots/_Inbox` is listed
- `Screenshot Organizer.scpt` is attached and enabled
