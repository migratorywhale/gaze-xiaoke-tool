#!/usr/bin/env python3
"""Dependency-free browser launcher for gaze_local.py."""

from __future__ import annotations

import argparse
import html
import json
import queue
import socket
import subprocess
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

from gaze_launcher import LauncherConfig, MASK_PRESETS, build_command, command_string


class LauncherState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.process: subprocess.Popen[str] | None = None
        self.output: list[str] = []
        self.command = ""
        self.status = "Ready. Default is dry-run."
        self.output_queue: queue.Queue[str] = queue.Queue()

    def append_output(self, text: str) -> None:
        with self.lock:
            self.output.append(text)
            self.output = self.output[-400:]

    def snapshot(self) -> dict[str, object]:
        self.drain_queue()
        with self.lock:
            running = self.process is not None and self.process.poll() is None
            return {
                "running": running,
                "status": self.status,
                "command": self.command,
                "output": "".join(self.output[-200:]),
            }

    def drain_queue(self) -> None:
        while True:
            try:
                line = self.output_queue.get_nowait()
            except queue.Empty:
                break
            with self.lock:
                self.output.append(line)
                self.output = self.output[-400:]
                if line.startswith("[process exited:"):
                    self.status = line.strip("[]\n")

    def start(self, config: LauncherConfig, *, once: bool) -> tuple[bool, str]:
        self.drain_queue()
        with self.lock:
            if self.process and self.process.poll() is None:
                return False, "gaze is already running"
            cmd = build_command(config, once=once)
            self.command = command_string(cmd)
            self.output.append(f"$ {self.command}\n")
            self.status = "Running once..." if once else "Running."
            try:
                self.process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
            except Exception as exc:
                self.status = f"Start failed: {exc}"
                return False, self.status
            process = self.process

        threading.Thread(target=self._read_output, args=(process,), daemon=True).start()
        return True, self.status

    def _read_output(self, process: subprocess.Popen[str]) -> None:
        assert process.stdout
        for line in process.stdout:
            self.output_queue.put(line)
        code = process.wait()
        self.output_queue.put(f"[process exited: {code}]\n")

    def stop(self) -> tuple[bool, str]:
        with self.lock:
            if not self.process or self.process.poll() is not None:
                self.status = "Not running."
                return False, self.status
            self.process.terminate()
            self.status = "Stopping..."
            return True, self.status


def make_handler(state: LauncherState):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/" or self.path.startswith("/?"):
                self.send_html(render_page())
                return
            if self.path == "/status":
                self.send_json(state.snapshot())
                return
            self.send_error(404)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length).decode("utf-8", "replace")
            form = parse_qs(raw)

            if self.path == "/start":
                ok, message = state.start(config_from_form(form), once=False)
                self.send_json({"ok": ok, "message": message})
                return
            if self.path == "/run-once":
                ok, message = state.start(config_from_form(form), once=True)
                self.send_json({"ok": ok, "message": message})
                return
            if self.path == "/stop":
                ok, message = state.stop()
                self.send_json({"ok": ok, "message": message})
                return
            if self.path == "/command":
                cmd = build_command(config_from_form(form))
                self.send_json({"command": command_string(cmd)})
                return
            self.send_error(404)

        def send_html(self, body: str) -> None:
            data = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def send_json(self, payload: dict[str, object]) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args) -> None:
            return

    return Handler


def config_from_form(form: dict[str, list[str]]) -> LauncherConfig:
    return LauncherConfig(
        target_mode=single(form, "target_mode", "follow"),
        window=single(form, "window", ""),
        region=single(form, "region", ""),
        dry_run=truthy(form, "dry_run"),
        auto_mask=truthy(form, "auto_mask"),
        mask_presets=form.get("mask_presets", ["mac-safe"]),
        no_ocr=truthy(form, "no_ocr"),
        caption_provider=single(form, "caption_provider", "none"),
        ocr_interval=single(form, "ocr_interval", "3"),
        caption_interval=single(form, "caption_interval", "10"),
        batch_interval=single(form, "batch_interval", "2"),
        extra_args=single(form, "extra_args", ""),
    )


def single(form: dict[str, list[str]], key: str, default: str) -> str:
    values = form.get(key)
    return values[0] if values else default


def truthy(form: dict[str, list[str]], key: str) -> bool:
    return single(form, key, "") in {"1", "true", "on", "yes"}


def render_page() -> str:
    mask_boxes = "\n".join(
        f'<label><input type="checkbox" name="mask_presets" value="{html.escape(name)}" {"checked" if name == "mac-safe" else ""}> {html.escape(name)}</label>'
        for name in MASK_PRESETS
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>gaze launcher</title>
  <style>
    body {{ font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; color: #1f2328; }}
    main {{ max-width: 920px; margin: 0 auto; }}
    fieldset {{ border: 1px solid #d0d7de; border-radius: 8px; padding: 14px; margin: 12px 0; }}
    legend {{ font-weight: 700; }}
    label {{ display: inline-flex; gap: 6px; align-items: center; margin: 6px 14px 6px 0; }}
    input[type=text], select {{ min-width: 180px; padding: 6px 8px; border: 1px solid #d0d7de; border-radius: 6px; }}
    .wide {{ min-width: min(620px, 92vw); }}
    button {{ padding: 8px 12px; border: 1px solid #8c959f; border-radius: 6px; background: #f6f8fa; cursor: pointer; }}
    button.primary {{ background: #0969da; color: white; border-color: #0969da; }}
    pre {{ background: #0d1117; color: #e6edf3; border-radius: 8px; padding: 12px; overflow: auto; min-height: 160px; }}
    code {{ word-break: break-all; }}
    .status {{ font-weight: 700; margin: 10px 0; }}
  </style>
</head>
<body>
<main>
  <h1>gaze launcher</h1>
  <form id="config">
    <fieldset>
      <legend>Target</legend>
      <label><input type="radio" name="target_mode" value="follow" checked> Follow active window</label>
      <label><input type="radio" name="target_mode" value="window"> Window title</label>
      <input type="text" name="window" placeholder="Claude">
      <label><input type="radio" name="target_mode" value="region"> Region</label>
      <input type="text" name="region" placeholder="0,0,1200,760">
    </fieldset>
    <fieldset>
      <legend>Privacy</legend>
      <label><input type="checkbox" name="dry_run" checked> Dry-run</label>
      <label><input type="checkbox" name="auto_mask" checked> Auto-mask</label>
      {mask_boxes}
    </fieldset>
    <fieldset>
      <legend>Channels</legend>
      <label><input type="checkbox" name="no_ocr"> Disable OCR</label>
      <label>Vision <select name="caption_provider"><option value="none" selected>none</option><option value="mock">mock</option><option value="glm">glm</option></select></label>
    </fieldset>
    <fieldset>
      <legend>Timing</legend>
      <label>OCR interval <input type="text" name="ocr_interval" value="3"></label>
      <label>Caption interval <input type="text" name="caption_interval" value="10"></label>
      <label>Batch interval <input type="text" name="batch_interval" value="2"></label>
    </fieldset>
    <fieldset>
      <legend>Extra Args</legend>
      <input class="wide" type="text" name="extra_args" placeholder="--verbose">
    </fieldset>
  </form>
  <p>
    <button onclick="refreshCommand()">Refresh</button>
    <button onclick="runOnce()" class="primary">Run Once</button>
    <button onclick="start()">Start</button>
    <button onclick="stop()">Stop</button>
  </p>
  <div class="status" id="status">Ready.</div>
  <p><code id="command"></code></p>
  <pre id="output"></pre>
</main>
<script>
const form = document.getElementById('config');
async function post(path) {{
  const response = await fetch(path, {{ method: 'POST', body: new URLSearchParams(new FormData(form)) }});
  return response.json();
}}
async function refreshCommand() {{
  const data = await post('/command');
  document.getElementById('command').textContent = data.command || '';
}}
async function runOnce() {{ await post('/run-once'); await poll(); }}
async function start() {{ await post('/start'); await poll(); }}
async function stop() {{ await post('/stop'); await poll(); }}
async function poll() {{
  const response = await fetch('/status');
  const data = await response.json();
  document.getElementById('status').textContent = data.status + (data.running ? ' Running' : '');
  document.getElementById('command').textContent = data.command || document.getElementById('command').textContent;
  document.getElementById('output').textContent = data.output || '';
}}
form.addEventListener('input', refreshCommand);
refreshCommand();
setInterval(poll, 1000);
</script>
</body>
</html>"""


def find_port(start: int) -> int:
    for port in range(start, start + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("no free localhost port found")


def launch_web(port: int = 8765, *, open_browser: bool = True) -> None:
    state = LauncherState()
    port = find_port(port)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(state))
    url = f"http://127.0.0.1:{port}/"
    print(f"gaze web launcher: {url}")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        state.stop()
        server.server_close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Browser launcher for gaze_local.py")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    launch_web(args.port, open_browser=not args.no_browser)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
