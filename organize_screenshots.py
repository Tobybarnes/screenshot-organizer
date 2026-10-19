#!/usr/bin/env python3
"""Rename and file macOS screenshots from a Folder Action inbox."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

DEFAULT_ROOT = Path.home() / "Documents" / "Screenshots"
DEFAULT_LOG = DEFAULT_ROOT / ".automation" / "screenshot-automation.log"
DEFAULT_MODEL = os.environ.get("SCREENSHOT_ORGANIZER_MODEL", "haiku")
API_KEY_FILE = Path.home() / ".config" / "screenshot-organizer" / "anthropic_api_key"
API_URL = "https://api.anthropic.com/v1/messages"

# The CLI understands short aliases; the API needs a full model ID.
MODEL_ALIASES = {
    "haiku": "claude-haiku-5-5",
    "sonnet": "claude-sonnet-5-5",
    "opus": "claude-opus-5-5",
}

# Claude's vision input tops out around 1568px on the long edge and 5MB per
# image, so anything bigger is wasted upload (or an outright failure).
MAX_EDGE_PX = 1568
MAX_IMAGE_BYTES = 5 * 1024 * 1024

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

# Flags that make each call start faster and leave less behind. They are
# optional: if the installed CLI rejects one, we retry without them.
LEAN_FLAGS = ["--strict-mcp-config", "--no-session-persistence"]


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
        r"(?:[\s ](?P<ampm>AM|PM))?",
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


def strip_env_names() -> set[str]:
    """Env vars to hide from the Claude subprocess (comma-separated, optional)."""
    raw = os.environ.get("SCREENSHOT_ORGANIZER_STRIP_ENV", "")
    return {name.strip() for name in raw.split(",") if name.strip()}


def clean_claude_environment(
    source: dict[str, str] | None = None,
    strip: set[str] | None = None,
) -> dict[str, str]:
    env = dict(os.environ if source is None else source)
    for key in strip_env_names() if strip is None else strip:
        env.pop(key, None)
    # Folder Actions can start with a sparse environment; the CLI needs HOME
    # to find its config.
    env.setdefault("HOME", str(Path.home()))
    return env


def load_api_key(key_file: Path | None = None) -> str | None:
    """Return an Anthropic API key from the config file, else the environment."""
    path = API_KEY_FILE if key_file is None else key_file
    try:
        key = path.read_text(encoding="utf-8").strip()
        if key:
            return key
    except OSError:
        pass
    return os.environ.get("ANTHROPIC_API_KEY", "").strip() or None


def api_model_id(model: str) -> str:
    return MODEL_ALIASES.get(model, model)


def parse_title_text(text: str) -> str:
    """Pull {"filename": ...} out of a model reply, tolerating fences or chatter."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise RuntimeError(f"Claude did not return JSON: {text[:120]!r}")
    title = str(json.loads(match.group(0)).get("filename", "")).strip()
    if not title:
        raise RuntimeError("Claude returned an empty filename")
    return title


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


def prepare_image(path: Path, sips_bin: str | None = None) -> tuple[bytes, str]:
    """Return (bytes, media_type) small enough to send to Claude.

    Uses macOS `sips` to downscale to a JPEG copy. The original file is
    never modified. Falls back to the raw PNG if sips is unavailable.
    """
    sips = sips_bin or shutil.which("sips")
    if sips:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "preview.jpg"
            result = subprocess.run(
                [
                    sips, "-Z", str(MAX_EDGE_PX),
                    "-s", "format", "jpeg",
                    "-s", "formatOptions", "80",
                    str(path), "--out", str(out),
                ],
                capture_output=True,
                timeout=30,
            )
            if result.returncode == 0 and out.exists() and out.stat().st_size > 0:
                return out.read_bytes(), "image/jpeg"

    data = path.read_bytes()
    if len(data) > MAX_IMAGE_BYTES:
        raise RuntimeError(
            f"Image is {len(data) // 1024}KB and could not be downscaled (sips unavailable)"
        )
    return data, "image/png"


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

    return parse_title_text(text)


def claude_command(claude_bin: str, model: str = DEFAULT_MODEL, lean: bool = True) -> list[str]:
    command = [
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
        model,
    ]
    if lean:
        command.extend(LEAN_FLAGS)
    return command


def _rejected_flag(stderr: str) -> bool:
    lowered = stderr.lower()
    return "unknown option" in lowered or "unknown argument" in lowered


def generate_title(path: Path, claude_bin: str, model: str = DEFAULT_MODEL) -> str:
    """Title a screenshot via the API if a key is configured, else the CLI."""
    data, media_type = prepare_image(path)
    api_key = load_api_key()
    if api_key:
        return _generate_title_api(data, media_type, api_key, model)
    return _generate_title_cli(data, media_type, claude_bin, model)


def _generate_title_api(data: bytes, media_type: str, api_key: str, model: str) -> str:
    """Call the Anthropic Messages API directly (no CLI login needed)."""
    payload = json.dumps(
        {
            "model": api_model_id(model),
            "max_tokens": 256,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": base64.standard_b64encode(data).decode("ascii"),
                            },
                        },
                        {"type": "text", "text": PROMPT},
                    ],
                }
            ],
        }
    ).encode()
    request = urllib.request.Request(
        API_URL,
        data=payload,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            body = json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Anthropic API {error.code}: {_api_error_message(error)}") from None
    except urllib.error.URLError as error:
        raise RuntimeError(f"Could not reach Anthropic API: {error.reason}") from None

    text = "".join(
        block.get("text", "") for block in body.get("content", []) if block.get("type") == "text"
    )
    return parse_title_text(text)


def _api_error_message(error: urllib.error.HTTPError) -> str:
    try:
        detail = json.loads(error.read() or b"{}")
        return detail.get("error", {}).get("message") or error.reason
    except (ValueError, OSError):
        return str(error.reason)


def _generate_title_cli(data: bytes, media_type: str, claude_bin: str, model: str) -> str:
    """Use the local Claude CLI and its existing login."""
    message = {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": base64.standard_b64encode(data).decode("ascii"),
                    },
                },
                {"type": "text", "text": PROMPT},
            ],
        },
    }
    payload = json.dumps(message)

    # Run from a neutral directory so no project CLAUDE.md or settings load.
    workdir = Path(tempfile.gettempdir())
    result = None
    for lean in (True, False):
        result = subprocess.run(
            claude_command(claude_bin, model=model, lean=lean),
            input=payload,
            capture_output=True,
            text=True,
            timeout=90,
            env=clean_claude_environment(),
            cwd=workdir,
        )
        if result.returncode == 0 or not (lean and _rejected_flag(result.stderr)):
            break

    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Claude CLI failed")
    return parse_claude_stream_result(result.stdout)


def log(log_path: Path, message: str) -> None:
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().isoformat(timespec="seconds")
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{timestamp} {message}\n")
    except OSError:
        print(message, file=sys.stderr)


def move_to_review(source: Path, screenshots_root: Path) -> Path:
    review_dir = screenshots_root / "_Needs Review"
    if source.parent == review_dir:
        return source  # already there (e.g. --retry-review); leave it alone
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
    model: str = DEFAULT_MODEL,
) -> ProcessResult:
    source = source.expanduser().resolve()
    screenshots_root = screenshots_root.expanduser().resolve()

    if not source.exists():
        message = f"skipped missing file {source}"
        log(log_path, message)
        return ProcessResult(source, None, True, message)

    if not is_candidate(source):
        message = f"ignored non-screenshot {source}"
        log(log_path, message)
        return ProcessResult(source, None, True, message)

    try:
        wait_until_stable(source)
        title = generate_title(source, claude_bin, model=model)
        destination = destination_for(source, title, screenshots_root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        message = f"moved {source} -> {destination}"
        log(log_path, message)
        return ProcessResult(source, destination, True, message)
    except Exception as error:
        if not source.exists():
            message = f"failed {source} (file disappeared): {error}"
            log(log_path, message)
            return ProcessResult(source, None, False, message)
        try:
            destination = move_to_review(source, screenshots_root)
            message = f"failed {source} -> {destination}: {error}"
        except Exception as move_error:
            destination = None
            message = f"failed {source}: {error}; also could not move to review: {move_error}"
        log(log_path, message)
        return ProcessResult(source, destination, False, message)


def tiny_png() -> bytes:
    """A 64x64 solid PNG, built without dependencies, for --check."""
    width = height = 64
    raw = b"".join(b"\x00" + b"\x33\x66\xcc" * width for _ in range(height))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def run_check(claude_bin: str, model: str) -> int:
    """End-to-end test of the Claude call, without touching any real files."""
    with tempfile.TemporaryDirectory() as tmp:
        sample = Path(tmp) / "Screenshot check.png"
        sample.write_bytes(tiny_png())
        route = "Anthropic API key" if load_api_key() else f"Claude CLI ({claude_bin})"
        try:
            title = generate_title(sample, claude_bin, model=model)
        except Exception as error:
            print(f"check failed via {route}: {error}", file=sys.stderr)
            return 1
    print(f"check ok via {route}: Claude titled a test image '{title}'")
    return 0


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
        "--model",
        default=DEFAULT_MODEL,
        help="haiku, sonnet, opus or a full model ID. Defaults to haiku (or $SCREENSHOT_ORGANIZER_MODEL).",
    )
    parser.add_argument(
        "--log-path",
        type=Path,
        default=DEFAULT_LOG,
        help="Path for automation logs.",
    )
    parser.add_argument(
        "--retry-review",
        action="store_true",
        help="Re-process everything in _Needs Review.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Send a tiny test image to Claude and report whether it worked.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)

    if args.check:
        return run_check(args.claude_bin, args.model)

    if args.paths:
        paths = args.paths
    elif args.retry_review:
        paths = sorted((args.screenshots_root / "_Needs Review").glob("Screenshot *.png"))
    else:
        paths = sorted((args.screenshots_root / "_Inbox").glob("Screenshot *.png"))

    results = [
        process_file(
            path,
            screenshots_root=args.screenshots_root,
            claude_bin=args.claude_bin,
            log_path=args.log_path,
            model=args.model,
        )
        for path in paths
    ]
    for result in results:
        print(result.message)
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
