#!/usr/bin/env python3
"""Standalone MCP server for gaze realtime data.

This server is deliberately separate from long-term memory MCPs. It exposes only
the short-lived gaze realtime tools and reads/writes a dedicated JSON store.
"""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from mcp import types
from mcp.server import Server

try:
    from gaze_realtime_tools import mark_realtime_read_impl, read_realtime_impl
except ImportError:
    from cognition_gaze_patch import mark_realtime_read_impl, read_realtime_impl


load_dotenv(Path(__file__).parent / ".env")

GAZE_REALTIME_PATH = Path(
    os.environ.get("GAZE_REALTIME_PATH", "/home/linuxuser/search_tool/gaze_realtime.json")
)
GAZE_MCP_TOKEN = os.environ.get("GAZE_MCP_TOKEN", "")
GAZE_MCP_PORT = int(os.environ.get("GAZE_MCP_PORT", "8772"))

app = Server("gaze")


def load_realtime_store() -> dict[str, Any]:
    if not GAZE_REALTIME_PATH.exists():
        return {}
    try:
        with GAZE_REALTIME_PATH.open("r", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                raw = f.read().strip()
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        if not raw:
            return {}
        data = json.loads(raw)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save_realtime_store(data: dict[str, Any]) -> None:
    GAZE_REALTIME_PATH.parent.mkdir(parents=True, exist_ok=True)
    with GAZE_REALTIME_PATH.open("a+", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.seek(0)
            f.write(json.dumps(data, ensure_ascii=False, indent=2))
            f.truncate()
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


@app.list_tools()
async def list_tools():
    return [
        types.Tool(
            name="read_realtime",
            description="读取 gaze 实时屏幕摘要。默认读取当前窗口；window_name=null 读取全局时间线。",
            inputSchema={
                "type": "object",
                "properties": {
                    "window_name": {
                        "type": ["string", "null"],
                        "default": "@current",
                        "description": "@current 为当前窗口；null 为全局时间线；也可传具体窗口名",
                    },
                    "since_id": {
                        "type": ["integer", "null"],
                        "description": "只读取指定 id 之后的条目；不传则按 cursor/unread_only 判断",
                    },
                    "limit": {
                        "type": "integer",
                        "default": 10,
                        "description": "最多返回多少条，范围 1-50",
                    },
                    "unread_only": {
                        "type": "boolean",
                        "default": True,
                        "description": "是否只读未读条目",
                    },
                    "mark_read": {
                        "type": "boolean",
                        "default": False,
                        "description": "读取后是否推进对应 cursor",
                    },
                },
            },
        ),
        types.Tool(
            name="mark_realtime_read",
            description="把 gaze 实时屏幕摘要标记为已读。window_name 不传时标记全局时间线。",
            inputSchema={
                "type": "object",
                "properties": {
                    "up_to_id": {
                        "type": ["integer", "null"],
                        "description": "标记到指定 id；不传则标记到最新",
                    },
                    "window_name": {
                        "type": ["string", "null"],
                        "description": "传具体窗口名时只推进该窗口 cursor",
                    },
                },
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict | None):
    arguments = arguments or {}

    if name == "read_realtime":
        result = read_realtime_impl(
            load_realtime_store,
            save_realtime_store,
            arguments.get("window_name", "@current"),
            arguments.get("since_id"),
            arguments.get("limit", 10),
            arguments.get("unread_only", True),
            arguments.get("mark_read", False),
        )
    elif name == "mark_realtime_read":
        result = mark_realtime_read_impl(
            load_realtime_store,
            save_realtime_store,
            arguments.get("up_to_id"),
            arguments.get("window_name"),
        )
    else:
        raise ValueError(f"Unknown tool: {name}")

    return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]


def run_http(port: int = GAZE_MCP_PORT) -> None:
    import uvicorn
    from contextlib import asynccontextmanager

    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse, Response
    from starlette.routing import Mount, Route

    allowed_origins = {"https://claude.ai", "https://app.claude.com"}
    session_manager = StreamableHTTPSessionManager(app=app, stateless=True)

    @asynccontextmanager
    async def lifespan(_app):
        async with session_manager.run():
            yield

    def extract_token(scope) -> str:
        from urllib.parse import parse_qs

        query_string = scope.get("query_string", b"").decode()
        params = parse_qs(query_string)
        headers = dict(scope.get("headers", []))
        return (
            params.get("token", [None])[0]
            or headers.get(b"authorization", b"").decode().replace("Bearer ", "").strip()
        )

    async def authed_mcp(scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            token = extract_token(scope)
            if not GAZE_MCP_TOKEN or token != GAZE_MCP_TOKEN:
                await Response("Unauthorized", status_code=401)(scope, receive, send)
                return
            origin = headers.get(b"origin", b"").decode()
            if origin and origin not in allowed_origins:
                await Response("Forbidden", status_code=403)(scope, receive, send)
                return
        await session_manager.handle_request(scope, receive, send)

    async def health(_request):
        return JSONResponse({"ok": True, "service": "gaze"})

    starlette_app = Starlette(
        lifespan=lifespan,
        routes=[
            Route("/health", health, methods=["GET"]),
            Mount("/mcp", app=authed_mcp),
        ],
    )

    uvicorn.run(starlette_app, host="127.0.0.1", port=port)


if __name__ == "__main__":
    run_http()
