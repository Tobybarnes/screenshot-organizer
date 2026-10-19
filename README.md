# Screenshot Organizer

Mac-native screenshot filing with Claude-generated filenames.

The workflow uses a Folder Action attached to `~/Documents/Screenshots/_Inbox`.
New screenshots land in the inbox, Claude reads the image, and the script moves it
to a monthly folder with a readable filename.

Example:

```text
~/Documents/Screenshots/_Inbox/Screenshot 2026-05-28 at 11.24.51 AM.png
~/Documents/Screenshots/2026-05/2026-05-28-campaign-budget-dashboard.png
```

## What It Does

1. Creates `~/Documents/Screenshots/_Inbox`.
2. Sets macOS screenshots to save into that inbox (and remembers your old location).
3. Installs a Folder Action script for the inbox.
4. Downscales a temporary copy of each screenshot and asks Claude CLI for a short title.
5. Moves screenshots into `~/Documents/Screenshots/YYYY-MM/`.
6. Moves failures into `~/Documents/Screenshots/_Needs Review/`.

No cron, launchd or Python packages; it uses only the standard library.

## Requirements

- macOS
- Python 3 (`xcode-select --install` if missing)
- One way to reach Claude:
  - **Claude Code CLI**, installed and logged in (uses your Claude plan), or
  - **an Anthropic API key** from [console.anthropic.com](https://console.anthropic.com/settings/keys) (billed per call to your API account). Use this if your CLI login doesn't work from Folder Actions.

If an API key is saved, it is used; otherwise the CLI is used. The installer offers
to save a key to `~/.config/screenshot-organizer/anthropic_api_key` (mode 600), or
picks up `ANTHROPIC_API_KEY` from your environment.

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
- writes `run.sh` there with the `PATH` your Claude CLI needs (Folder Actions run with a bare `PATH`, which breaks npm-installed Claude)
- compiles a Folder Action script into `~/Library/Scripts/Folder Action Scripts/`
- attaches that script to `~/Documents/Screenshots/_Inbox`
- sets the macOS screenshot save location to the inbox
- runs a self-check that sends a tiny test image to Claude under the same bare environment a Folder Action gets

If macOS blocks the scripted Folder Action attachment, open **Folder Actions
Setup** and attach `~/Library/Scripts/Folder Action Scripts/Screenshot Organizer.scpt`
to `~/Documents/Screenshots/_Inbox`.

### Options

Set these before running `./install.sh`:

| Variable | Default | Purpose |
| --- | --- | --- |
| `SCREENSHOT_ORGANIZER_ROOT` | `~/Documents/Screenshots` | Where everything lives |
| `SCREENSHOT_ORGANIZER_MODEL` | `haiku` | `haiku`, `sonnet`, `opus`, or a full model ID |
| `SCREENSHOT_ORGANIZER_STRIP_ENV` | _(empty)_ | Comma-separated env vars to hide from the Claude subprocess, e.g. if a proxy variable in your shell interferes with Claude |

## Test

```bash
~/.local/share/screenshot-organizer/run.sh --check
```

Then take a screenshot with the normal shortcut. A processed file should appear in
`~/Documents/Screenshots/YYYY-MM/`.

Logs: `~/Documents/Screenshots/.automation/screenshot-automation.log`

## Backfill Existing Desktop Screenshots

```bash
~/.local/share/screenshot-organizer/run.sh ~/Desktop/Screenshot*.png
```

## Retry Failures

```bash
~/.local/share/screenshot-organizer/run.sh --retry-review
```

## Uninstall

```bash
./uninstall.sh
```

Removes the Folder Action and installed worker, and restores your previous
screenshot save location. Organised screenshots are left alone.

## Troubleshooting

### `--check` fails

The output says which route it tried.

- **Claude CLI:** run `claude` once in Terminal to make sure it is logged in. If it
  works in Terminal but not here, save an API key instead:
  `mkdir -p ~/.config/screenshot-organizer && (umask 077; pbpaste > ~/.config/screenshot-organizer/anthropic_api_key)`
- **API key:** a `401` means the key is wrong or revoked; replace the file above.

Uninstalling leaves the key file in place; delete it yourself if you no longer need it.

### Files go to `_Needs Review`

The original screenshot is preserved, but titling or moving failed. The reason is
in the log. Fix the cause, then run `run.sh --retry-review`.

### Folder Action does not run

Open **Folder Actions Setup** and confirm:

- Folder Actions are enabled
- `~/Documents/Screenshots/_Inbox` is listed
- `Screenshot Organizer.scpt` is attached and enabled

If it is attached but nothing happens, macOS may be blocking access to
`~/Documents`. Check **System Settings → Privacy & Security → Files and Folders**
(and Full Disk Access) for *Folder Actions Dispatcher* / *System Events*, or set
`SCREENSHOT_ORGANIZER_ROOT` to a folder outside `~/Documents` and reinstall.

## Development

```bash
python3 -m unittest discover -s tests
```
