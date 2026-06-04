#!/usr/bin/env python3
"""Drop-in helpers for exposing gaze realtime data from a cognition-style MCP.

Usage pattern in the server:

    from cognition_gaze_patch import realtime_surface, mark_realtime_read_impl

    # Inside wakeup/surface building:
    result.update(realtime_surface(all_data))

    @mcp.tool()
    def mark_realtime_read(up_to_id=None):
        return mark_realtime_read_impl(_load_all, _save_all, up_to_id)
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable


TIMELINE_KEY = "_realtime:screen_caption"
CURSOR_KEY = "_realtime:screen_cursor"
WINDOW_PREFIX = "_realtime:window:"
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
    for key, value in all_data.items():
        if not key.startswith(WINDOW_PREFIX):
            continue
        entries = decode_list(value)
        if entries:
            windows[key[len(WINDOW_PREFIX):]] = entries[-window_tail:]

    return {
        "realtime_screen": realtime_screen,
        "realtime_screen_unread": unread or None,
        "realtime_screen_unread_count": len(unread),
        "realtime_current_window": all_data.get(CURRENT_KEY),
        "realtime_windows": windows,
    }


def mark_realtime_read_impl(
    load_all: Callable[[], dict[str, Any]],
    save_all: Callable[[dict[str, Any]], None],
    up_to_id: int | None = None,
) -> dict[str, Any]:
    data = load_all()
    rt = decode_list(data.get(TIMELINE_KEY))
    if not rt:
        return {"updated": False}

    max_id = max((safe_int(entry.get("id"), 0) for entry in rt), default=0)
    new_cursor = max_id if up_to_id is None else safe_int(up_to_id, max_id)
    data[CURSOR_KEY] = str(new_cursor)
    save_all(data)
    return {"updated": True, "new_cursor": new_cursor}


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
