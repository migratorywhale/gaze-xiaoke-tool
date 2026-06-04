#!/usr/bin/env python3
"""Drop-in helpers for exposing gaze realtime data from a cognition-style MCP.

Usage pattern in the server:

    from cognition_gaze_patch import realtime_surface, mark_realtime_read_impl

    # Inside wakeup/surface building:
    result.update(realtime_surface(all_data))

    @mcp.tool()
    def mark_realtime_read(up_to_id=None, window_name=None):
        return mark_realtime_read_impl(_load_all, _save_all, up_to_id, window_name)
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
) -> dict[str, Any]:
    rt = decode_list(all_data.get(TIMELINE_KEY))
    cursor = safe_int(all_data.get(CURSOR_KEY), 0)

    unread = [
        entry for entry in rt
        if isinstance(entry.get("id"), int) and entry["id"] > cursor
    ]

    realtime_screen = None
    if rt and is_fresh(rt[-1].get("ts"), max_age_sec=max_age_sec):
        realtime_screen = rt[-timeline_tail:]

    windows = {}
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
        windows[window_name] = entries[-window_tail:]

        window_cursor = safe_int(all_data.get(window_cursor_key(window_name)), cursor)
        window_unread = [
            entry for entry in entries
            if isinstance(entry.get("id"), int) and entry["id"] > window_cursor
        ]
        window_unread_counts[window_name] = len(window_unread)
        if window_name == current_window:
            current_window_unread = window_unread or None
            current_window_unread_count = len(window_unread)

    return {
        "realtime_screen": realtime_screen,
        "realtime_screen_unread": unread or None,
        "realtime_screen_unread_count": len(unread),
        "realtime_current_window": current_window,
        "realtime_windows": windows,
        "realtime_window_unread_counts": window_unread_counts,
        "realtime_current_window_unread": current_window_unread,
        "realtime_current_window_unread_count": current_window_unread_count,
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
