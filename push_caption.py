#!/usr/bin/env python3
"""Receive gaze caption JSON on stdin and write it into nowhere memory.

Input may be one caption object or a list of caption objects. This file is meant
to run on the Linux/VPS side. It deliberately writes only the _realtime:* keys
used by gaze, leaving the rest of the store intact.
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
WINDOW_CURSOR_PREFIX = "_realtime:window_cursor:"
MAX_TIMELINE = int(os.getenv("GAZE_MAX_TIMELINE", "50"))
MAX_PER_WINDOW = int(os.getenv("GAZE_MAX_PER_WINDOW", "50"))
MAX_CAPTION_CHARS = int(os.getenv("GAZE_MAX_CAPTION_CHARS", "500"))
TTL_SECONDS = int(os.getenv("GAZE_TTL_SECONDS", str(6 * 60 * 60)))


def main() -> int:
    raw = sys.stdin.read().strip()
    if not raw:
        print("ERR empty stdin", file=sys.stderr)
        return 1

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"ERR invalid json: {exc}", file=sys.stderr)
        return 1

    entries = normalize_payload(payload)
    if not entries:
        print("ERR payload must be an object or non-empty object list", file=sys.stderr)
        return 1

    STORE.parent.mkdir(parents=True, exist_ok=True)
    if not STORE.exists():
        STORE.write_text("{}", encoding="utf-8")

    written_ids = []
    current_window = "fullscreen"

    with STORE.open("r+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            data = load_store(fh)
            timeline = get_list(data, TIMELINE_KEY)
            last_id = max((safe_int(item.get("id"), 0) for item in timeline), default=0)

            for entry in entries:
                window = sanitize_window(entry.get("window") or "fullscreen")
                window_key = f"{WINDOW_PREFIX}{window}"
                win_list = get_list(data, window_key)

                entry["window"] = window
                entry["id"] = next_id(last_id, win_list)
                last_id = entry["id"]
                current_window = window

                timeline.append(entry)
                win_list.append(entry)
                written_ids.append(entry["id"])
                data[window_key] = encode_entries(win_list, max_items=MAX_PER_WINDOW)

            timeline = cleanup_realtime_data(data, timeline)
            data[CURRENT_KEY] = timeline[-1].get("window", current_window) if timeline else ""

            fh.seek(0)
            fh.write(json.dumps(data, ensure_ascii=False, indent=2))
            fh.truncate()
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    print(
        f"OK count={len(written_ids)} ids={written_ids[0]}..{written_ids[-1]} "
        f"timeline={len(timeline[-MAX_TIMELINE:])} window={current_window}"
    )
    return 0


def normalize_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        return []

    entries = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        caption = str(item.get("caption", "")).strip()
        if not caption:
            continue
        entry = dict(item)
        entry["caption"] = caption[:MAX_CAPTION_CHARS]
        entry.setdefault("ts", iso_now())
        entries.append(entry)
    return entries


def cleanup_realtime_data(data: dict[str, Any], timeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    timeline = prune_entries(timeline)
    data[TIMELINE_KEY] = encode_entries(timeline, max_items=MAX_TIMELINE)

    live_windows = {
        str(entry.get("window", "")).strip()
        for entry in timeline
        if str(entry.get("window", "")).strip()
    }

    for key in list(data):
        if not key.startswith(WINDOW_PREFIX):
            continue
        window = key[len(WINDOW_PREFIX):]
        entries = prune_entries(get_list(data, key))
        if entries:
            data[key] = encode_entries(entries, max_items=MAX_PER_WINDOW)
            live_windows.add(window)
        else:
            data.pop(key, None)

    for key in list(data):
        if not key.startswith(WINDOW_CURSOR_PREFIX):
            continue
        window = key[len(WINDOW_CURSOR_PREFIX):]
        if window not in live_windows:
            data.pop(key, None)

    return timeline


def prune_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if TTL_SECONDS <= 0:
        return entries

    cutoff = time.time() - TTL_SECONDS
    kept = []
    for entry in entries:
        ts = parse_epoch(entry.get("ts"))
        if ts is None or ts >= cutoff:
            kept.append(entry)
    return kept


def parse_epoch(value: Any) -> float | None:
    if not value:
        return None
    from datetime import datetime

    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.timestamp()
    return parsed.timestamp()


def encode_entries(entries: list[dict[str, Any]], *, max_items: int) -> str:
    return json.dumps(entries[-max_items:], ensure_ascii=False)


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


def next_id(last_id: int, *lists: list[dict[str, Any]]) -> int:
    existing = []
    for items in lists:
        for item in items:
            try:
                existing.append(int(item.get("id", 0)))
            except (TypeError, ValueError):
                pass
    return max(int(time.time() * 1000), last_id + 1, max(existing, default=0) + 1)


def safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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
