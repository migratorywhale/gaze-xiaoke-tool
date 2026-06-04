#!/usr/bin/env python3
"""Small Tkinter launcher for gaze_local.py."""

from __future__ import annotations

import argparse
import queue
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parent
GAZE_LOCAL = ROOT / "gaze_local.py"
MASK_PRESETS = ("mac-safe", "browser-top", "dock-bottom", "menu-bar")


@dataclass
class LauncherConfig:
    target_mode: str = "follow"
    window: str = ""
    region: str = ""
    dry_run: bool = True
    auto_mask: bool = True
    mask_presets: list[str] = field(default_factory=lambda: ["mac-safe"])
    no_ocr: bool = False
    caption_provider: str = "none"
    ocr_interval: str = "3"
    caption_interval: str = "10"
    batch_interval: str = "2"
    extra_args: str = ""


def build_command(config: LauncherConfig, *, once: bool = False) -> list[str]:
    cmd = [sys.executable, str(GAZE_LOCAL)]

    if config.target_mode == "follow":
        cmd.append("--follow-active-window")
    elif config.target_mode == "window" and config.window.strip():
        cmd.extend(["--window", config.window.strip()])
    elif config.target_mode == "region" and config.region.strip():
        cmd.extend(["--region", config.region.strip()])

    if config.dry_run:
        cmd.append("--dry-run")
    if config.auto_mask:
        cmd.append("--auto-mask")
    for preset in config.mask_presets:
        cmd.extend(["--mask-preset", preset])
    if config.no_ocr:
        cmd.append("--no-ocr")

    cmd.extend(["--caption-provider", config.caption_provider])
    add_numeric_arg(cmd, "--ocr-interval", config.ocr_interval)
    add_numeric_arg(cmd, "--caption-interval", config.caption_interval)
    add_numeric_arg(cmd, "--batch-interval", config.batch_interval)

    if once:
        cmd.append("--once")
    cmd.extend(split_extra_args(config.extra_args))
    return cmd


def add_numeric_arg(cmd: list[str], flag: str, value: str) -> None:
    value = value.strip()
    if value:
        cmd.extend([flag, value])


def split_extra_args(value: str) -> list[str]:
    if not value.strip():
        return []
    import shlex

    return shlex.split(value)


def command_string(cmd: Iterable[str]) -> str:
    import shlex

    return " ".join(shlex.quote(part) for part in cmd)


def main() -> int:
    parser = argparse.ArgumentParser(description="Tkinter launcher for gaze_local.py")
    parser.add_argument("--print-default-command", action="store_true")
    args = parser.parse_args()

    if args.print_default_command:
        print(command_string(build_command(LauncherConfig())))
        return 0

    launch_gui()
    return 0


def launch_gui() -> None:
    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.title("gaze launcher")
    root.geometry("840x620")

    app = GazeLauncher(root, tk, ttk, messagebox)
    app.pack(fill="both", expand=True)
    root.protocol("WM_DELETE_WINDOW", app.close)
    root.mainloop()


class GazeLauncher:
    def __init__(self, root, tk, ttk, messagebox):
        self.root = root
        self.tk = tk
        self.ttk = ttk
        self.messagebox = messagebox
        self.process: subprocess.Popen[str] | None = None
        self.output_queue: queue.Queue[str] = queue.Queue()

        self.frame = ttk.Frame(root, padding=12)
        self.target_mode = tk.StringVar(value="follow")
        self.window = tk.StringVar()
        self.region = tk.StringVar()
        self.dry_run = tk.BooleanVar(value=True)
        self.auto_mask = tk.BooleanVar(value=True)
        self.mask_vars = {name: tk.BooleanVar(value=name == "mac-safe") for name in MASK_PRESETS}
        self.no_ocr = tk.BooleanVar(value=False)
        self.caption_provider = tk.StringVar(value="none")
        self.ocr_interval = tk.StringVar(value="3")
        self.caption_interval = tk.StringVar(value="10")
        self.batch_interval = tk.StringVar(value="2")
        self.extra_args = tk.StringVar()
        self.command_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready. Default is dry-run.")

        self.build_ui()
        self.refresh_command()
        self.poll_output()

    def pack(self, **kwargs) -> None:
        self.frame.pack(**kwargs)

    def build_ui(self) -> None:
        ttk = self.ttk
        tk = self.tk

        title = ttk.Label(self.frame, text="gaze launcher", font=("", 18, "bold"))
        title.grid(row=0, column=0, columnspan=4, sticky="w")

        target = ttk.LabelFrame(self.frame, text="Target")
        target.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(12, 8))
        ttk.Radiobutton(target, text="Follow active window", variable=self.target_mode, value="follow", command=self.refresh_command).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(target, text="Window title", variable=self.target_mode, value="window", command=self.refresh_command).grid(row=1, column=0, sticky="w")
        ttk.Entry(target, textvariable=self.window, width=42).grid(row=1, column=1, sticky="ew", padx=8)
        ttk.Radiobutton(target, text="Region", variable=self.target_mode, value="region", command=self.refresh_command).grid(row=2, column=0, sticky="w")
        ttk.Entry(target, textvariable=self.region, width=42).grid(row=2, column=1, sticky="ew", padx=8)
        ttk.Label(target, text="x,y,width,height").grid(row=2, column=2, sticky="w")
        target.columnconfigure(1, weight=1)

        privacy = ttk.LabelFrame(self.frame, text="Privacy")
        privacy.grid(row=2, column=0, columnspan=4, sticky="ew", pady=8)
        ttk.Checkbutton(privacy, text="Dry-run", variable=self.dry_run, command=self.refresh_command).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(privacy, text="Auto-mask", variable=self.auto_mask, command=self.refresh_command).grid(row=0, column=1, sticky="w")
        for idx, name in enumerate(MASK_PRESETS):
            ttk.Checkbutton(privacy, text=name, variable=self.mask_vars[name], command=self.refresh_command).grid(row=1, column=idx, sticky="w")

        channels = ttk.LabelFrame(self.frame, text="Channels")
        channels.grid(row=3, column=0, columnspan=4, sticky="ew", pady=8)
        ttk.Checkbutton(channels, text="Disable OCR", variable=self.no_ocr, command=self.refresh_command).grid(row=0, column=0, sticky="w")
        ttk.Label(channels, text="Vision").grid(row=0, column=1, sticky="e")
        ttk.Combobox(channels, textvariable=self.caption_provider, values=("none", "glm"), width=8, state="readonly").grid(row=0, column=2, sticky="w", padx=8)

        timing = ttk.LabelFrame(self.frame, text="Timing")
        timing.grid(row=4, column=0, columnspan=4, sticky="ew", pady=8)
        ttk.Label(timing, text="OCR interval").grid(row=0, column=0, sticky="e")
        ttk.Entry(timing, textvariable=self.ocr_interval, width=8).grid(row=0, column=1, sticky="w", padx=8)
        ttk.Label(timing, text="Caption interval").grid(row=0, column=2, sticky="e")
        ttk.Entry(timing, textvariable=self.caption_interval, width=8).grid(row=0, column=3, sticky="w", padx=8)
        ttk.Label(timing, text="Batch interval").grid(row=0, column=4, sticky="e")
        ttk.Entry(timing, textvariable=self.batch_interval, width=8).grid(row=0, column=5, sticky="w", padx=8)

        extra = ttk.LabelFrame(self.frame, text="Extra Args")
        extra.grid(row=5, column=0, columnspan=4, sticky="ew", pady=8)
        ttk.Entry(extra, textvariable=self.extra_args).pack(fill="x", padx=4, pady=4)

        command = ttk.LabelFrame(self.frame, text="Command")
        command.grid(row=6, column=0, columnspan=4, sticky="ew", pady=8)
        ttk.Entry(command, textvariable=self.command_var).pack(fill="x", padx=4, pady=4)

        buttons = ttk.Frame(self.frame)
        buttons.grid(row=7, column=0, columnspan=4, sticky="ew", pady=8)
        ttk.Button(buttons, text="Refresh", command=self.refresh_command).pack(side="left")
        ttk.Button(buttons, text="Copy", command=self.copy_command).pack(side="left", padx=6)
        ttk.Button(buttons, text="Run Once", command=lambda: self.start(once=True)).pack(side="left", padx=6)
        ttk.Button(buttons, text="Start", command=lambda: self.start(once=False)).pack(side="left", padx=6)
        ttk.Button(buttons, text="Stop", command=self.stop).pack(side="left", padx=6)

        ttk.Label(self.frame, textvariable=self.status_var).grid(row=8, column=0, columnspan=4, sticky="w")
        self.output = tk.Text(self.frame, height=12, wrap="word")
        self.output.grid(row=9, column=0, columnspan=4, sticky="nsew", pady=(8, 0))

        for var in [
            self.window,
            self.region,
            self.ocr_interval,
            self.caption_interval,
            self.batch_interval,
            self.extra_args,
            self.caption_provider,
        ]:
            var.trace_add("write", lambda *_: self.refresh_command())
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(9, weight=1)

    def config(self) -> LauncherConfig:
        return LauncherConfig(
            target_mode=self.target_mode.get(),
            window=self.window.get(),
            region=self.region.get(),
            dry_run=self.dry_run.get(),
            auto_mask=self.auto_mask.get(),
            mask_presets=[name for name, var in self.mask_vars.items() if var.get()],
            no_ocr=self.no_ocr.get(),
            caption_provider=self.caption_provider.get(),
            ocr_interval=self.ocr_interval.get(),
            caption_interval=self.caption_interval.get(),
            batch_interval=self.batch_interval.get(),
            extra_args=self.extra_args.get(),
        )

    def refresh_command(self) -> None:
        try:
            self.command_var.set(command_string(build_command(self.config())))
        except Exception as exc:
            self.command_var.set(f"ERR: {exc}")

    def copy_command(self) -> None:
        self.refresh_command()
        self.root.clipboard_clear()
        self.root.clipboard_append(self.command_var.get())
        self.status_var.set("Command copied.")

    def start(self, *, once: bool) -> None:
        if self.process and self.process.poll() is None:
            self.messagebox.showinfo("gaze launcher", "gaze is already running.")
            return
        cmd = build_command(self.config(), once=once)
        self.command_var.set(command_string(cmd))
        self.output.insert("end", f"$ {self.command_var.get()}\n")
        self.output.see("end")
        try:
            self.process = subprocess.Popen(
                cmd,
                cwd=str(ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except Exception as exc:
            self.status_var.set(f"Start failed: {exc}")
            return
        self.status_var.set("Running once..." if once else "Running.")
        threading.Thread(target=self.read_output, daemon=True).start()

    def read_output(self) -> None:
        assert self.process and self.process.stdout
        for line in self.process.stdout:
            self.output_queue.put(line)
        code = self.process.wait()
        self.output_queue.put(f"[process exited: {code}]\n")

    def poll_output(self) -> None:
        try:
            while True:
                line = self.output_queue.get_nowait()
                self.output.insert("end", line)
                self.output.see("end")
                if line.startswith("[process exited:"):
                    self.status_var.set(line.strip("[]\n"))
        except queue.Empty:
            pass
        self.root.after(200, self.poll_output)

    def stop(self) -> None:
        if not self.process or self.process.poll() is not None:
            self.status_var.set("Not running.")
            return
        self.process.terminate()
        self.status_var.set("Stopping...")

    def close(self) -> None:
        self.stop()
        self.root.destroy()


if __name__ == "__main__":
    raise SystemExit(main())
