#!/usr/bin/env python3
"""Mac-friendly gaze client: screenshot + optional OCR/caption + SSH push."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from typing import Any

from dotenv import load_dotenv
from PIL import Image, ImageGrab


DEFAULT_PROMPT = """看截图，像跟朋友吐槽一样发一条弹幕。
要求：
- 不评价身材、颜值、性别气质，不使用"美女""小姐姐"等凝视词
- 如果有可读文字，优先概括文字内容
- 描述用中性词，例如衣服颜色、窗口状态、画面动作
- 不输出账号、URL、token、邮箱、路径等隐私细节
- 40字以内

输出一句:"""

DEFAULT_REMOTE_COMMAND = "python3 /root/mcp-memory-server/push_caption.py"

NOISE_REGEXES = [
    re.compile(r"https?://\S+", re.I),
    re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"\b(?:xsec_token|token|api[_-]?key|password|passwd|secret)=\S+", re.I),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.I),
    re.compile(r"\b(?:sk|rk|pk)-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b[A-Za-z0-9_=-]{32,}\b"),
    re.compile(r"\d{1,2}:\d{2}\s*/\s*\d{1,2}:\d{2}"),
    re.compile(r"\[?\s*若要退出全屏模式[^\n/]*\]?"),
    re.compile(r"\bEsc\b"),
    re.compile(r"\.{3,}"),
]

DEFAULT_BOOKMARK_KEYWORDS = [
    "ChatGPT",
    "Claude",
    "Netflix",
    "Nintendo",
    "哔哩哔哩",
    "哗哩哗哩",
    "哔哩哗哩",
]


@dataclass(frozen=True)
class CaptureTarget:
    bbox: tuple[int, int, int, int] | None
    label: str


class GLMCaptioner:
    def __init__(self, api_key: str | None, model: str, endpoint: str, prompt: str):
        if not api_key:
            raise ValueError("GLM_API_KEY is required for --caption-provider glm")
        self.api_key = api_key
        self.model = model
        self.endpoint = endpoint
        self.prompt = prompt

    def caption(self, image: Image.Image) -> str:
        import httpx

        image = shrink(image, max_side=1024)
        buf = BytesIO()
        image.convert("RGB").save(buf, format="JPEG", quality=85, optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")

        response = httpx.post(
            self.endpoint,
            json={
                "model": self.model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                            {"type": "text", "text": self.prompt},
                        ],
                    }
                ],
                "max_tokens": 160,
            },
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"unexpected GLM response: {data}") from exc
        return str(text).strip().strip("\"'。.")


class OcrEngine:
    def __init__(self):
        self.engine = self._create_engine()

    def _create_engine(self):
        try:
            from rapidocr_onnxruntime import RapidOCR

            return RapidOCR()
        except Exception as first_exc:
            try:
                from rapidocr import RapidOCR

                return RapidOCR()
            except Exception as second_exc:
                raise RuntimeError(
                    "RapidOCR is not available. Run install_macos.sh or pass --no-ocr."
                ) from second_exc

    def read(self, image: Image.Image, min_chars: int, min_score: float) -> list[str]:
        import numpy as np

        result = self.engine(np.array(image.convert("RGB")))
        if isinstance(result, tuple):
            rows = result[0]
        elif hasattr(result, "to_list"):
            rows = result.to_list()
        else:
            rows = result

        if not rows:
            return []

        items: list[tuple[float, str]] = []
        for row in rows:
            parsed = parse_ocr_row(row)
            if not parsed:
                continue
            bbox, text, score = parsed
            if score < min_score or len(text.strip()) < min_chars:
                continue
            y_top = min(point[1] for point in bbox)
            items.append((float(y_top), text.strip()))
        items.sort(key=lambda item: item[0])
        return [text for _, text in items]


def parse_ocr_row(row: Any) -> tuple[list[list[float]], str, float] | None:
    if isinstance(row, dict):
        bbox = row.get("bbox") or row.get("box") or row.get("dt_boxes")
        text = row.get("text") or row.get("rec_text") or row.get("txt")
        score = row.get("score") or row.get("rec_score") or 1.0
        if bbox and text:
            return bbox, str(text), float(score)
        return None

    if isinstance(row, (list, tuple)) and len(row) >= 3:
        bbox, text, score = row[0], row[1], row[2]
        return bbox, str(text), float(score)

    return None


def parse_region(value: str | None) -> tuple[int, int, int, int] | None:
    if not value:
        return None
    parts = [int(part.strip()) for part in value.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("--region must be x,y,width,height")
    x, y, width, height = parts
    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError("--region width and height must be positive")
    return x, y, x + width, y + height


def capture_target(window: str | None, region: str | None) -> CaptureTarget:
    bbox = parse_region(region)
    if bbox:
        return CaptureTarget(bbox=bbox, label=f"region:{region}")

    if window:
        found = find_macos_window(window)
        if found:
            return found
        print(f"[warn] window not found: {window!r}; falling back to fullscreen", file=sys.stderr)

    return CaptureTarget(bbox=None, label=window or "fullscreen")


def find_macos_window(needle: str) -> CaptureTarget | None:
    try:
        import Quartz
    except Exception:
        return None

    needle_lower = needle.lower()
    options = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    windows = Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID)
    matches = []
    for win in windows:
        owner = str(win.get("kCGWindowOwnerName", "") or "")
        title = str(win.get("kCGWindowName", "") or "")
        haystack = f"{owner} {title}".lower()
        if needle_lower not in haystack:
            continue
        bounds = win.get("kCGWindowBounds")
        if not bounds:
            continue
        area = int(bounds.get("Width", 0)) * int(bounds.get("Height", 0))
        if area > 0:
            matches.append((area, owner, title, bounds))

    if not matches:
        return None

    _, owner, title, bounds = max(matches, key=lambda item: item[0])
    scale = mac_screen_scale()
    x = int(float(bounds["X"]) * scale)
    y = int(float(bounds["Y"]) * scale)
    width = int(float(bounds["Width"]) * scale)
    height = int(float(bounds["Height"]) * scale)
    label = title or owner or needle
    return CaptureTarget(bbox=(x, y, x + width, y + height), label=label)


def mac_screen_scale() -> float:
    try:
        from AppKit import NSScreen

        screen = NSScreen.mainScreen()
        if screen:
            return float(screen.backingScaleFactor())
    except Exception:
        pass
    return 1.0


def screenshot(target: CaptureTarget) -> Image.Image:
    return ImageGrab.grab(bbox=target.bbox, all_screens=False)


def shrink(image: Image.Image, max_side: int) -> Image.Image:
    width, height = image.size
    if max(width, height) <= max_side:
        return image
    image = image.copy()
    image.thumbnail((max_side, max_side))
    return image


def clean_caption(text: str, bookmark_keywords: list[str], min_keep: int = 3) -> str | None:
    if not text:
        return None

    prefix = ""
    if text.startswith("[screen text] "):
        prefix, text = "[screen text] ", text[len("[screen text] "):]
    elif text.startswith("[屏上文字] "):
        prefix, text = "[屏上文字] ", text[len("[屏上文字] "):]

    for rx in NOISE_REGEXES:
        text = rx.sub("", text)
    for keyword in bookmark_keywords:
        keyword = keyword.strip()
        if keyword:
            text = text.replace(keyword, "")

    text = re.sub(r"(\s*/\s*)+", " / ", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" /\t\n")
    bare = re.sub(r"[/\s]+", "", text)
    if len(bare) < min_keep:
        return None
    return prefix + text


def make_entry(source: str, caption: str, target: CaptureTarget) -> dict[str, Any]:
    return {
        "source": source,
        "caption": caption,
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "window": target.label,
    }


def push_entry(
    entry: dict[str, Any],
    *,
    ssh_host: str | None,
    remote_command: str,
    dry_run: bool,
    retries: int,
) -> tuple[bool, str]:
    payload = json.dumps(entry, ensure_ascii=False)

    if dry_run:
        print(payload)
        return True, "dry_run"

    if not ssh_host:
        return False, "missing ssh host; set GAZE_SSH_HOST or --ssh-host"

    cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=8",
        ssh_host,
        remote_command,
    ]

    last_error = "not attempted"
    for attempt in range(retries + 1):
        try:
            result = subprocess.run(
                cmd,
                input=payload.encode("utf-8"),
                capture_output=True,
                timeout=20,
                check=False,
            )
        except Exception as exc:
            last_error = str(exc)[:160]
        else:
            if result.returncode == 0:
                return True, result.stdout.decode("utf-8", "replace").strip() or "OK"
            stderr = result.stderr.decode("utf-8", "replace").strip()
            stdout = result.stdout.decode("utf-8", "replace").strip()
            last_error = stderr or stdout or f"ssh exit {result.returncode}"

        if attempt < retries:
            time.sleep(0.5)

    return False, last_error


def env_bookmark_keywords() -> list[str]:
    extra = os.getenv("GAZE_BOOKMARK_KEYWORDS", "")
    return DEFAULT_BOOKMARK_KEYWORDS + [item.strip() for item in extra.split(",") if item.strip()]


def run(args: argparse.Namespace) -> int:
    load_dotenv()
    target = capture_target(args.window, args.region)
    bookmark_keywords = env_bookmark_keywords()

    ocr = None
    if not args.no_ocr:
        try:
            ocr = OcrEngine()
        except RuntimeError as exc:
            print(f"[warn] {exc}", file=sys.stderr)

    captioner = None
    if args.caption_provider == "glm":
        try:
            captioner = GLMCaptioner(
                api_key=os.getenv("GLM_API_KEY"),
                model=args.glm_model,
                endpoint=args.glm_endpoint,
                prompt=args.prompt,
            )
        except ValueError as exc:
            print(f"[warn] {exc}; vision captions disabled", file=sys.stderr)

    prev_texts: list[str] = []
    next_caption_time = 0.0

    while True:
        started = time.time()
        image = screenshot(target)

        if ocr:
            curr_texts = ocr.read(image, min_chars=args.ocr_min_chars, min_score=args.ocr_min_score)
            new_lines = [text for text in curr_texts if text not in prev_texts]
            if new_lines:
                joined = " / ".join(new_lines)[: args.max_ocr_chars]
                cleaned = clean_caption(f"[屏上文字] {joined}", bookmark_keywords)
                if cleaned:
                    entry = make_entry("ocr", cleaned, target)
                    ok, msg = push_entry(
                        entry,
                        ssh_host=args.ssh_host or os.getenv("GAZE_SSH_HOST"),
                        remote_command=args.remote_command or os.getenv("GAZE_REMOTE_COMMAND", DEFAULT_REMOTE_COMMAND),
                        dry_run=args.dry_run,
                        retries=args.retries,
                    )
                    print_status("OCR", cleaned, ok, msg)
            prev_texts = curr_texts

        if captioner and started >= next_caption_time:
            try:
                caption = captioner.caption(image)
                cleaned = clean_caption(caption, bookmark_keywords)
                if cleaned:
                    entry = make_entry("cap", cleaned, target)
                    ok, msg = push_entry(
                        entry,
                        ssh_host=args.ssh_host or os.getenv("GAZE_SSH_HOST"),
                        remote_command=args.remote_command or os.getenv("GAZE_REMOTE_COMMAND", DEFAULT_REMOTE_COMMAND),
                        dry_run=args.dry_run,
                        retries=args.retries,
                    )
                    print_status("CAP", cleaned, ok, msg)
            except Exception as exc:
                print(f"[CAP] error: {exc}", file=sys.stderr)
            next_caption_time = time.time() + args.caption_interval

        if args.once:
            return 0

        elapsed = time.time() - started
        time.sleep(max(0.5, args.ocr_interval - elapsed))


def print_status(kind: str, text: str, ok: bool, msg: str) -> None:
    arrow = "->" if ok else "x"
    print(f"[{datetime.now():%H:%M:%S}] [{kind}] {text[:80]} {arrow} {msg}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Mac gaze client for realtime screen captions.")
    parser.add_argument("--window", "-w", help="Fuzzy match a macOS window/app name.")
    parser.add_argument("--region", help="Capture x,y,width,height instead of full screen.")
    parser.add_argument("--caption-provider", choices=["glm", "none"], default="glm")
    parser.add_argument("--glm-model", default="glm-4v-flash")
    parser.add_argument("--glm-endpoint", default="https://open.bigmodel.cn/api/paas/v4/chat/completions")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--no-ocr", action="store_true")
    parser.add_argument("--ocr-interval", type=float, default=3.0)
    parser.add_argument("--caption-interval", type=float, default=10.0)
    parser.add_argument("--ocr-min-score", type=float, default=0.6)
    parser.add_argument("--ocr-min-chars", type=int, default=3)
    parser.add_argument("--max-ocr-chars", type=int, default=200)
    parser.add_argument("--ssh-host")
    parser.add_argument("--remote-command")
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--dry-run", action="store_true", help="Print payloads instead of SSH pushing them.")
    parser.add_argument("--once", action="store_true", help="Capture one cycle, then exit.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return run(args)
    except KeyboardInterrupt:
        print("\nstopped")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
