#!/usr/bin/env python3
"""Receive one gaze caption JSON object on stdin and write it into nowhere memory.

This file is meant to run on the Linux/VPS side. It deliberately writes only the
_realtime:* keys used by gaze, leaving the rest of the store intact.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any


STORE = Path(os.getenv("GAZE_STORE_PATH", "/root/.mcp-memory/memories.json"))
TIMELINE_KEY = "_realtime:screen_caption"
WINDOW_PREFIX = "_realtime:window:"
CURRENT_KEY = "_realtime:current_window"
MAX_TIMELINE = int(os.getenv("GAZE_MAX_TIMELINE", "50"))
MAX_PER_WINDOW = int(os.getenv("GAZE_MAX_PER_WINDOW", "50"))
MAX_CAPTION_CHARS = int(os.getenv("GAZE_MAX_CAPTION_CHARS", "500"))


def main() -> int:
    raw = sys.stdin.read().strip()
    if not raw:
        print("ERR empty stdin", file=sys.stderr)
        return 1

    try:
        entry = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"ERR invalid json: {exc}", file=sys.stderr)
        return 1

    if not isinstance(entry, dict):
        print("ERR payload must be an object", file=sys.stderr)
        return 1

    caption = str(entry.get("caption", "")).strip()
    if not caption:
        print("ERR missing caption", file=sys.stderr)
        return 1

    entry["caption"] = caption[:MAX_CAPTION_CHARS]
    entry.setdefault("ts", iso_now())
    window = sanitize_window(entry.get("window") or "fullscreen")
    entry["window"] = window
    window_key = f"{WINDOW_PREFIX}{window}"

    STORE.parent.mkdir(parents=True, exist_ok=True)
    if not STORE.exists():
        STORE.write_text("{}", encoding="utf-8")

    with STORE.open("r+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            data = load_store(fh)
            timeline = get_list(data, TIMELINE_KEY)
            win_list = get_list(data, window_key)

            entry["id"] = next_id(timeline, win_list)
            timeline.append(entry)
            win_list.append(entry)

            data[TIMELINE_KEY] = json.dumps(timeline[-MAX_TIMELINE:], ensure_ascii=False)
            data[window_key] = json.dumps(win_list[-MAX_PER_WINDOW:], ensure_ascii=False)
            data[CURRENT_KEY] = window

            fh.seek(0)
            fh.write(json.dumps(data, ensure_ascii=False, indent=2))
            fh.truncate()
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    print(f"OK id={entry['id']} timeline={len(timeline[-MAX_TIMELINE:])} window={window}")
    return 0


def iso_now() -> str:
    from datetime import datetime

    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_store(fh) -> dict[str, Any]:
    fh.seek(0)
    raw = fh.read().strip()
    if not raw:
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("memory store root must be a JSON object")
    return data


def get_list(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    raw = data.get(key, "")
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
    else:
        parsed = raw
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def next_id(*lists: list[dict[str, Any]]) -> int:
    existing = []
    for items in lists:
        for item in items:
            try:
                existing.append(int(item.get("id", 0)))
            except (TypeError, ValueError):
                pass
    return max(int(time.time() * 1000), max(existing, default=0) + 1)


def sanitize_window(value: Any) -> str:
    text = str(value).strip() or "fullscreen"
    text = text.replace(":", "_")
    text = re.sub(r"[^0-9A-Za-z_.\- \u4e00-\u9fff]+", "_", text)
    text = re.sub(r"\s+", " ", text).strip(" ._-")
    return (text or "fullscreen")[:80]


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERR {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
