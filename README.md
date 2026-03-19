# Screenshot Organizer

Auto-organizes Desktop screenshots using Claude vision. Every new screenshot gets categorized and renamed into a folder structure that's actually searchable — no more hunting through `Screenshot 2026-03-13 at 9.54.13 AM.png`.

Runs as a launchd agent on macOS, so it fires automatically whenever a screenshot lands on your Desktop.

## What it does

1. Watches `~/Desktop` for new screenshots (anything matching `Screenshot *.png`)
2. Sends each image to Claude, which reads it and returns a category and a descriptive kebab-case filename
3. Moves the file to `~/Documents/Screenshots/{category}/descriptive-name.png`

So `Screenshot 2026-03-06 at 5.49.35 PM.png` becomes `Documents/shopify-admin-nav-ia-future-state-comparison.png`, and Spotlight can actually find it.

## Categories

- **Charts & Data** — graphs, survey results, data visualizations
- **Code** — terminals, editors, debug output
- **Documents** — slides, docs, strategy decks
- **Experiential** — prototypes, explorations, UI experiments
- **Social Media** — Slack, Twitter, LinkedIn, Pinterest
- **Websites** — product UIs, tools, anything browser-based
- **Other** — everything else

## Requirements

- macOS
- Python 3 (ships with macOS)
- [Claude Code CLI](https://claude.ai/code) installed and authenticated (`claude` in your PATH)

No Anthropic API key needed. The script uses `claude -p` with your existing Claude Code credentials.

## Installation

**1. Clone the repo**

```bash
git clone https://github.com/Tobybarnes/screenshot-organizer.git
cd screenshot-organizer
```

**2. Copy the script**

```bash
cp organize-screenshots.py ~/.local/bin/organize-screenshots.py
chmod +x ~/.local/bin/organize-screenshots.py
```

**3. Edit paths in the script**

Open `~/.local/bin/organize-screenshots.py` and update `SCREENSHOTS_DIR` if you want screenshots stored somewhere other than `~/Documents/Screenshots`.

**4. Install the launchd agent**

Open `com.toby.screenshot-organizer.plist` and replace the two occurrences of `/Users/tobybarnes` with your actual home directory path, then:

```bash
cp com.toby.screenshot-organizer.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.toby.screenshot-organizer.plist
```

**5. Test it**

Run the script manually to process any screenshots already on your Desktop:

```bash
python3 ~/.local/bin/organize-screenshots.py
```

## Logs

```bash
tail -f ~/Library/Logs/screenshot-organizer.log
```

## Uninstall

```bash
launchctl unload ~/Library/LaunchAgents/com.toby.screenshot-organizer.plist
rm ~/Library/LaunchAgents/com.toby.screenshot-organizer.plist
rm ~/.local/bin/organize-screenshots.py
```
