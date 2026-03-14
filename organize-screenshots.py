#!/usr/bin/env python3
"""
Screenshot Organizer
Watches ~/Desktop for new screenshots, categorizes them using Claude vision,
and moves them to ~/Local documents/Screenshots/{category}/descriptive-name.png
"""

import anthropic
import base64
import json
import os
import re
import shutil
import sys
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

DESKTOP = Path.home() / "Desktop"
SCREENSHOTS_DIR = Path("/Users/tobybarnes/Local documents/Screenshots")
API_KEY_FILE = Path.home() / ".anthropic_api_key"

CATEGORIES = [
    "Charts & Data",
    "Social Media",
    "Websites",
    "Documents",
    "Code",
    "Experiential",
    "Other",
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def get_api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key:
        return key
    if API_KEY_FILE.exists():
        return API_KEY_FILE.read_text().strip()
    sys.exit(
        f"No API key found. Set ANTHROPIC_API_KEY env var or write it to {API_KEY_FILE}"
    )


def to_base64(path: Path) -> str:
    return base64.standard_b64encode(path.read_bytes()).decode()


def analyze(client: anthropic.Anthropic, path: Path) -> dict:
    """Send image to Claude, get back category + filename."""
    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=256,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": to_base64(path),
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "Analyze this screenshot and respond with JSON only "
                            "(no markdown fences):\n"
                            '{"category": "<category>", "filename": "<name>"}\n\n'
                            f"category must be one of: {', '.join(CATEGORIES)}\n"
                            "filename: kebab-case, descriptive, under 60 chars, "
                            "no extension. Should make the screenshot findable by "
                            "memory — what it shows or why someone saved it."
                        ),
                    },
                ],
            }
        ],
    )

    text = response.content[0].text.strip()
    # Strip any accidental markdown fences
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def safe_name(name: str) -> str:
    name = re.sub(r"[^\w\-]", "-", name)
    name = re.sub(r"-{2,}", "-", name).strip("-")
    return name[:80] or "screenshot"


def unique_path(directory: Path, stem: str, suffix: str = ".png") -> Path:
    dest = directory / f"{stem}{suffix}"
    n = 1
    while dest.exists():
        dest = directory / f"{stem}-{n}{suffix}"
        n += 1
    return dest


# ── Main ──────────────────────────────────────────────────────────────────────

def process(client: anthropic.Anthropic, path: Path) -> bool:
    print(f"  → {path.name}", flush=True)
    try:
        result = analyze(client, path)
        category = result.get("category", "Other")
        if category not in CATEGORIES:
            category = "Other"
        filename = safe_name(result.get("filename", "screenshot"))

        dest_dir = SCREENSHOTS_DIR / category
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = unique_path(dest_dir, filename)

        shutil.move(str(path), str(dest))
        print(f"     {category}/{dest.name}", flush=True)
        return True

    except Exception as e:
        print(f"     ERROR: {e}", file=sys.stderr, flush=True)
        return False


def main():
    screenshots = sorted(DESKTOP.glob("Screenshot *.png"))
    if not screenshots:
        sys.exit(0)  # Nothing to do — launchd fires on any Desktop change

    print(f"Screenshot Organizer: {len(screenshots)} file(s) to process")
    client = anthropic.Anthropic(api_key=get_api_key())
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    ok = sum(process(client, s) for s in screenshots)
    print(f"Done: {ok}/{len(screenshots)} organized.")


if __name__ == "__main__":
    main()
