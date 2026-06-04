#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "Missing .venv. Run: bash install_macos.sh"
  exit 1
fi

PY=.venv/bin/python
TMP_STORE="${TMPDIR:-/tmp}/gaze-safe-check-$$.json"
TTL_STORE="${TMPDIR:-/tmp}/gaze-safe-check-ttl-$$.json"
trap 'rm -f "$TMP_STORE" "$TTL_STORE"' EXIT

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

echo "== cognition helper =="
"$PY" - <<'PY'
import json

from cognition_gaze_patch import mark_realtime_read_impl, realtime_surface

data = {
    "_realtime:screen_caption": json.dumps([
        {"id": 1, "caption": "a1", "window": "A", "ts": "2026-01-01T00:00:00+00:00"},
        {"id": 2, "caption": "a2", "window": "A", "ts": "2026-01-01T00:00:01+00:00"},
        {"id": 3, "caption": "b1", "window": "B", "ts": "2026-01-01T00:00:02+00:00"},
    ]),
    "_realtime:window:A": json.dumps([
        {"id": 1, "caption": "a1", "window": "A", "ts": "2026-01-01T00:00:00+00:00"},
        {"id": 2, "caption": "a2", "window": "A", "ts": "2026-01-01T00:00:01+00:00"},
    ]),
    "_realtime:window:B": json.dumps([
        {"id": 3, "caption": "b1", "window": "B", "ts": "2026-01-01T00:00:02+00:00"},
    ]),
    "_realtime:current_window": "B",
}

surface = realtime_surface(data, max_age_sec=999999999)
assert surface["realtime_screen_unread_count"] == 3
assert surface["realtime_window_unread_counts"] == {"A": 2, "B": 1}
assert surface["realtime_current_window_unread_count"] == 1

def load_all():
    return data

def save_all(next_data):
    data.clear()
    data.update(next_data)

result = mark_realtime_read_impl(load_all, save_all, window_name="A")
assert result == {"updated": True, "window": "A", "new_cursor": 2}
surface = realtime_surface(data, max_age_sec=999999999)
assert surface["realtime_window_unread_counts"] == {"A": 0, "B": 1}

result = mark_realtime_read_impl(load_all, save_all)
assert result == {"updated": True, "new_cursor": 3}
surface = realtime_surface(data, max_age_sec=999999999)
assert surface["realtime_screen_unread_count"] == 0
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

echo "== push_caption TTL cleanup =="
"$PY" - "$TTL_STORE" <<'PY'
import json
import sys
from pathlib import Path

old = {"id": 1, "caption": "old", "window": "old_window", "source": "manual", "ts": "2000-01-01T00:00:00+00:00"}
data = {
    "_realtime:screen_caption": json.dumps([old]),
    "_realtime:window:old_window": json.dumps([old]),
    "_realtime:window_cursor:old_window": "1",
    "_realtime:current_window": "old_window",
}
Path(sys.argv[1]).write_text(json.dumps(data), encoding="utf-8")
PY
printf '%s' '{"caption":"fresh","window":"fresh_window","source":"manual"}' \
  | GAZE_STORE_PATH="$TTL_STORE" GAZE_TTL_SECONDS=60 "$PY" push_caption.py
"$PY" - "$TTL_STORE" <<'PY'
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text())
timeline = json.loads(data["_realtime:screen_caption"])
assert len(timeline) == 1
assert timeline[0]["caption"] == "fresh"
assert data["_realtime:current_window"] == "fresh_window"
assert "_realtime:window:old_window" not in data
assert "_realtime:window_cursor:old_window" not in data
print("ttl current", data["_realtime:current_window"])
PY

if [ "${GAZE_SKIP_SCREEN_CHECK:-0}" = "1" ]; then
  echo "== screen check skipped =="
else
  echo "== screen permission dry-run =="
  "$PY" gaze_local.py --once --dry-run --no-ocr --caption-provider none --mask-preset mac-safe
  "$PY" gaze_local.py --once --dry-run --no-ocr --caption-provider none --follow-active-window --mask-preset mac-safe
fi

echo "safe_check OK"
