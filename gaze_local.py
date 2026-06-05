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
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageGrab


DEFAULT_PROMPT = """看截图，像跟朋友吐槽一样发一条弹幕。
要求：
- 不评价身材、颜值、性别气质，不使用"美女""小姐姐"等凝视词
- 如果有可读文字，优先概括文字内容
- 描述用中性词，例如衣服颜色、窗口状态、画面动作
- 不输出账号、URL、token、邮箱、路径等隐私细节
- 40字以内

输出一句:"""

DEFAULT_REMOTE_COMMAND = "python3 /root/mcp-memory-server/push_caption.py"
PUSH_IDLE = {"empty", "waiting"}
MASK_PRESETS = ("menu-bar", "browser-top", "dock-bottom", "mac-safe")
BROWSER_APP_HINTS = (
    "arc",
    "brave",
    "chromium",
    "dia",
    "firefox",
    "google chrome",
    "microsoft edge",
    "opera",
    "safari",
    "vivaldi",
)
SYSTEM_WINDOW_OWNERS = {
    "Control Center",
    "Dock",
    "Notification Center",
    "SystemUIServer",
    "Window Server",
}

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

DEFAULT_WINDOW_BLACKLIST = [
    "Messages",
    "信息",
    "Mail",
    "邮件",
    "Outlook",
    "微信",
    "WeChat",
    "QQ",
    "TIM",
    "Telegram",
    "Discord",
    "Signal",
    "LINE",
    "Slack",
    "支付宝",
    "Alipay",
    "Wallet",
    "银行",
    "Bank",
    "1Password",
    "Bitwarden",
    "KeePass",
    "LastPass",
    "Keychain Access",
    "Terminal",
    "iTerm",
    "Warp",
    "Visual Studio Code",
    "Cursor",
    "gaze_local",
    "gaze launcher",
]

CONSOLE_NOISE_TERMS = [
    "gaze_local.py",
    "gaze_launcher.py",
    "python gaze",
    "python3 gaze",
    "--caption-provider",
    "--ocr-interval",
    "--mask-preset",
    "PYTHONUNBUFFERED",
    "Traceback (most recent call last)",
    "File \"",
    "zsh:",
    "bash:",
    "curl ",
    "ssh ",
    "scp ",
    "git ",
    "npm ",
    "node_modules",
    "localhost:",
    "127.0.0.1",
]

APP_KEY_NORMALIZERS = [
    ("Google Chrome", "Chrome"),
    ("Microsoft Edge", "Edge"),
    ("Mozilla Firefox", "Firefox"),
    ("Firefox", "Firefox"),
    ("Safari", "Safari"),
    ("Arc", "Arc"),
    ("Brave", "Brave"),
    ("Visual Studio Code", "VSCode"),
    ("Cursor", "Cursor"),
    ("Spotify", "Spotify"),
    ("Bilibili", "Bilibili"),
    ("哔哩哔哩", "Bilibili"),
    ("Doki Doki", "DDLC"),
    ("Disco Elysium", "Disco Elysium"),
]


@dataclass(frozen=True)
class CaptureTarget:
    bbox: tuple[int, int, int, int] | None
    label: str
    app_name: str | None = None
    window_id: int | None = None


class GLMCaptioner:
    def __init__(self, api_key: str | None, model: str, endpoint: str, prompt: str):
        if not api_key:
            raise ValueError("GLM_API_KEY is required for --caption-provider glm")
        self.api_key = api_key
        self.model = model
        self.endpoint = endpoint
        self.prompt = prompt

    def caption(self, image: Image.Image, recent_ocr: list[str] | None = None) -> str:
        import httpx

        image = shrink(image, max_side=1024)
        buf = BytesIO()
        image.convert("RGB").save(buf, format="JPEG", quality=85, optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        prompt = prompt_with_ocr_context(self.prompt, recent_ocr)

        response = httpx.post(
            self.endpoint,
            json={
                "model": self.model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                            {"type": "text", "text": prompt},
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


class MockCaptioner:
    def caption(self, image: Image.Image, recent_ocr: list[str] | None = None) -> str:
        width, height = image.size
        ocr_note = f"，已有OCR {len(recent_ocr)}条" if recent_ocr else ""
        return f"[mock vision] 当前帧 {width}x{height}{ocr_note}"


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


def capture_target(
    window: str | None,
    region: str | None,
    *,
    strict_window: bool = False,
    allow_fullscreen_fallback: bool = False,
) -> CaptureTarget:
    bbox = parse_region(region)
    if bbox:
        return CaptureTarget(bbox=bbox, label=f"region:{region}")

    if window:
        found = find_macos_window(window)
        if found:
            return found
        if strict_window or not allow_fullscreen_fallback:
            raise RuntimeError(f"window not found: {window!r}")
        print(f"[warn] window not found: {window!r}; falling back to fullscreen", file=sys.stderr)
        return CaptureTarget(bbox=None, label="fullscreen:fallback")

    return CaptureTarget(bbox=None, label=window or "fullscreen")


def current_capture_target(args: argparse.Namespace, fallback: CaptureTarget) -> CaptureTarget | None:
    if args.region:
        return fallback
    if args.follow_active_window:
        found = find_active_macos_window()
        if found:
            return found
        if not args.allow_fullscreen_fallback:
            if args.verbose:
                print(
                    "[warn] active window not found; skipping frame "
                    "(use --allow-fullscreen-fallback to capture fullscreen)",
                    file=sys.stderr,
                )
            return None
        if args.verbose:
            print("[warn] active window not found; using fallback target", file=sys.stderr)
    return fallback


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
            window_id = int(win.get("kCGWindowNumber", 0) or 0) or None
            matches.append((area, owner, title, bounds, window_id))

    if not matches:
        return None

    _, owner, title, bounds, window_id = max(matches, key=lambda item: item[0])
    return target_from_window_bounds(bounds, title or owner or needle, app_name=owner or None, window_id=window_id)


def find_active_macos_window() -> CaptureTarget | None:
    try:
        import Quartz
    except Exception:
        return None

    frontmost = frontmost_app_name()
    if not frontmost:
        return None

    options = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    windows = Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID)
    candidates = []

    for win in windows:
        owner = str(win.get("kCGWindowOwnerName", "") or "")
        title = str(win.get("kCGWindowName", "") or "")
        layer = int(win.get("kCGWindowLayer", 0) or 0)
        alpha = float(win.get("kCGWindowAlpha", 1.0) or 0.0)
        bounds = win.get("kCGWindowBounds")
        if owner != frontmost or owner in SYSTEM_WINDOW_OWNERS:
            continue
        if layer != 0 or alpha <= 0 or not bounds:
            continue
        width = int(bounds.get("Width", 0) or 0)
        height = int(bounds.get("Height", 0) or 0)
        area = width * height
        if width < 80 or height < 80 or area < 20_000:
            continue
        window_id = int(win.get("kCGWindowNumber", 0) or 0) or None
        candidates.append((area, owner, title, bounds, window_id))

    if not candidates:
        return None

    _, owner, title, bounds, window_id = max(candidates, key=lambda item: item[0])
    return target_from_window_bounds(bounds, title or owner, app_name=owner or None, window_id=window_id)


def frontmost_app_name() -> str | None:
    try:
        from AppKit import NSWorkspace
    except Exception:
        return None

    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if not app:
        return None
    name = app.localizedName()
    return str(name) if name else None


def target_from_window_bounds(
    bounds: Any,
    label: str,
    app_name: str | None = None,
    window_id: int | None = None,
) -> CaptureTarget:
    scale = mac_screen_scale()
    x = int(float(bounds["X"]) * scale)
    y = int(float(bounds["Y"]) * scale)
    width = int(float(bounds["Width"]) * scale)
    height = int(float(bounds["Height"]) * scale)
    return CaptureTarget(
        bbox=(x, y, x + width, y + height),
        label=normalize_window_label(label, app_name),
        app_name=app_name,
        window_id=window_id,
    )


def normalize_window_label(label: str | None, app_name: str | None = None) -> str:
    haystack = f"{app_name or ''} {label or ''}".strip()
    if not haystack:
        return "fullscreen"
    for needle, key in APP_KEY_NORMALIZERS:
        if needle.lower() in haystack.lower():
            return key
    return (label or app_name or "fullscreen")[:40].strip() or "fullscreen"


def csv_env_list(name: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, "").split(",") if item.strip()]


def env_window_blacklist() -> list[str]:
    return DEFAULT_WINDOW_BLACKLIST + csv_env_list("GAZE_WINDOW_BLACKLIST")


def is_target_blacklisted(target: CaptureTarget, blacklist: list[str]) -> bool:
    haystack = f"{target.app_name or ''} {target.label or ''}".lower()
    return any(keyword.lower() in haystack for keyword in blacklist if keyword.strip())


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
    if target.window_id:
        return screenshot_window(target.window_id)
    return ImageGrab.grab(bbox=target.bbox, all_screens=False)


def screenshot_window(window_id: int) -> Image.Image:
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        path = tmp.name
    try:
        result = subprocess.run(
            ["screencapture", "-x", "-l", str(window_id), path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", "replace").strip()
            raise RuntimeError(stderr or f"screencapture exited {result.returncode}")
        with Image.open(path) as image:
            return image.copy()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def apply_masks(image: Image.Image, presets: list[str], rect_values: list[str]) -> Image.Image:
    if not presets and not rect_values:
        return image

    masked = image.copy()
    draw = ImageDraw.Draw(masked)
    width, height = masked.size

    for preset in presets:
        for rect in preset_rects(preset, width, height):
            draw.rectangle(rect, fill=(0, 0, 0))

    for value in rect_values:
        rect = parse_mask_rect(value, width, height)
        draw.rectangle(rect, fill=(0, 0, 0))

    return masked


def effective_mask_presets(target: CaptureTarget, presets: list[str], auto_mask: bool) -> list[str]:
    if not auto_mask:
        return list(presets)
    return unique_presets([*presets, *auto_mask_presets(target)])


def auto_mask_presets(target: CaptureTarget) -> list[str]:
    app_text = (target.app_name or "").lower()
    if any(hint in app_text for hint in BROWSER_APP_HINTS):
        return ["browser-top"]
    return []


def unique_presets(presets: list[str]) -> list[str]:
    seen = set()
    result = []
    for preset in presets:
        if preset in seen:
            continue
        seen.add(preset)
        result.append(preset)
    return result


def preset_rects(name: str, width: int, height: int) -> list[tuple[int, int, int, int]]:
    top_menu = min(36, height)
    browser_top = min(140, height)
    dock = min(120, height)

    if name == "menu-bar":
        return [(0, 0, width, top_menu)]
    if name == "browser-top":
        return [(0, 0, width, browser_top)]
    if name == "dock-bottom":
        return [(0, max(0, height - dock), width, height)]
    if name == "mac-safe":
        return [(0, 0, width, min(88, height)), (0, max(0, height - dock), width, height)]
    raise ValueError(f"unknown mask preset: {name}")


def parse_mask_rect(value: str, image_width: int, image_height: int) -> tuple[int, int, int, int]:
    left, top, right, bottom = parse_region(value)
    if left < 0:
        right = image_width + right
        left = image_width + left
    if top < 0:
        bottom = image_height + bottom
        top = image_height + top
    return clip_rect((left, top, right, bottom), image_width, image_height)


def clip_rect(rect: tuple[int, int, int, int], width: int, height: int) -> tuple[int, int, int, int]:
    left, top, right, bottom = rect
    left = max(0, min(width, left))
    right = max(0, min(width, right))
    top = max(0, min(height, top))
    bottom = max(0, min(height, bottom))
    if right < left:
        left, right = right, left
    if bottom < top:
        top, bottom = bottom, top
    return left, top, right, bottom


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

    if is_console_noise(text):
        return None
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
    if is_console_noise(text):
        return None
    return prefix + text


def is_console_noise(text: str) -> bool:
    lower = text.lower()
    return any(term.lower() in lower for term in CONSOLE_NOISE_TERMS)


def prompt_with_ocr_context(prompt: str, recent_ocr: list[str] | None) -> str:
    if not recent_ocr:
        return prompt
    ocr_context = "\n".join(f"- {line}" for line in recent_ocr[-6:] if line.strip())
    if not ocr_context:
        return prompt
    return (
        "OCR has already captured these screen-text snippets. "
        "Do not repeat them unless an important visual detail depends on them.\n"
        f"{ocr_context}\n\n---\n\n{prompt}"
    )


def make_entry(source: str, caption: str, target: CaptureTarget) -> dict[str, Any]:
    entry = {
        "source": source,
        "caption": caption,
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "window": target.label,
    }
    if target.app_name:
        entry["app"] = target.app_name
    return entry


class EntryPusher:
    def __init__(
        self,
        *,
        ssh_host: str | None,
        remote_command: str,
        dry_run: bool,
        retries: int,
        batch_interval: float,
        max_batch: int,
        max_queue: int,
    ):
        self.ssh_host = ssh_host
        self.remote_command = remote_command
        self.dry_run = dry_run
        self.retries = retries
        self.batch_interval = max(0.0, batch_interval)
        self.max_batch = max(1, max_batch)
        self.max_queue = max(self.max_batch, max_queue)
        self.queue: list[dict[str, Any]] = []
        self.next_flush_time = time.time() + self.batch_interval

    def enqueue(self, entry: dict[str, Any]) -> tuple[bool, str]:
        self.queue.append(entry)
        dropped = 0
        if len(self.queue) > self.max_queue:
            dropped = len(self.queue) - self.max_queue
            del self.queue[:dropped]

        if self.batch_interval == 0 or len(self.queue) >= self.max_batch:
            ok, msg = self.flush(force=True)
        else:
            ok, msg = True, f"queued {len(self.queue)}"

        if dropped:
            msg = f"{msg}; dropped_oldest={dropped}"
        return ok, msg

    def maybe_flush(self) -> tuple[bool, str]:
        if not self.queue:
            return True, "empty"
        if time.time() < self.next_flush_time:
            return True, "waiting"
        return self.flush(force=True)

    def flush(self, *, force: bool = False) -> tuple[bool, str]:
        if not self.queue:
            return True, "empty"
        if not force and time.time() < self.next_flush_time:
            return True, f"queued {len(self.queue)}"

        entries = list(self.queue)
        payload = json.dumps(entries[0] if len(entries) == 1 else entries, ensure_ascii=False)
        ok, msg = send_payload(
            payload,
            ssh_host=self.ssh_host,
            remote_command=self.remote_command,
            dry_run=self.dry_run,
            retries=self.retries,
        )
        self.next_flush_time = time.time() + self.batch_interval
        if ok:
            del self.queue[: len(entries)]
            return True, f"pushed {len(entries)}: {msg}"
        return False, f"push failed for {len(entries)} queued: {msg}"


def send_payload(
    payload: str,
    *,
    ssh_host: str | None,
    remote_command: str,
    dry_run: bool,
    retries: int,
) -> tuple[bool, str]:

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


def image_signature(image: Image.Image) -> bytes:
    return image.convert("L").resize((32, 32)).tobytes()


def image_diff_score(left: bytes | None, right: bytes) -> float:
    if left is None:
        return 255.0
    if len(left) != len(right):
        return 255.0
    return sum(abs(a - b) for a, b in zip(left, right)) / len(right)


def env_bookmark_keywords() -> list[str]:
    return DEFAULT_BOOKMARK_KEYWORDS + csv_env_list("GAZE_BOOKMARK_KEYWORDS")


def load_prompt(args: argparse.Namespace) -> str:
    prompt_file = args.prompt_file or os.getenv("GAZE_PROMPT_FILE")
    if prompt_file:
        return Path(prompt_file).expanduser().read_text(encoding="utf-8").strip()
    return args.prompt or os.getenv("GAZE_PROMPT") or DEFAULT_PROMPT


def run(args: argparse.Namespace) -> int:
    load_dotenv()
    try:
        fallback_target = capture_target(
            args.window,
            args.region,
            strict_window=args.strict_window,
            allow_fullscreen_fallback=args.allow_fullscreen_fallback,
        )
    except RuntimeError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2
    bookmark_keywords = env_bookmark_keywords()
    window_blacklist = env_window_blacklist()
    prompt = load_prompt(args)
    pusher = EntryPusher(
        ssh_host=args.ssh_host or os.getenv("GAZE_SSH_HOST"),
        remote_command=args.remote_command or os.getenv("GAZE_REMOTE_COMMAND", DEFAULT_REMOTE_COMMAND),
        dry_run=args.dry_run,
        retries=args.retries,
        batch_interval=args.batch_interval,
        max_batch=args.max_batch,
        max_queue=args.max_queue,
    )

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
                prompt=prompt,
            )
        except ValueError as exc:
            print(f"[warn] {exc}; vision captions disabled", file=sys.stderr)
    elif args.caption_provider == "mock":
        captioner = MockCaptioner()

    prev_texts: list[str] = []
    prev_caption_signature: bytes | None = None
    next_caption_time = 0.0
    last_target_key: tuple[str, tuple[int, int, int, int] | None, str | None] | None = None
    last_mask_key: tuple[str, ...] | None = None

    while True:
        started = time.time()
        target = current_capture_target(args, fallback_target)
        if target is None:
            if args.once:
                return 0
            time.sleep(max(0.5, args.ocr_interval))
            continue
        if is_target_blacklisted(target, window_blacklist):
            if args.verbose:
                print(f"[privacy] skipped blacklisted window: {target.label}", file=sys.stderr)
            if args.once:
                return 0
            time.sleep(max(0.5, args.ocr_interval))
            continue
        target_key = (target.label, target.bbox, target.app_name)
        mask_presets = effective_mask_presets(target, args.mask_preset, args.auto_mask)
        mask_key = tuple(mask_presets)
        if target_key != last_target_key:
            prev_texts = []
            prev_caption_signature = None
            last_target_key = target_key
            if args.verbose:
                print(f"[target] {target.label}")
        if args.verbose and args.auto_mask and mask_key != last_mask_key:
            last_mask_key = mask_key
            auto_presets = auto_mask_presets(target)
            if auto_presets:
                print(f"[mask] auto {target.app_name or target.label}: {','.join(auto_presets)}")
        image = apply_masks(screenshot(target), mask_presets, args.mask_rect)

        if ocr:
            curr_texts = ocr.read(image, min_chars=args.ocr_min_chars, min_score=args.ocr_min_score)
            new_lines = [text for text in curr_texts if text not in prev_texts]
            if new_lines:
                joined = " / ".join(new_lines)[: args.max_ocr_chars]
                cleaned = clean_caption(f"[屏上文字] {joined}", bookmark_keywords)
                if cleaned:
                    entry = make_entry("ocr", cleaned, target)
                    ok, msg = pusher.enqueue(entry)
                    print_status("OCR", cleaned, ok, msg)
            prev_texts = curr_texts

        if captioner and started >= next_caption_time:
            signature = image_signature(image)
            diff_score = image_diff_score(prev_caption_signature, signature)
            try:
                if args.vision_min_diff <= 0 or diff_score >= args.vision_min_diff:
                    caption = captioner.caption(image, recent_ocr=prev_texts[-6:] if prev_texts else None)
                    cleaned = clean_caption(caption, bookmark_keywords)
                    if cleaned:
                        entry = make_entry("cap", cleaned, target)
                        ok, msg = pusher.enqueue(entry)
                        print_status("CAP", cleaned, ok, msg)
                    prev_caption_signature = signature
                elif args.verbose:
                    print(f"[CAP] skipped unchanged frame diff={diff_score:.2f}")
            except Exception as exc:
                print(f"[CAP] error: {exc}", file=sys.stderr)
            next_caption_time = time.time() + args.caption_interval

        ok, msg = pusher.maybe_flush()
        if msg not in PUSH_IDLE:
            print_status("PUSH", msg, ok, "")

        if args.once:
            ok, msg = pusher.flush(force=True)
            if msg not in PUSH_IDLE:
                print_status("PUSH", msg, ok, "")
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
    parser.add_argument(
        "--strict-window",
        action="store_true",
        help="Compatibility alias for the default narrow behavior: fail when --window is not found.",
    )
    parser.add_argument(
        "--allow-fullscreen-fallback",
        action="store_true",
        help="Wide door: allow --window or --follow-active-window to fall back to fullscreen.",
    )
    parser.add_argument(
        "--follow-active-window",
        action="store_true",
        help="Capture the current foreground macOS window each loop. Ignored when --region is set.",
    )
    parser.add_argument(
        "--mask-preset",
        action="append",
        choices=MASK_PRESETS,
        default=[],
        help="Black out common privacy zones before OCR/vision. Can be repeated.",
    )
    parser.add_argument(
        "--auto-mask",
        action="store_true",
        help="Add app-aware privacy masks, such as browser chrome masking for browser windows.",
    )
    parser.add_argument(
        "--mask-rect",
        action="append",
        default=[],
        help="Black out x,y,width,height before OCR/vision. Negative x/y count from right/bottom.",
    )
    parser.add_argument("--caption-provider", choices=["glm", "mock", "none"], default="glm")
    parser.add_argument("--glm-model", default="glm-4v-flash")
    parser.add_argument("--glm-endpoint", default="https://open.bigmodel.cn/api/paas/v4/chat/completions")
    parser.add_argument("--prompt", help="Inline vision prompt. Overrides GAZE_PROMPT and the built-in default.")
    parser.add_argument("--prompt-file", help="Read the vision prompt from a UTF-8 text file. Overrides --prompt.")
    parser.add_argument("--no-ocr", action="store_true")
    parser.add_argument("--ocr-interval", type=float, default=3.0)
    parser.add_argument("--caption-interval", type=float, default=10.0)
    parser.add_argument("--vision-min-diff", type=float, default=3.0, help="Skip vision if 32x32 mean diff is below this. Use 0 to disable.")
    parser.add_argument("--ocr-min-score", type=float, default=0.6)
    parser.add_argument("--ocr-min-chars", type=int, default=3)
    parser.add_argument("--max-ocr-chars", type=int, default=200)
    parser.add_argument("--ssh-host")
    parser.add_argument("--remote-command")
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--batch-interval", type=float, default=2.0, help="Seconds to batch entries before one SSH push. Use 0 for immediate push.")
    parser.add_argument("--max-batch", type=int, default=8)
    parser.add_argument("--max-queue", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true", help="Print payloads instead of SSH pushing them.")
    parser.add_argument("--once", action="store_true", help="Capture one cycle, then exit.")
    parser.add_argument("--verbose", action="store_true")
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
