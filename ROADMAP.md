# Roadmap

## Done

- Mac-friendly local client with full-screen, window, and region capture.
- Optional RapidOCR text channel.
- Optional GLM vision-caption channel.
- Dry-run mode for safe local testing.
- Local SSH batching queue with single-object and list payload support.
- Mask presets and manual mask rectangles before OCR/vision.
- Vision image-diff throttling for unchanged frames.
- Foreground macOS window tracking with `--follow-active-window`.
- VPS-side `push_caption.py` with file locking, sanitized window names, bounded timeline size, and fixed JSON read/write handling.
- cognition helper for `realtime_surface()` and `mark_realtime_read_impl()`.
- One-command `safe_check.sh`.
- Private GitHub repo with `.env` and `.venv` ignored.

## Next

1. Add per-window cursors instead of one global `_realtime:screen_cursor`.
2. Add TTL cleanup for stale `_realtime:*` entries.
3. Add an MCP read tool so wakeup can surface "unread count" first and pull full screen context only when needed.

## Later

- Small launcher UI for selecting window/region and toggling dry-run/upload.
- Provider abstraction for Qwen-VL, Doubao, local OCR-only mode, and future vision APIs.
- Optional encrypted transport or HTTP ingest endpoint to avoid one SSH process per entry.
