#!/usr/bin/env python3
"""Rename and file macOS screenshots from a Folder Action inbox."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


DEFAULT_ROOT = Path.home() / "Documents" / "Screenshots"
DEFAULT_INBOX = DEFAULT_ROOT / "_Inbox"
DEFAULT_REVIEW = DEFAULT_ROOT / "_Needs Review"
DEFAULT_LOG = DEFAULT_ROOT / ".automation" / "screenshot-automation.log"
SHOPIFY_PROXY_ENV = {
    "OPENAI_BASE_URL",
    "OPENAI_API_BASE",
    "OPENAI_API_KEY",
}

PROMPT = """Analyze this screenshot and return JSON only.

Return this shape:
{"filename": "<short-readable-title>"}

Rules:
- filename should describe what the screenshot shows or why it was saved
- use plain words, not a timestamp
- keep it under 60 characters
- no file extension
- no markdown fences
"""

TITLE_SCHEMA = json.dumps(
    {
        "type": "object",
        "properties": {"filename": {"type": "string"}},
        "required": ["filename"],
        "additionalProperties": False,
    }
)


@dataclass
class ProcessResult:
    source: Path
    destination: Path | None
    ok: bool
    message: str


def safe_slug(value: str) -> str:
    """Convert a model-provided title into a readable filesystem slug."""
    value = value.strip().lower()
    value = re.sub(r"[^\w\s-]", " ", value)
    value = re.sub(r"[_\s]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value[:80].strip("-") or "screenshot"


def screenshot_datetime(path: Path) -> datetime:
    """Parse macOS screenshot timestamps, falling back to file mtime."""
    match = re.match(
        r"Screenshot (?P<date>\d{4}-\d{2}-\d{2}) at "
        r"(?P<hour>\d{1,2})\.(?P<minute>\d{2})\.(?P<second>\d{2})"
        r"(?:[\s\u202f](?P<ampm>AM|PM))?",
        path.name,
    )
    if not match:
        return datetime.fromtimestamp(path.stat().st_mtime)

    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    second = int(match.group("second"))
    ampm = match.group("ampm")
    if ampm == "PM" and hour != 12:
        hour += 12
    if ampm == "AM" and hour == 12:
        hour = 0

    return datetime.strptime(match.group("date"), "%Y-%m-%d").replace(
        hour=hour,
        minute=minute,
        second=second,
    )


def destination_for(source: Path, title: str, screenshots_root: Path) -> Path:
    shot_time = screenshot_datetime(source)
    month_dir = screenshots_root / shot_time.strftime("%Y-%m")
    name = f"{shot_time.strftime('%Y-%m-%d')}-{safe_slug(title)}{source.suffix.lower() or '.png'}"
    return unique_path(month_dir / name)


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path

    counter = 2
    while True:
        candidate = path.with_name(f"{path.stem}-{counter}{path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def clean_claude_environment(source: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if source is None else source)
    for key in SHOPIFY_PROXY_ENV:
        env.pop(key, None)
    return env


def wait_until_stable(path: Path, timeout: float = 10.0) -> None:
    """Wait for macOS to finish writing a newly-created screenshot."""
    deadline = time.time() + timeout
    previous: tuple[int, int] | None = None

    while time.time() < deadline:
        stat = path.stat()
        current = (stat.st_size, stat.st_mtime_ns)
        if previous == current and stat.st_size > 0:
            return
        previous = current
        time.sleep(1)

    raise TimeoutError(f"File did not become stable: {path}")


def to_base64(path: Path) -> str:
    return base64.standard_b64encode(path.read_bytes()).decode("ascii")


def parse_claude_stream_result(stdout: str) -> str:
    text = ""
    for line in stdout.splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if event.get("type") == "result":
            structured_output = event.get("structured_output")
            if isinstance(structured_output, dict):
                title = str(structured_output.get("filename", "")).strip()
                if title:
                    return title
            text = event.get("result", "").strip()
            if text:
                break

    if not text:
        raise RuntimeError("Claude did not return a result event")

    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    title = json.loads(text).get("filename", "").strip()
    if not title:
        raise RuntimeError("Claude returned an empty filename")
    return title


def claude_command(claude_bin: str) -> list[str]:
    return [
        claude_bin,
        "-p",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--json-schema",
        TITLE_SCHEMA,
        "--model",
        "sonnet",
    ]


def generate_title(path: Path, claude_bin: str) -> str:
    message = {
        "type": "user",
        "message": {
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
                {"type": "text", "text": PROMPT},
            ],
        },
    }

    result = subprocess.run(
        claude_command(claude_bin),
        input=json.dumps(message),
        capture_output=True,
        text=True,
        timeout=90,
        env=clean_claude_environment(),
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Claude CLI failed")

    return parse_claude_stream_result(result.stdout)


def log(log_path: Path, message: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().isoformat(timespec="seconds")
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"{timestamp} {message}\n")


def move_to_review(source: Path, screenshots_root: Path) -> Path:
    review_dir = screenshots_root / "_Needs Review"
    review_dir.mkdir(parents=True, exist_ok=True)
    destination = unique_path(review_dir / source.name)
    shutil.move(str(source), str(destination))
    return destination


def is_candidate(path: Path) -> bool:
    return (
        path.is_file()
        and path.suffix.lower() == ".png"
        and path.name.startswith("Screenshot ")
    )


def process_file(
    source: Path,
    *,
    screenshots_root: Path = DEFAULT_ROOT,
    claude_bin: str = "claude",
    log_path: Path = DEFAULT_LOG,
) -> ProcessResult:
    source = source.expanduser().resolve()
    screenshots_root = screenshots_root.expanduser().resolve()

    if not is_candidate(source):
        message = f"ignored non-screenshot {source}"
        log(log_path, message)
        return ProcessResult(source, None, True, message)

    try:
        wait_until_stable(source)
        title = generate_title(source, claude_bin)
        destination = destination_for(source, title, screenshots_root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        message = f"moved {source} -> {destination}"
        log(log_path, message)
        return ProcessResult(source, destination, True, message)
    except Exception as error:
        destination = move_to_review(source, screenshots_root)
        message = f"failed {source} -> {destination}: {error}"
        log(log_path, message)
        return ProcessResult(source, destination, False, message)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path, help="Screenshot paths to process")
    parser.add_argument(
        "--screenshots-root",
        type=Path,
        default=DEFAULT_ROOT,
        help="Root screenshots folder. Defaults to ~/Documents/Screenshots.",
    )
    parser.add_argument(
        "--claude-bin",
        default=os.environ.get("SCREENSHOT_ORGANIZER_CLAUDE", "claude"),
        help="Path to the Claude CLI.",
    )
    parser.add_argument(
        "--log-path",
        type=Path,
        default=DEFAULT_LOG,
        help="Path for automation logs.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    paths = args.paths or sorted((args.screenshots_root / "_Inbox").glob("Screenshot *.png"))
    results = [
        process_file(
            path,
            screenshots_root=args.screenshots_root,
            claude_bin=args.claude_bin,
            log_path=args.log_path,
        )
        for path in paths
    ]

    for result in results:
        print(result.message)

    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
