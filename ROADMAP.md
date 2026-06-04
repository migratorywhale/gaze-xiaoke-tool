# Roadmap

## Done

- Mac-friendly local client with full-screen, window, and region capture.
- Optional RapidOCR text channel.
- Optional GLM vision-caption channel.
- Dry-run mode for safe local testing.
- Local SSH batching queue with single-object and list payload support.
- Mask presets and manual mask rectangles before OCR/vision.
- App-aware auto masking for common browser windows.
- Vision image-diff throttling for unchanged frames.
- Foreground macOS window tracking with `--follow-active-window`.
- Tkinter/browser launchers for composing and running local gaze commands.
- VPS-side `push_caption.py` with file locking, sanitized window names, bounded timeline size, and fixed JSON read/write handling.
- TTL cleanup for stale `_realtime:*` entries.
- cognition helper for `realtime_surface()` and `mark_realtime_read_impl()`.
- Per-window cursors via `_realtime:window_cursor:<window>`.
- `read_realtime_impl()` helper for pull-on-demand screen context.
- Standalone `gaze_mcp_server.py` with dedicated token, URL, and pm2 process.
- Split gaze tools back out of Xiaoke's memory MCP after verifying the standalone endpoint.
- One-command `safe_check.sh`.
- Private GitHub repo with `.env` and `.venv` ignored.

## Next

1. Add more precise mask presets for menu bars, notifications, and per-browser toolbar heights.
2. Add launcher niceties: window list, saved profiles, and clearer upload warnings.

## Later

- Small launcher UI for selecting window/region and toggling dry-run/upload.
- Provider abstraction for Qwen-VL, Doubao, local OCR-only mode, and future vision APIs.
- Optional encrypted transport or HTTP ingest endpoint to avoid one SSH process per entry.
