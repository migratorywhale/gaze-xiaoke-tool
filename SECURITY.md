# Security Notes

This tool can observe screen contents. Treat every OCR line and vision caption as potentially private.

## Never Commit

- `.env`
- API keys, SSH keys, tokens, passwords, cookies
- raw screenshots
- generated screen-caption logs
- nowhere/cognition memory stores
- personal browser history, URLs, or bookmark exports

## Safe Test Order

1. Run local syntax checks.
2. Run `gaze_local.py --once --dry-run --no-ocr --caption-provider none`.
3. Test OCR on generated images or a deliberately safe screen.
4. Test `push_caption.py` with `GAZE_STORE_PATH=/tmp/gaze-test-memories.json`.
5. Only then point the client at a real SSH host/store.

## Runtime Defaults

- Prefer `--dry-run` for first tests.
- Prefer `--region` or `--window` over full-screen capture.
- Prefer full-screen target apps when using OCR/caption, so browser chrome and desktop notifications are hidden.
- Use `GAZE_BOOKMARK_KEYWORDS` for local words that should be stripped before upload.

## Production Changes

Do not directly modify Xiaoke's live cognition service or memory store without an explicit instruction from Isa. Prepare reviewable helper files, patches, or deployment commands first.
