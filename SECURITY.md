# Security Notes

This tool can observe screen contents. Treat every OCR line and vision caption as potentially private.

## Never Commit

- `.env`
- API keys, SSH keys, tokens, passwords, cookies
- raw screenshots
- generated screen-caption logs
- long-term memory stores
- personal browser history, URLs, or bookmark exports

## Safe Test Order

1. Run local syntax checks.
2. Run `./safe_check.sh`.
3. Test OCR on generated images or a deliberately safe screen.
4. Test `push_caption.py` with `GAZE_STORE_PATH=/tmp/gaze-test-memories.json`.
5. Only then point the client at a real SSH host/store.

## Runtime Defaults

- Prefer `--dry-run` for first tests.
- Prefer `--region` or `--window` over full-screen capture.
- `--window` and `--follow-active-window` use a narrow default: when the intended window cannot be found, gaze skips/fails instead of capturing fullscreen. Only use `--allow-fullscreen-fallback` for deliberate manual debugging.
- Add private app/window fragments to `GAZE_WINDOW_BLACKLIST`; matching windows are skipped entirely.
- Prefer `--mask-preset mac-safe` when testing on a normal desktop.
- Use `--auto-mask` with `--follow-active-window` to cover known browser chrome automatically.
- Use `--follow-active-window` only after confirming the current foreground app is safe to observe.
- Prefer full-screen target apps when using OCR/caption, so browser chrome and desktop notifications are hidden.
- Use `GAZE_BOOKMARK_KEYWORDS` for local words that should be stripped before upload.
- Keep `GAZE_TTL_SECONDS` short enough that `_realtime:*` remains temporary perception, not long-term memory.
- Keep gaze MCP separate from memory MCP. Gaze should expose only `read_realtime` and `mark_realtime_read`.
- Keep `GAZE_MCP_TOKEN` private. The full connection URL belongs in `.env` or `.gaze_mcp_connection.txt`, never in committed docs.

## Production Changes

Do not directly modify Xiaoke's live MCP service or memory store without an explicit instruction from Isa. Prepare reviewable helper files, patches, or deployment commands first.
