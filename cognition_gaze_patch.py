#!/usr/bin/env python3
"""Drop-in helpers for exposing gaze realtime data from a cognition-style MCP.

Usage pattern in the server:

    from cognition_gaze_patch import realtime_surface, mark_realtime_read_impl, read_realtime_impl

    # Inside wakeup/surface building:
    result.update(realtime_surface(all_data, include_entries=False))

    @mcp.tool()
    def mark_realtime_read(up_to_id=None, window_name=None):
        return mark_realtime_read_impl(_load_all, _save_all, up_to_id, window_name)

    @mcp.tool()
    def read_realtime(window_name="@current", since_id=None, limit=10, unread_only=True, mark_read=False):
        return read_realtime_impl(_load_all, _save_all, window_name, since_id, limit, unread_only, mark_read)
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable


TIMELINE_KEY = "_realtime:screen_caption"
CURSOR_KEY = "_realtime:screen_cursor"
WINDOW_PREFIX = "_realtime:window:"
WINDOW_CURSOR_PREFIX = "_realtime:window_cursor:"
CURRENT_KEY = "_realtime:current_window"


def realtime_surface(
    all_data: dict[str, Any],
    *,
    max_age_sec: int = 1800,
    timeline_tail: int = 5,
    window_tail: int = 3,
    include_entries: bool = True,
) -> dict[str, Any]:
    rt = decode_list(all_data.get(TIMELINE_KEY))
    cursor = safe_int(all_data.get(CURSOR_KEY), 0)

    unread = [
        entry for entry in rt
        if isinstance(entry.get("id"), int) and entry["id"] > cursor
    ]

    realtime_screen = None
    if include_entries and rt and is_fresh(rt[-1].get("ts"), max_age_sec=max_age_sec):
        realtime_screen = rt[-timeline_tail:]

    windows = {}
    window_latest_ids = {}
    window_unread_counts = {}
    current_window_unread = None
    current_window_unread_count = 0
    current_window = all_data.get(CURRENT_KEY)

    for key, value in all_data.items():
        if not key.startswith(WINDOW_PREFIX):
            continue
        entries = decode_list(value)
        if not entries:
            continue

        window_name = key[len(WINDOW_PREFIX):]
        windows[window_name] = entries[-window_tail:] if include_entries else None
        window_latest_ids[window_name] = max_id(entries)

        window_cursor = safe_int(all_data.get(window_cursor_key(window_name)), cursor)
        window_unread = [
            entry for entry in entries
            if isinstance(entry.get("id"), int) and entry["id"] > window_cursor
        ]
        window_unread_counts[window_name] = len(window_unread)
        if window_name == current_window:
            current_window_unread = (window_unread or None) if include_entries else None
            current_window_unread_count = len(window_unread)

    return {
        "realtime_screen": realtime_screen,
        "realtime_screen_unread": (unread or None) if include_entries else None,
        "realtime_screen_unread_count": len(unread),
        "realtime_latest_id": max_id(rt),
        "realtime_current_window": current_window,
        "realtime_windows": windows,
        "realtime_window_latest_ids": window_latest_ids,
        "realtime_window_unread_counts": window_unread_counts,
        "realtime_current_window_unread": current_window_unread,
        "realtime_current_window_unread_count": current_window_unread_count,
    }


def read_realtime_impl(
    load_all: Callable[[], dict[str, Any]],
    save_all: Callable[[dict[str, Any]], None] | None = None,
    window_name: str | None = None,
    since_id: int | None = None,
    limit: int = 10,
    unread_only: bool = True,
    mark_read: bool = False,
) -> dict[str, Any]:
    data = load_all()
    current_window = data.get(CURRENT_KEY)
    window = resolve_window_name(data, window_name)

    if window:
        entries = decode_list(data.get(f"{WINDOW_PREFIX}{window}"))
        cursor_key = window_cursor_key(window)
        cursor = safe_int(data.get(cursor_key), safe_int(data.get(CURSOR_KEY), 0))
        scope = "window"
    else:
        entries = decode_list(data.get(TIMELINE_KEY))
        cursor_key = CURSOR_KEY
        cursor = safe_int(data.get(CURSOR_KEY), 0)
        scope = "timeline"

    if window and not entries and f"{WINDOW_PREFIX}{window}" not in data:
        return {
            "scope": scope,
            "window": window,
            "current_window": current_window,
            "entries": [],
            "count": 0,
            "cursor": cursor,
            "latest_id": 0,
            "marked_read": False,
            "reason": "window_not_found",
        }

    latest_id = max_id(entries)
    threshold = safe_int(since_id, cursor) if since_id is not None or unread_only else None
    filtered = filter_after_id(entries, threshold) if threshold is not None else list(entries)
    limited = clamp_limit(limit)
    selected = filtered[:limited] if threshold is not None else filtered[-limited:]

    marked_read = False
    new_cursor = cursor
    if mark_read and selected and save_all is not None:
        new_cursor = max_id(selected)
        data[cursor_key] = str(new_cursor)
        save_all(dict(data))
        marked_read = True

    return {
        "scope": scope,
        "window": window,
        "current_window": current_window,
        "entries": selected,
        "count": len(selected),
        "available_count": len(filtered),
        "remaining_count": max(0, len(filtered) - len(selected)),
        "cursor": cursor,
        "new_cursor": new_cursor,
        "latest_id": latest_id,
        "marked_read": marked_read,
        "unread_only": bool(unread_only),
    }


def mark_realtime_read_impl(
    load_all: Callable[[], dict[str, Any]],
    save_all: Callable[[dict[str, Any]], None],
    up_to_id: int | None = None,
    window_name: str | None = None,
) -> dict[str, Any]:
    data = load_all()

    if window_name:
        window = str(window_name).strip()
        entries = decode_list(data.get(f"{WINDOW_PREFIX}{window}"))
        if not entries:
            return {"updated": False, "window": window, "reason": "window_not_found"}

        max_id = max((safe_int(entry.get("id"), 0) for entry in entries), default=0)
        new_cursor = max_id if up_to_id is None else safe_int(up_to_id, max_id)
        data[window_cursor_key(window)] = str(new_cursor)
        save_all(dict(data))
        return {"updated": True, "window": window, "new_cursor": new_cursor}

    rt = decode_list(data.get(TIMELINE_KEY))
    if not rt:
        return {"updated": False}

    max_id = max((safe_int(entry.get("id"), 0) for entry in rt), default=0)
    new_cursor = max_id if up_to_id is None else safe_int(up_to_id, max_id)
    data[CURSOR_KEY] = str(new_cursor)
    save_all(dict(data))
    return {"updated": True, "new_cursor": new_cursor}


def window_cursor_key(window_name: str) -> str:
    return f"{WINDOW_CURSOR_PREFIX}{window_name}"


def resolve_window_name(data: dict[str, Any], window_name: str | None) -> str | None:
    if window_name is None:
        return None
    window = str(window_name).strip()
    if not window:
        return None
    if window == "@current":
        current = str(data.get(CURRENT_KEY) or "").strip()
        return current or None
    return window


def filter_after_id(entries: list[dict[str, Any]], threshold: int | None) -> list[dict[str, Any]]:
    if threshold is None:
        return list(entries)
    return [
        entry for entry in entries
        if isinstance(entry.get("id"), int) and entry["id"] > threshold
    ]


def max_id(entries: list[dict[str, Any]]) -> int:
    return max((safe_int(entry.get("id"), 0) for entry in entries), default=0)


def clamp_limit(value: Any, *, default: int = 10, minimum: int = 1, maximum: int = 50) -> int:
    limit = safe_int(value, default)
    return max(minimum, min(maximum, limit))


def decode_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return []
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def is_fresh(ts: Any, *, max_age_sec: int) -> bool:
    if not ts:
        return False
    try:
        then = datetime.fromisoformat(str(ts))
    except ValueError:
        return False
    now = datetime.now(then.tzinfo) if then.tzinfo else datetime.now()
    return (now - then).total_seconds() < max_age_sec
