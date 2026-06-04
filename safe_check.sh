#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "Missing .venv. Run: bash install_macos.sh"
  exit 1
fi

PY=.venv/bin/python
TMP_STORE="${TMPDIR:-/tmp}/gaze-safe-check-$$.json"
trap 'rm -f "$TMP_STORE"' EXIT

echo "== syntax =="
"$PY" -m py_compile gaze_local.py push_caption.py cognition_gaze_patch.py

echo "== local helpers =="
"$PY" - <<'PY'
from PIL import Image

from gaze_local import (
    CaptureTarget,
    EntryPusher,
    apply_masks,
    clean_caption,
    find_active_macos_window,
    frontmost_app_name,
    image_diff_score,
    image_signature,
)

cleaned = clean_caption(
    "Visit https://example.com token=abc sk-12345678901234567890 keep text",
    [],
)
assert cleaned == "Visit keep text", cleaned

img = Image.new("RGB", (200, 100), "white")
masked = apply_masks(img, ["menu-bar"], ["0,80,200,20"])
assert masked.getpixel((10, 10)) == (0, 0, 0)
assert masked.getpixel((10, 90)) == (0, 0, 0)
assert masked.getpixel((100, 50)) == (255, 255, 255)

same = image_diff_score(image_signature(img), image_signature(img))
changed = image_diff_score(image_signature(img), image_signature(masked))
assert same == 0.0
assert changed > 0.0

pusher = EntryPusher(
    ssh_host=None,
    remote_command="python3 push_caption.py",
    dry_run=True,
    retries=0,
    batch_interval=999,
    max_batch=2,
    max_queue=10,
)
ok, msg = pusher.enqueue({"caption": "one", "window": "test"})
assert ok and msg.startswith("queued"), msg
ok, msg = pusher.enqueue({"caption": "two", "window": "test"})
assert ok and msg.startswith("pushed 2"), msg

entry = {
    "source": "unit",
    "caption": "ok",
    "ts": "2026-01-01T00:00:00+00:00",
    "window": CaptureTarget(None, "unit").label,
}
assert entry["window"] == "unit"

frontmost = frontmost_app_name()
active = find_active_macos_window()
assert frontmost is None or isinstance(frontmost, str)
assert active is None or active.label
PY

echo "== OCR generated image =="
"$PY" - <<'PY'
from PIL import Image, ImageDraw

from gaze_local import OcrEngine

img = Image.new("RGB", (500, 140), "white")
draw = ImageDraw.Draw(img)
draw.text((30, 45), "HELLO GAZE 123", fill="black")
texts = OcrEngine().read(img, min_chars=1, min_score=0.1)
joined = " ".join(texts)
assert "HELLO" in joined and "GAZE" in joined, texts
print(joined)
PY

echo "== push_caption temp store =="
printf '%s' '[{"caption":"one","window":"A:test","source":"manual"},{"caption":"two","window":"B:test","source":"manual"}]' \
  | GAZE_STORE_PATH="$TMP_STORE" "$PY" push_caption.py
"$PY" - "$TMP_STORE" <<'PY'
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text())
timeline = json.loads(data["_realtime:screen_caption"])
assert len(timeline) == 2
assert data["_realtime:current_window"] == "B_test"
assert "_realtime:window:A_test" in data
assert "_realtime:window:B_test" in data
print("timeline", len(timeline), "current", data["_realtime:current_window"])
PY

if [ "${GAZE_SKIP_SCREEN_CHECK:-0}" = "1" ]; then
  echo "== screen check skipped =="
else
  echo "== screen permission dry-run =="
  "$PY" gaze_local.py --once --dry-run --no-ocr --caption-provider none --mask-preset mac-safe
  "$PY" gaze_local.py --once --dry-run --no-ocr --caption-provider none --follow-active-window --mask-preset mac-safe
fi

echo "safe_check OK"
