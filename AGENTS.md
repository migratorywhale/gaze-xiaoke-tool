# Codex Instructions For This Repo

This repository packages a gaze-style realtime screen caption tool. It is used
by Xiaoke now, but should stay reusable for XiaoG, Ama, and other explicitly
authorized AI users.

Keep the project conservative by default:

- Do not commit `.env`, API keys, SSH keys, screenshots, logs, memory stores, or generated OCR/caption payloads.
- Keep `--dry-run` examples prominent and test locally before enabling SSH push.
- Treat screen content as sensitive. Prefer cropping, masking, filtering, and temporary stores when testing.
- Do not edit any AI's identity, diary, memory database, or production cognition service directly unless Isa explicitly asks for that exact maintenance operation.
- For service integration, prefer helper files and patches that can be reviewed before deployment.
- Use small commits with clear messages. Push after meaningful repo-maintenance changes.

Useful checks:

```bash
. .venv/bin/activate
python -m py_compile gaze_local.py gaze_launcher.py gaze_web_launcher.py push_caption.py cognition_gaze_patch.py
python gaze_local.py --once --dry-run --no-ocr --caption-provider none
./safe_check.sh
```

For server-side testing, use a temporary store first:

```bash
echo '{"caption":"hello gaze","window":"test","source":"manual"}' \
  | GAZE_STORE_PATH=/tmp/gaze-test-memories.json python3 push_caption.py
```
