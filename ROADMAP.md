# Roadmap

## Done

- Mac-friendly local client with full-screen, window, and region capture.
- Optional RapidOCR text channel.
- Optional GLM vision-caption channel.
- Dry-run mode for safe local testing.
- VPS-side `push_caption.py` with file locking, sanitized window names, bounded timeline size, and fixed JSON read/write handling.
- cognition helper for `realtime_surface()` and `mark_realtime_read_impl()`.
- Private GitHub repo with `.env` and `.venv` ignored.

## Next

1. Add a local queue so OCR/caption entries can be batched before SSH push.
2. Add image masking/cropping presets for menu bar, browser address bar, bookmarks, and Dock.
3. Add active-window tracking on macOS so capture can follow the foreground app.
4. Add image-difference throttling so unchanged screens skip vision calls.
5. Add per-window cursors instead of one global `_realtime:screen_cursor`.
6. Add TTL cleanup for stale `_realtime:*` entries.
7. Add an MCP read tool so wakeup can surface "unread count" first and pull full screen context only when needed.

## Later

- Small launcher UI for selecting window/region and toggling dry-run/upload.
- Provider abstraction for Qwen-VL, Doubao, local OCR-only mode, and future vision APIs.
- Optional encrypted transport or HTTP ingest endpoint to avoid one SSH process per entry.
