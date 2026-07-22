#!/usr/bin/env python3
"""Native desktop interface for VN300 telemetry analysis and Pi monitoring."""

from __future__ import annotations

import csv
import json
import math
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any

from vn300_team_app import build_analysis_command, result_files
from vn300_updater import (
    UPDATE_BRANCH,
    fetch_remote_version,
    has_git_checkout,
    launch_update_helper,
    read_current_version,
    read_update_status,
    stage_branch_archive,
    update_available,
)


APP_DIR = Path(__file__).resolve().parent
REPO_ROOT = APP_DIR.parent
STATE_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "VN300TeamTools"
STATE_PATH = STATE_DIR / "desktop_state.json"
CURRENT_VERSION = read_current_version(REPO_ROOT)

COLORS = {
    "ink": "#172027",
    "muted": "#68767d",
    "line": "#d5dde0",
    "soft": "#f2f5f5",
    "paper": "#ffffff",
    "sidebar": "#172126",
    "sidebar_active": "#23363d",
    "teal": "#00877f",
    "teal_dark": "#006b65",
    "green": "#16845b",
    "amber": "#c87a16",
    "red": "#b43c36",
}

DEFAULT_STATE = {
    "pi_endpoint": "",
    "analysis": {
        "input_path": "",
        "output_root": str(REPO_ROOT / "analysis_output" / "desktop_runs"),
        "mode": "auto",
        "driver_order": "",
        "driver_order_offset": 0,
        "auto_sectors": 3,
        "sector_report_min_seconds": 20.0,
        "gg_enabled": True,
        "include_ascii": False,
    },
    "history": [],
    "geometry": "1280x800",
}


def load_state(path: Path = STATE_PATH) -> dict[str, Any]:
    state = json.loads(json.dumps(DEFAULT_STATE))
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return state
    if not isinstance(loaded, dict):
        return state
    if isinstance(loaded.get("analysis"), dict):
        state["analysis"].update(loaded["analysis"])
    if isinstance(loaded.get("history"), list):
        state["history"] = loaded["history"][:20]
    for key in ("pi_endpoint", "geometry"):
        if isinstance(loaded.get(key), str):
            state[key] = loaded[key]
    return state


def save_state(state: dict[str, Any], path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temporary.replace(path)


def normalize_pi_endpoint(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Enter the Pi IP address or hostname.")
    if "://" not in value:
        value = f"http://{value}"
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("The Pi address must be an IP address, hostname, or HTTP URL.")
    port = parsed.port or 8080
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{parsed.scheme}://{host}:{port}/"


def fetch_pi_snapshot(endpoint: str, timeout: float = 2.5) -> dict[str, Any]:
    url = urllib.parse.urljoin(endpoint, "api/latest")
    request = urllib.request.Request(url, headers={"User-Agent": "VN300DesktopApp/0.1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read(2_000_000)
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("The Pi returned an invalid dashboard response.")
    return payload


def format_number(value: Any, digits: int = 1, suffix: str = "") -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "--"
    if not math.isfinite(number):
        return "--"
    return f"{number:.{digits}f}{suffix}"


def format_lap_time(value: Any) -> str:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return "--:--.---"
    if not math.isfinite(seconds) or seconds < 0:
        return "--:--.---"
    minutes = int(seconds // 60)
    return f"{minutes}:{seconds - minutes * 60:06.3f}"


class MetricTile(tk.Frame):
    def __init__(self, parent: tk.Misc, label: str, unit: str = ""):
        super().__init__(parent, bg=COLORS["paper"], highlightbackground=COLORS["line"], highlightthickness=1)
        self.grid_propagate(False)
        self.configure(height=84)
        tk.Label(self, text=label.upper(), bg=COLORS["paper"], fg=COLORS["muted"], font=("Segoe UI", 8, "bold")).pack(
            anchor="w", padx=12, pady=(10, 0)
        )
        row = tk.Frame(self, bg=COLORS["paper"])
        row.pack(fill="x", padx=12, pady=(5, 8))
        self.value_label = tk.Label(row, text="--", bg=COLORS["paper"], fg=COLORS["ink"], font=("Segoe UI", 19, "bold"))
        self.value_label.pack(side="left")
        self.unit_label = tk.Label(row, text=unit, bg=COLORS["paper"], fg=COLORS["muted"], font=("Segoe UI", 9))
        self.unit_label.pack(side="left", padx=(5, 0), pady=(10, 0))

    def set(self, value: str, color: str | None = None) -> None:
        self.value_label.configure(text=value, fg=color or COLORS["ink"])


class VN300DesktopApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"VN300 Team Tools v{CURRENT_VERSION}")
        self.configure(bg=COLORS["soft"])
        self.minsize(1000, 650)
        self.state_data = load_state()
        try:
            self.geometry(self.state_data.get("geometry") or "1280x800")
        except tk.TclError:
            self.geometry("1280x800")

        self.events: queue.Queue[tuple[Any, ...]] = queue.Queue()
        self.views: dict[str, tk.Frame] = {}
        self.nav_buttons: dict[str, tk.Button] = {}
        self.current_view = "analysis"
        self.analysis_process: subprocess.Popen[str] | None = None
        self.analysis_job: dict[str, Any] | None = None
        self.analysis_cancel_requested = False
        self.pi_endpoint = str(self.state_data.get("pi_endpoint", ""))
        self.pi_pending_endpoint = ""
        self.pi_connected = False
        self.pi_polling = False
        self.pi_poll_generation = 0
        self.pi_speed_history: list[float] = []
        self.pi_last_snapshot: dict[str, Any] = {}
        self.update_check_in_progress = False
        self.update_available_version = ""

        self._configure_styles()
        self._build_shell()
        self._build_analysis_view()
        self._build_dashboard_view()
        self._build_results_view()
        self.show_view("analysis")
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(100, self._process_events)
        self.after(350, self._initial_pi_connect)
        self.after(800, self._show_last_update_status)
        self.after(1400, lambda: self.check_for_updates(manual=False))

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=COLORS["paper"])
        style.configure("Soft.TFrame", background=COLORS["soft"])
        style.configure("TLabel", background=COLORS["paper"], foreground=COLORS["ink"], font=("Segoe UI", 9))
        style.configure("Muted.TLabel", foreground=COLORS["muted"], font=("Segoe UI", 8))
        style.configure("Heading.TLabel", font=("Segoe UI", 12, "bold"))
        style.configure("Section.TLabel", foreground=COLORS["teal"], font=("Segoe UI", 8, "bold"))
        style.configure("TEntry", fieldbackground="#ffffff", bordercolor="#bfcacd", padding=7)
        style.configure("TCombobox", fieldbackground="#ffffff", bordercolor="#bfcacd", padding=6)
        style.configure("TSpinbox", fieldbackground="#ffffff", bordercolor="#bfcacd", padding=6)
        style.configure("Primary.TButton", background=COLORS["teal"], foreground="#ffffff", borderwidth=0, padding=(15, 9), font=("Segoe UI", 9, "bold"))
        style.map("Primary.TButton", background=[("active", COLORS["teal_dark"]), ("disabled", "#87aaa6")])
        style.configure("Secondary.TButton", background="#ffffff", foreground=COLORS["ink"], bordercolor="#bfcacd", padding=(12, 8), font=("Segoe UI", 9, "bold"))
        style.map("Secondary.TButton", background=[("active", "#eef3f2")])
        style.configure("Danger.TButton", background="#ffffff", foreground=COLORS["red"], bordercolor="#d4aaa7", padding=(12, 8), font=("Segoe UI", 9, "bold"))
        style.configure("Treeview", rowheight=28, background="#ffffff", fieldbackground="#ffffff", bordercolor=COLORS["line"], font=("Segoe UI", 9))
        style.configure("Treeview.Heading", background="#e8eeee", foreground=COLORS["ink"], bordercolor=COLORS["line"], font=("Segoe UI", 8, "bold"))
        style.map("Treeview", background=[("selected", "#d8eeeb")], foreground=[("selected", COLORS["ink"])])
        style.configure("Horizontal.TProgressbar", background=COLORS["teal"], troughcolor="#d9e2e1")

    def _build_shell(self) -> None:
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        sidebar = tk.Frame(self, width=210, bg=COLORS["sidebar"])
        sidebar.grid(row=0, column=0, sticky="ns")
        sidebar.grid_propagate(False)

        brand = tk.Frame(sidebar, bg=COLORS["sidebar"], height=86)
        brand.pack(fill="x", padx=16, pady=(12, 20))
        brand.pack_propagate(False)
        mark = tk.Label(brand, text="V3", width=3, height=2, bg="#17a69b", fg="#ffffff", font=("Segoe UI", 11, "bold"))
        mark.pack(side="left", pady=14)
        brand_text = tk.Frame(brand, bg=COLORS["sidebar"])
        brand_text.pack(side="left", padx=10, pady=16)
        tk.Label(brand_text, text="VN300", bg=COLORS["sidebar"], fg="#ffffff", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        tk.Label(brand_text, text="TEAM TOOLS", bg=COLORS["sidebar"], fg="#91a5ac", font=("Segoe UI", 7, "bold")).pack(anchor="w")

        for name, label, short in (
            ("analysis", "Data analysis", "DA"),
            ("dashboard", "Pi dashboard", "PI"),
            ("results", "Results", "R"),
        ):
            button = tk.Button(
                sidebar,
                text=f"  {short}     {label}",
                anchor="w",
                relief="flat",
                bd=0,
                padx=12,
                pady=12,
                bg=COLORS["sidebar"],
                fg="#b3c1c5",
                activebackground=COLORS["sidebar_active"],
                activeforeground="#ffffff",
                font=("Segoe UI", 9, "bold"),
                command=lambda selected=name: self.show_view(selected),
            )
            button.pack(fill="x", padx=10, pady=2)
            self.nav_buttons[name] = button

        self.sidebar_pi_dot = tk.Label(sidebar, text="●", bg=COLORS["sidebar"], fg="#839197", font=("Segoe UI", 8))
        self.sidebar_pi_dot.pack(side="bottom", anchor="w", padx=17, pady=(0, 18))
        self.sidebar_pi_text = tk.Label(sidebar, text="Pi not connected", bg=COLORS["sidebar"], fg="#96a7ad", font=("Segoe UI", 8))
        self.sidebar_pi_text.place(x=34, rely=1.0, y=-29, anchor="sw")

        workspace = tk.Frame(self, bg=COLORS["soft"])
        workspace.grid(row=0, column=1, sticky="nsew")
        workspace.grid_rowconfigure(1, weight=1)
        workspace.grid_columnconfigure(0, weight=1)
        header = tk.Frame(workspace, height=82, bg=COLORS["paper"], highlightbackground=COLORS["line"], highlightthickness=1)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        heading = tk.Frame(header, bg=COLORS["paper"])
        heading.pack(side="left", padx=27, pady=15)
        self.header_eyebrow = tk.Label(heading, text="OFFLINE WORKSPACE", bg=COLORS["paper"], fg=COLORS["teal"], font=("Segoe UI", 7, "bold"))
        self.header_eyebrow.pack(anchor="w")
        self.header_title = tk.Label(heading, text="Data analysis", bg=COLORS["paper"], fg=COLORS["ink"], font=("Segoe UI", 17, "bold"))
        self.header_title.pack(anchor="w", pady=(2, 0))
        self.header_status = tk.Label(header, text="No active analysis", bg="#ffffff", fg=COLORS["muted"], relief="solid", bd=1, padx=12, pady=7, font=("Segoe UI", 8, "bold"))
        self.header_status.pack(side="right", padx=24)
        self.update_button = ttk.Button(header, text=f"v{CURRENT_VERSION} | Check updates", style="Secondary.TButton", command=self.update_action)
        self.update_button.pack(side="right")

        self.content = tk.Frame(workspace, bg=COLORS["soft"])
        self.content.grid(row=1, column=0, sticky="nsew")
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)

    def show_view(self, name: str) -> None:
        titles = {
            "analysis": ("OFFLINE WORKSPACE", "Data analysis"),
            "dashboard": ("LIVE TELEMETRY", "Pi dashboard"),
            "results": ("GENERATED OUTPUT", "Analysis results"),
        }
        for view in self.views.values():
            view.grid_remove()
        if name in self.views:
            self.views[name].grid()
        for key, button in self.nav_buttons.items():
            active = key == name
            button.configure(bg=COLORS["sidebar_active"] if active else COLORS["sidebar"], fg="#ffffff" if active else "#b3c1c5")
        self.current_view = name
        self.header_eyebrow.configure(text=titles[name][0])
        self.header_title.configure(text=titles[name][1])

    def _new_view(self, name: str, background: str = COLORS["paper"]) -> tk.Frame:
        frame = tk.Frame(self.content, bg=background)
        frame.grid(row=0, column=0, sticky="nsew")
        self.views[name] = frame
        return frame

    def _section_header(self, parent: tk.Misc, index: str, title: str, row: int, columnspan: int = 1) -> None:
        frame = tk.Frame(parent, bg=COLORS["paper"])
        frame.grid(row=row, column=0, columnspan=columnspan, sticky="ew", pady=(4, 12))
        tk.Label(frame, text=index, width=3, bg="#ffffff", fg=COLORS["teal"], relief="solid", bd=1, font=("Segoe UI", 7, "bold")).pack(side="left")
        tk.Label(frame, text=title, bg=COLORS["paper"], fg=COLORS["ink"], font=("Segoe UI", 11, "bold")).pack(side="left", padx=9)

    def _labeled_entry(self, parent: tk.Misc, label: str, variable: tk.Variable, row: int, browse: Any = None) -> ttk.Entry:
        tk.Label(parent, text=label, bg=COLORS["paper"], fg="#435159", font=("Segoe UI", 8, "bold")).grid(row=row, column=0, sticky="w", pady=(0, 5))
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row + 1, column=0, sticky="ew", pady=(0, 13))
        if browse:
            ttk.Button(parent, text="Browse", style="Secondary.TButton", command=browse).grid(row=row + 1, column=1, padx=(8, 0), pady=(0, 13))
        return entry

    def _build_analysis_view(self) -> None:
        view = self._new_view("analysis")
        view.grid_rowconfigure(0, weight=1)
        view.grid_columnconfigure(0, weight=1)
        view.grid_columnconfigure(1, minsize=360)
        form = tk.Frame(view, bg=COLORS["paper"], padx=34, pady=25)
        form.grid(row=0, column=0, sticky="nsew")
        form.grid_columnconfigure(0, weight=1)
        form.grid_columnconfigure(1, minsize=80)
        settings = self.state_data["analysis"]
        self.analysis_vars = {
            "input_path": tk.StringVar(value=settings.get("input_path", "")),
            "output_root": tk.StringVar(value=settings.get("output_root", "")),
            "mode": tk.StringVar(value=settings.get("mode", "auto")),
            "driver_order": tk.StringVar(value=settings.get("driver_order", "")),
            "driver_order_offset": tk.IntVar(value=settings.get("driver_order_offset", 0)),
            "auto_sectors": tk.IntVar(value=settings.get("auto_sectors", 3)),
            "sector_report_min_seconds": tk.DoubleVar(value=settings.get("sector_report_min_seconds", 20.0)),
            "gg_enabled": tk.BooleanVar(value=settings.get("gg_enabled", True)),
            "include_ascii": tk.BooleanVar(value=settings.get("include_ascii", False)),
        }
        self._section_header(form, "01", "Data source", 0, 2)
        self._labeled_entry(form, "Telemetry data", self.analysis_vars["input_path"], 1, self._browse_input)
        self._labeled_entry(form, "Output location", self.analysis_vars["output_root"], 3, self._browse_output)
        separator = ttk.Separator(form)
        separator.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(8, 18))
        self._section_header(form, "02", "Analysis setup", 6, 2)

        options = tk.Frame(form, bg=COLORS["paper"])
        options.grid(row=7, column=0, columnspan=2, sticky="ew")
        for column in range(3):
            options.grid_columnconfigure(column, weight=1)
        tk.Label(options, text="Timing mode", bg=COLORS["paper"], fg="#435159", font=("Segoe UI", 8, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Combobox(options, textvariable=self.analysis_vars["mode"], values=("auto", "lap", "autocross"), state="readonly").grid(row=1, column=0, sticky="ew", padx=(0, 10), pady=(5, 14))
        tk.Label(options, text="Automatic sectors", bg=COLORS["paper"], fg="#435159", font=("Segoe UI", 8, "bold")).grid(row=0, column=1, sticky="w")
        ttk.Spinbox(options, textvariable=self.analysis_vars["auto_sectors"], from_=0, to=20).grid(row=1, column=1, sticky="ew", padx=(0, 10), pady=(5, 14))
        tk.Label(options, text="Minimum timed segment (s)", bg=COLORS["paper"], fg="#435159", font=("Segoe UI", 8, "bold")).grid(row=0, column=2, sticky="w")
        ttk.Spinbox(options, textvariable=self.analysis_vars["sector_report_min_seconds"], from_=0, to=3600, increment=.5).grid(row=1, column=2, sticky="ew", pady=(5, 14))
        ttk.Checkbutton(options, text="Measured G-G lap prediction", variable=self.analysis_vars["gg_enabled"]).grid(row=2, column=0, columnspan=2, sticky="w", pady=5)
        ttk.Checkbutton(options, text="Include matching ASCII logs", variable=self.analysis_vars["include_ascii"]).grid(row=2, column=2, sticky="w", pady=5)

        driver = tk.LabelFrame(form, text="Driver mapping", bg=COLORS["paper"], fg=COLORS["muted"], bd=1, relief="solid", font=("Segoe UI", 8, "bold"), padx=12, pady=10)
        driver.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(18, 0))
        driver.grid_columnconfigure(0, weight=1)
        driver.grid_columnconfigure(1, minsize=110)
        tk.Label(driver, text="Sorted driver order", bg=COLORS["paper"], fg="#435159", font=("Segoe UI", 8, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Entry(driver, textvariable=self.analysis_vars["driver_order"]).grid(row=1, column=0, sticky="ew", padx=(0, 10), pady=(5, 0))
        tk.Label(driver, text="Files to skip", bg=COLORS["paper"], fg="#435159", font=("Segoe UI", 8, "bold")).grid(row=0, column=1, sticky="w")
        ttk.Spinbox(driver, textvariable=self.analysis_vars["driver_order_offset"], from_=0, to=10000).grid(row=1, column=1, sticky="ew", pady=(5, 0))

        status = tk.Frame(view, bg="#edf2f1", padx=22, pady=24, highlightbackground=COLORS["line"], highlightthickness=1)
        status.grid(row=0, column=1, sticky="nsew")
        status.grid_rowconfigure(4, weight=1)
        status.grid_columnconfigure(0, weight=1)
        tk.Label(status, text="ANALYZER", bg="#edf2f1", fg=COLORS["teal"], font=("Segoe UI", 7, "bold")).grid(row=0, column=0, sticky="w")
        self.analysis_status_label = tk.Label(status, text="Ready", bg="#edf2f1", fg=COLORS["ink"], font=("Segoe UI", 13, "bold"))
        self.analysis_status_label.grid(row=1, column=0, sticky="w", pady=(3, 10))
        self.analysis_progress = ttk.Progressbar(status, mode="indeterminate")
        self.analysis_progress.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        self.analysis_source_label = tk.Label(status, text="Waiting for telemetry data", bg="#edf2f1", fg=COLORS["muted"], anchor="w", font=("Segoe UI", 8))
        self.analysis_source_label.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        self.analysis_log = tk.Text(status, height=16, bg="#142026", fg="#c7dad8", insertbackground="#ffffff", relief="flat", padx=10, pady=10, wrap="word", font=("Consolas", 8), state="disabled")
        self.analysis_log.grid(row=4, column=0, sticky="nsew")
        action_row = tk.Frame(status, bg="#edf2f1")
        action_row.grid(row=5, column=0, sticky="ew", pady=(14, 0))
        self.cancel_analysis_button = ttk.Button(action_row, text="Cancel", style="Danger.TButton", command=self.cancel_analysis, state="disabled")
        self.cancel_analysis_button.pack(side="left")
        self.run_analysis_button = ttk.Button(action_row, text="Run analysis", style="Primary.TButton", command=self.start_analysis)
        self.run_analysis_button.pack(side="right")

    def _build_dashboard_view(self) -> None:
        view = self._new_view("dashboard", COLORS["soft"])
        view.grid_rowconfigure(2, weight=1)
        view.grid_columnconfigure(0, weight=1)
        toolbar = tk.Frame(view, bg=COLORS["soft"], padx=24, pady=16)
        toolbar.grid(row=0, column=0, sticky="ew")
        toolbar.grid_columnconfigure(1, weight=1)
        tk.Label(toolbar, text="PI ADDRESS", bg=COLORS["soft"], fg=COLORS["muted"], font=("Segoe UI", 7, "bold")).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.pi_address_var = tk.StringVar(value=self.pi_endpoint)
        self.pi_address_entry = ttk.Entry(toolbar, textvariable=self.pi_address_var)
        self.pi_address_entry.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ttk.Button(toolbar, text="Connect", style="Primary.TButton", command=self.connect_pi).grid(row=0, column=2, padx=(0, 7))
        ttk.Button(toolbar, text="Change address", style="Secondary.TButton", command=self.prompt_pi_address).grid(row=0, column=3, padx=(0, 12))
        self.pi_status_label = tk.Label(toolbar, text="Not connected", width=16, bg="#ffffff", fg=COLORS["muted"], relief="solid", bd=1, padx=9, pady=7, font=("Segoe UI", 8, "bold"))
        self.pi_status_label.grid(row=0, column=4)

        metrics = tk.Frame(view, bg=COLORS["soft"], padx=24)
        metrics.grid(row=1, column=0, sticky="ew")
        for column in range(8):
            metrics.grid_columnconfigure(column, weight=1, uniform="metric")
        self.pi_metrics: dict[str, MetricTile] = {}
        for column, (key, label, unit) in enumerate((
            ("speed", "Speed", "mph"),
            ("lat_g", "Lateral", "g"),
            ("long_g", "Longitudinal", "g"),
            ("yaw", "Yaw", "deg"),
            ("current", "Current", ""),
            ("best", "Best", ""),
            ("delta", "Live delta", "s"),
            ("laps", "Laps", ""),
        )):
            tile = MetricTile(metrics, label, unit)
            tile.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 4, 0 if column == 7 else 4))
            self.pi_metrics[key] = tile

        body = tk.Frame(view, bg=COLORS["soft"], padx=24, pady=15)
        body.grid(row=2, column=0, sticky="nsew")
        body.grid_rowconfigure(0, weight=1)
        body.grid_rowconfigure(1, weight=1)
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)
        track_frame = tk.Frame(body, bg=COLORS["paper"], highlightbackground=COLORS["line"], highlightthickness=1)
        track_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 7), pady=(0, 7))
        tk.Label(track_frame, text="TIMING TRACE", bg=COLORS["paper"], fg=COLORS["muted"], font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=12, pady=(10, 0))
        self.track_canvas = tk.Canvas(track_frame, bg="#f8faf9", bd=0, highlightthickness=0)
        self.track_canvas.pack(fill="both", expand=True, padx=10, pady=8)
        speed_frame = tk.Frame(body, bg=COLORS["paper"], highlightbackground=COLORS["line"], highlightthickness=1)
        speed_frame.grid(row=0, column=1, sticky="nsew", padx=(7, 0), pady=(0, 7))
        tk.Label(speed_frame, text="LIVE SPEED", bg=COLORS["paper"], fg=COLORS["muted"], font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=12, pady=(10, 0))
        self.speed_canvas = tk.Canvas(speed_frame, bg="#f8faf9", bd=0, highlightthickness=0)
        self.speed_canvas.pack(fill="both", expand=True, padx=10, pady=8)

        lap_frame = tk.Frame(body, bg=COLORS["paper"], highlightbackground=COLORS["line"], highlightthickness=1)
        lap_frame.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(7, 0))
        lap_frame.grid_rowconfigure(1, weight=1)
        lap_frame.grid_columnconfigure(0, weight=1)
        tk.Label(lap_frame, text="COMPLETED LAPS / RUNS", bg=COLORS["paper"], fg=COLORS["muted"], font=("Segoe UI", 8, "bold")).grid(row=0, column=0, sticky="w", padx=12, pady=10)
        columns = ("number", "type", "time", "delta", "warning")
        self.pi_lap_tree = ttk.Treeview(lap_frame, columns=columns, show="headings", height=6)
        for key, label, width in (("number", "#", 45), ("type", "Type", 75), ("time", "Time", 110), ("delta", "Delta", 90), ("warning", "Status", 350)):
            self.pi_lap_tree.heading(key, text=label)
            self.pi_lap_tree.column(key, width=width, stretch=key == "warning", anchor="w" if key == "warning" else "center")
        self.pi_lap_tree.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        scrollbar = ttk.Scrollbar(lap_frame, orient="vertical", command=self.pi_lap_tree.yview)
        scrollbar.grid(row=1, column=1, sticky="ns", pady=(0, 10), padx=(0, 8))
        self.pi_lap_tree.configure(yscrollcommand=scrollbar.set)

        footer = tk.Frame(view, bg=COLORS["paper"], height=38, highlightbackground=COLORS["line"], highlightthickness=1)
        footer.grid(row=3, column=0, sticky="ew")
        footer.grid_propagate(False)
        self.pi_session_label = tk.Label(footer, text="Session: --", bg=COLORS["paper"], fg=COLORS["muted"], font=("Segoe UI", 8))
        self.pi_session_label.pack(side="left", padx=15)
        self.pi_health_label = tk.Label(footer, text="Log: --", bg=COLORS["paper"], fg=COLORS["muted"], font=("Segoe UI", 8))
        self.pi_health_label.pack(side="left", padx=15)
        self.pi_cpu_label = tk.Label(footer, text="Pi CPU: --", bg=COLORS["paper"], fg=COLORS["muted"], font=("Segoe UI", 8))
        self.pi_cpu_label.pack(side="right", padx=15)

    def _build_results_view(self) -> None:
        view = self._new_view("results", COLORS["soft"])
        view.grid_rowconfigure(0, weight=1)
        view.grid_columnconfigure(0, minsize=290)
        view.grid_columnconfigure(1, weight=1)
        history_pane = tk.Frame(view, bg="#f8faf9", padx=16, pady=18, highlightbackground=COLORS["line"], highlightthickness=1)
        history_pane.grid(row=0, column=0, sticky="nsew")
        history_pane.grid_rowconfigure(1, weight=1)
        history_pane.grid_columnconfigure(0, weight=1)
        tk.Label(history_pane, text="RECENT RUNS", bg="#f8faf9", fg=COLORS["teal"], font=("Segoe UI", 8, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 10))
        self.history_tree = ttk.Treeview(history_pane, columns=("status", "created"), show="tree headings", selectmode="browse")
        self.history_tree.heading("#0", text="Data source")
        self.history_tree.heading("status", text="Status")
        self.history_tree.heading("created", text="Completed")
        self.history_tree.column("#0", width=150, stretch=True)
        self.history_tree.column("status", width=70, stretch=False)
        self.history_tree.column("created", width=105, stretch=False)
        self.history_tree.grid(row=1, column=0, sticky="nsew")
        self.history_tree.bind("<<TreeviewSelect>>", self._history_selected)

        detail = tk.Frame(view, bg=COLORS["paper"], padx=18, pady=16)
        detail.grid(row=0, column=1, sticky="nsew")
        detail.grid_rowconfigure(2, weight=1)
        detail.grid_columnconfigure(0, weight=1)
        toolbar = tk.Frame(detail, bg=COLORS["paper"])
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        toolbar.grid_columnconfigure(0, weight=1)
        self.result_title_label = tk.Label(toolbar, text="No result selected", bg=COLORS["paper"], fg=COLORS["ink"], font=("Segoe UI", 11, "bold"), anchor="w")
        self.result_title_label.grid(row=0, column=0, sticky="ew")
        ttk.Button(toolbar, text="Open folder", style="Secondary.TButton", command=self.open_result_folder).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(toolbar, text="Open file", style="Primary.TButton", command=self.open_result_file).grid(row=0, column=2, padx=(8, 0))

        file_frame = tk.Frame(detail, bg=COLORS["paper"])
        file_frame.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        file_frame.grid_columnconfigure(0, weight=1)
        self.result_file_tree = ttk.Treeview(file_frame, columns=("type",), show="tree headings", height=5, selectmode="browse")
        self.result_file_tree.heading("#0", text="Result")
        self.result_file_tree.heading("type", text="Type")
        self.result_file_tree.column("#0", width=330, stretch=True)
        self.result_file_tree.column("type", width=70, stretch=False, anchor="center")
        self.result_file_tree.grid(row=0, column=0, sticky="ew")
        self.result_file_tree.bind("<<TreeviewSelect>>", self._result_file_selected)
        self.result_file_tree.bind("<Double-1>", lambda _event: self.open_result_file())

        preview_frame = tk.Frame(detail, bg=COLORS["paper"], highlightbackground=COLORS["line"], highlightthickness=1)
        preview_frame.grid(row=2, column=0, sticky="nsew")
        preview_frame.grid_rowconfigure(0, weight=1)
        preview_frame.grid_columnconfigure(0, weight=1)
        self.preview_tree = ttk.Treeview(preview_frame, show="headings")
        self.preview_tree.grid(row=0, column=0, sticky="nsew")
        self.preview_y = ttk.Scrollbar(preview_frame, orient="vertical", command=self.preview_tree.yview)
        self.preview_y.grid(row=0, column=1, sticky="ns")
        self.preview_x = ttk.Scrollbar(preview_frame, orient="horizontal", command=self.preview_tree.xview)
        self.preview_x.grid(row=1, column=0, sticky="ew")
        self.preview_tree.configure(yscrollcommand=self.preview_y.set, xscrollcommand=self.preview_x.set)
        self.preview_message = tk.Label(preview_frame, text="Completed analysis CSV files are previewed here.", bg="#f8faf9", fg=COLORS["muted"], font=("Segoe UI", 9))
        self.preview_message.place(relx=.5, rely=.5, anchor="center")
        self.selected_history: dict[str, Any] | None = None
        self.selected_result: dict[str, str] | None = None
        self._refresh_history()

    def _browse_input(self) -> None:
        selected = filedialog.askdirectory(title="Select VN300 telemetry folder", initialdir=self._existing_parent(self.analysis_vars["input_path"].get()))
        if selected:
            self.analysis_vars["input_path"].set(selected)

    def _browse_output(self) -> None:
        selected = filedialog.askdirectory(title="Select analysis output folder", initialdir=self._existing_parent(self.analysis_vars["output_root"].get()))
        if selected:
            self.analysis_vars["output_root"].set(selected)

    @staticmethod
    def _existing_parent(value: str) -> str:
        path = Path(value).expanduser() if value else REPO_ROOT
        while not path.exists() and path != path.parent:
            path = path.parent
        return str(path if path.is_dir() else path.parent)

    def _analysis_payload(self) -> dict[str, Any]:
        return {key: variable.get() for key, variable in self.analysis_vars.items()}

    def start_analysis(self) -> None:
        if self.analysis_process is not None:
            return
        try:
            payload = self._analysis_payload()
        except tk.TclError:
            messagebox.showerror("Analysis setup", "Sector, minimum-time, and driver-offset values must be numbers.", parent=self)
            return
        output_root = Path(str(payload["output_root"])).expanduser()
        if not str(payload["output_root"]).strip():
            messagebox.showerror("Analysis setup", "Select an output folder.", parent=self)
            return
        output_dir: Path | None = None
        try:
            output_root.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("analysis_%Y%m%d_%H%M%S")
            output_dir = output_root / stamp
            suffix = 2
            while output_dir.exists():
                output_dir = output_root / f"{stamp}_{suffix}"
                suffix += 1
            output_dir.mkdir()
            command, validated = build_analysis_command(payload, output_dir)
        except (OSError, ValueError) as exc:
            if output_dir is not None and output_dir.is_dir() and not any(output_dir.iterdir()):
                output_dir.rmdir()
            messagebox.showerror("Analysis setup", str(exc), parent=self)
            return

        self.state_data["analysis"].update(validated)
        self._save_state_safely()
        self.analysis_job = {
            "id": uuid.uuid4().hex[:12],
            "status": "running",
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "completed_at": "",
            "input_path": validated["input_path"],
            "output_dir": str(output_dir.resolve()),
            "mode": validated["mode"],
            "results": [],
            "exit_code": None,
        }
        self.analysis_cancel_requested = False
        self._set_analysis_running(True)
        self._replace_analysis_log("Starting analyzer...\n")
        self.analysis_source_label.configure(text=validated["input_path"])
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            self.analysis_process = subprocess.Popen(
                command,
                cwd=str(REPO_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
        except OSError as exc:
            self.analysis_process = None
            self._set_analysis_running(False)
            if output_dir.is_dir() and not any(output_dir.iterdir()):
                output_dir.rmdir()
            messagebox.showerror("Analyzer", f"Could not start the analyzer: {exc}", parent=self)
            return
        threading.Thread(target=self._read_analysis_output, args=(self.analysis_process,), daemon=True).start()

    def _read_analysis_output(self, process: subprocess.Popen[str]) -> None:
        assert process.stdout is not None
        for line in process.stdout:
            self.events.put(("analysis_log", line))
        exit_code = process.wait()
        self.events.put(("analysis_done", exit_code))

    def cancel_analysis(self) -> None:
        process = self.analysis_process
        if process is None:
            return
        self.analysis_cancel_requested = True
        self.analysis_status_label.configure(text="Cancelling")
        try:
            process.terminate()
        except OSError:
            pass

    def _set_analysis_running(self, running: bool) -> None:
        self.run_analysis_button.configure(state="disabled" if running else "normal")
        self.cancel_analysis_button.configure(state="normal" if running else "disabled")
        self.header_status.configure(
            text="Analysis running" if running else "No active analysis",
            fg=COLORS["amber"] if running else COLORS["muted"],
        )
        if running:
            self.analysis_status_label.configure(text="Running")
            self.analysis_progress.start(10)
        else:
            self.analysis_progress.stop()

    def _replace_analysis_log(self, text: str) -> None:
        self.analysis_log.configure(state="normal")
        self.analysis_log.delete("1.0", "end")
        self.analysis_log.insert("end", text)
        self.analysis_log.configure(state="disabled")

    def _append_analysis_log(self, text: str) -> None:
        self.analysis_log.configure(state="normal")
        self.analysis_log.insert("end", text)
        self.analysis_log.see("end")
        self.analysis_log.configure(state="disabled")

    def _finish_analysis(self, exit_code: int) -> None:
        self.analysis_process = None
        self._set_analysis_running(False)
        if self.analysis_job is None:
            return
        status = "cancelled" if self.analysis_cancel_requested else ("completed" if exit_code == 0 else "failed")
        self.analysis_job["status"] = status
        self.analysis_job["exit_code"] = exit_code
        self.analysis_job["completed_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        self.analysis_job["results"] = result_files(Path(self.analysis_job["output_dir"]))
        history = [item for item in self.state_data["history"] if item.get("id") != self.analysis_job["id"]]
        self.state_data["history"] = [self.analysis_job, *history][:20]
        self._save_state_safely()
        self.analysis_status_label.configure(text=status.capitalize(), fg=COLORS["green"] if status == "completed" else COLORS["red"])
        self.header_status.configure(text=f"Last run {status}", fg=COLORS["green"] if status == "completed" else COLORS["red"])
        self._refresh_history(select_id=self.analysis_job["id"])
        if status == "completed":
            self.show_view("results")

    def _initial_pi_connect(self) -> None:
        if self.pi_endpoint:
            self.pi_address_var.set(self.pi_endpoint)
            self.connect_pi()
        else:
            self.prompt_pi_address(first_run=True)

    def prompt_pi_address(self, first_run: bool = False) -> None:
        initial = self.pi_address_var.get() or "raspberrypi.local"
        value = simpledialog.askstring(
            "Connect to VN300 Pi",
            "Raspberry Pi IP address or hostname:",
            initialvalue=initial,
            parent=self,
        )
        if value:
            self.pi_address_var.set(value.strip())
            self.connect_pi()
        elif first_run:
            self._set_pi_status("Not connected", "neutral")

    def connect_pi(self) -> None:
        try:
            endpoint = normalize_pi_endpoint(self.pi_address_var.get())
        except ValueError as exc:
            messagebox.showerror("Pi address", str(exc), parent=self)
            return
        self.pi_address_var.set(endpoint)
        self.pi_pending_endpoint = endpoint
        self.pi_connected = False
        self.pi_poll_generation += 1
        self._set_pi_status("Connecting", "connecting")
        self._schedule_pi_poll(0, self.pi_poll_generation)

    def _schedule_pi_poll(self, delay_ms: int, generation: int) -> None:
        self.after(delay_ms, lambda: self._start_pi_poll(generation))

    def _start_pi_poll(self, generation: int) -> None:
        if generation != self.pi_poll_generation or self.pi_polling:
            return
        endpoint = self.pi_pending_endpoint or self.pi_endpoint
        if not endpoint:
            return
        self.pi_polling = True
        threading.Thread(target=self._poll_pi_worker, args=(endpoint, generation), daemon=True).start()

    def _poll_pi_worker(self, endpoint: str, generation: int) -> None:
        started = time.monotonic()
        try:
            snapshot = fetch_pi_snapshot(endpoint)
            self.events.put(("pi_snapshot", generation, endpoint, snapshot, time.monotonic() - started))
        except (ValueError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
            self.events.put(("pi_error", generation, endpoint, str(exc)))

    def _handle_pi_snapshot(self, generation: int, endpoint: str, snapshot: dict[str, Any], latency: float) -> None:
        self.pi_polling = False
        if generation != self.pi_poll_generation:
            self._schedule_pi_poll(0, self.pi_poll_generation)
            return
        first_success = endpoint != self.pi_endpoint
        self.pi_connected = True
        self.pi_pending_endpoint = endpoint
        if first_success or not self.state_data.get("pi_endpoint"):
            self.pi_endpoint = endpoint
            self.state_data["pi_endpoint"] = endpoint
            self._save_state_safely()
        self._set_pi_status("Logging" if snapshot.get("logging") else "Online", "online")
        self._update_pi_dashboard(snapshot, latency)
        self._schedule_pi_poll(500, generation)

    def _handle_pi_error(self, generation: int, endpoint: str, error: str) -> None:
        self.pi_polling = False
        if generation != self.pi_poll_generation:
            self._schedule_pi_poll(0, self.pi_poll_generation)
            return
        self.pi_connected = False
        self._set_pi_status("Offline", "offline")
        self.sidebar_pi_text.configure(text="Pi offline")
        self.pi_health_label.configure(text=f"Connection: {error[:90]}")
        self._schedule_pi_poll(2500, generation)

    def _set_pi_status(self, text: str, state: str) -> None:
        colors = {"online": COLORS["green"], "offline": COLORS["red"], "connecting": COLORS["amber"], "neutral": COLORS["muted"]}
        color = colors.get(state, COLORS["muted"])
        self.pi_status_label.configure(text=text, fg=color)
        self.sidebar_pi_dot.configure(fg=color)
        self.sidebar_pi_text.configure(text=f"Pi {text.lower()}")

    def _update_pi_dashboard(self, snapshot: dict[str, Any], latency: float) -> None:
        self.pi_last_snapshot = snapshot
        fields = snapshot.get("fields") if isinstance(snapshot.get("fields"), dict) else {}
        timing = snapshot.get("timing") if isinstance(snapshot.get("timing"), dict) else {}
        speed = fields.get("Speed_mph")
        self.pi_metrics["speed"].set(format_number(speed, 1))
        self.pi_metrics["lat_g"].set(format_number(fields.get("Lateral_G"), 2))
        self.pi_metrics["long_g"].set(format_number(fields.get("Longitudinal_G"), 2))
        self.pi_metrics["yaw"].set(format_number(fields.get("Yaw_deg"), 1))
        self.pi_metrics["current"].set(format_lap_time(timing.get("current_elapsed_s")))
        self.pi_metrics["best"].set(format_lap_time(timing.get("best_lap_s")))
        delta = timing.get("live_delta_s") if timing.get("live_delta_available") else None
        delta_text = format_number(delta, 3, "")
        delta_color = COLORS["green"] if isinstance(delta, (int, float)) and delta < 0 else COLORS["red"] if isinstance(delta, (int, float)) else COLORS["ink"]
        self.pi_metrics["delta"].set(delta_text, delta_color)
        self.pi_metrics["laps"].set(str(timing.get("lap_count", 0)))
        if isinstance(speed, (int, float)) and math.isfinite(speed):
            self.pi_speed_history.append(float(speed))
            self.pi_speed_history = self.pi_speed_history[-240:]
        self._draw_speed_trace()
        self._draw_track_trace(timing)
        self._update_lap_table(timing.get("completed", []))
        session = snapshot.get("session") or "--"
        self.pi_session_label.configure(text=f"Session: {session} | {latency * 1000:.0f} ms")
        health = snapshot.get("log_health") or "--"
        destination = snapshot.get("log_destination") or "--"
        free_mb = snapshot.get("free_space_mb")
        self.pi_health_label.configure(text=f"Log: {destination} / {health} / {format_number(free_mb, 0)} MB free")
        self.pi_cpu_label.configure(text=f"Pi CPU: {format_number(snapshot.get('cpu_temp_c'), 1)} C")

    def _draw_speed_trace(self) -> None:
        canvas = self.speed_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 120)
        height = max(canvas.winfo_height(), 80)
        pad = 18
        for fraction in (.25, .5, .75):
            y = pad + (height - pad * 2) * fraction
            canvas.create_line(pad, y, width - pad, y, fill="#dce4e4")
        values = self.pi_speed_history
        if len(values) < 2:
            canvas.create_text(20, 25, anchor="w", text="No live speed data", fill=COLORS["muted"], font=("Segoe UI", 8))
            return
        maximum = max(60.0, max(values) * 1.1)
        points = []
        for index, value in enumerate(values):
            x = pad + index * (width - pad * 2) / max(len(values) - 1, 1)
            y = height - pad - value / maximum * (height - pad * 2)
            points.extend((x, y))
        canvas.create_line(*points, fill=COLORS["teal"], width=2, smooth=True)
        canvas.create_text(width - pad, pad, anchor="ne", text=f"{maximum:.0f} mph", fill=COLORS["muted"], font=("Segoe UI", 7))

    def _draw_track_trace(self, timing: dict[str, Any]) -> None:
        canvas = self.track_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 120)
        height = max(canvas.winfo_height(), 80)
        current = [point for point in timing.get("current_trace", []) if isinstance(point, dict) and isinstance(point.get("lat"), (int, float)) and isinstance(point.get("lon"), (int, float))]
        best = [point for point in timing.get("best_trace", []) if isinstance(point, dict) and isinstance(point.get("lat"), (int, float)) and isinstance(point.get("lon"), (int, float))]
        all_points = current + best
        if len(all_points) < 2:
            canvas.create_text(20, 25, anchor="w", text="No active timing trace", fill=COLORS["muted"], font=("Segoe UI", 8))
            return
        origin_lat = math.radians(all_points[0]["lat"])
        origin_lon = all_points[0]["lon"]
        origin_lat_deg = all_points[0]["lat"]

        def project(points: list[dict[str, Any]]) -> list[tuple[float, float]]:
            return [
                ((point["lon"] - origin_lon) * math.cos(origin_lat) * 111320.0, (point["lat"] - origin_lat_deg) * 110540.0)
                for point in points
            ]

        projected_current = project(current)
        projected_best = project(best)
        projected_all = projected_current + projected_best
        min_x = min(point[0] for point in projected_all)
        max_x = max(point[0] for point in projected_all)
        min_y = min(point[1] for point in projected_all)
        max_y = max(point[1] for point in projected_all)
        span_x = max(max_x - min_x, 1.0)
        span_y = max(max_y - min_y, 1.0)
        scale = min((width - 38) / span_x, (height - 38) / span_y)

        def pixels(points: list[tuple[float, float]]) -> list[float]:
            result = []
            offset_x = (width - span_x * scale) / 2
            offset_y = (height - span_y * scale) / 2
            for x, y in points:
                result.extend((offset_x + (x - min_x) * scale, height - (offset_y + (y - min_y) * scale)))
            return result

        if len(projected_best) > 1:
            canvas.create_line(*pixels(projected_best), fill=COLORS["green"], width=2)
        if len(projected_current) > 1:
            canvas.create_line(*pixels(projected_current), fill=COLORS["teal"], width=2)
            px = pixels([projected_current[-1]])
            canvas.create_oval(px[0] - 4, px[1] - 4, px[0] + 4, px[1] + 4, fill=COLORS["teal"], outline="")

    def _update_lap_table(self, rows: Any) -> None:
        self.pi_lap_tree.delete(*self.pi_lap_tree.get_children())
        if not isinstance(rows, list):
            return
        for row in reversed(rows[-20:]):
            if not isinstance(row, dict):
                continue
            delta = row.get("delta_to_best_s")
            delta_text = "--" if delta is None else f"{float(delta):+.3f}"
            self.pi_lap_tree.insert("", "end", values=(row.get("number", ""), row.get("type", ""), format_lap_time(row.get("duration_s")), delta_text, row.get("warning", "")))

    def _refresh_history(self, select_id: str = "") -> None:
        if not hasattr(self, "history_tree"):
            return
        self.history_tree.delete(*self.history_tree.get_children())
        selected_item = ""
        for entry in self.state_data.get("history", []):
            entry_id = str(entry.get("id", ""))
            source = Path(str(entry.get("input_path", ""))).name or entry_id
            created = str(entry.get("completed_at") or entry.get("created_at") or "").replace("T", " ")[:16]
            item = self.history_tree.insert("", "end", iid=entry_id, text=source, values=(entry.get("status", ""), created))
            if entry_id == select_id:
                selected_item = item
        if selected_item:
            self.history_tree.selection_set(selected_item)
            self.history_tree.focus(selected_item)
            self.history_tree.see(selected_item)
            self._history_selected()

    def _history_selected(self, _event: Any = None) -> None:
        selection = self.history_tree.selection()
        if not selection:
            return
        entry_id = selection[0]
        self.selected_history = next((item for item in self.state_data.get("history", []) if str(item.get("id")) == entry_id), None)
        self.selected_result = None
        self.result_file_tree.delete(*self.result_file_tree.get_children())
        if self.selected_history is None:
            return
        self.result_title_label.configure(text=Path(str(self.selected_history.get("input_path", ""))).name)
        first_csv = ""
        for index, result in enumerate(self.selected_history.get("results", [])):
            item = self.result_file_tree.insert("", "end", iid=f"result_{index}", text=result.get("label", result.get("name", "")), values=(result.get("kind", "").upper(),))
            if not first_csv and result.get("kind") == "csv":
                first_csv = item
        if first_csv:
            self.result_file_tree.selection_set(first_csv)
            self.result_file_tree.focus(first_csv)
            self._result_file_selected()
        else:
            self._clear_preview("No CSV results are available for this run.")

    def _result_file_selected(self, _event: Any = None) -> None:
        selection = self.result_file_tree.selection()
        if not selection or self.selected_history is None:
            return
        try:
            index = int(selection[0].split("_", 1)[1])
            self.selected_result = self.selected_history.get("results", [])[index]
        except (ValueError, IndexError):
            self.selected_result = None
            return
        self.result_title_label.configure(text=self.selected_result.get("label", self.selected_result.get("name", "Result")))
        path = Path(self.selected_history["output_dir"]) / self.selected_result["name"]
        if self.selected_result.get("kind") == "csv":
            self._preview_csv(path)
        else:
            self._clear_preview("HTML analysis report selected.")

    def _preview_csv(self, path: Path) -> None:
        try:
            with path.open(newline="", encoding="utf-8-sig", errors="replace") as handle:
                reader = csv.reader(handle)
                header = next(reader, [])
                rows = [row for _, row in zip(range(500), reader)]
        except OSError as exc:
            self._clear_preview(str(exc))
            return
        self.preview_message.place_forget()
        self.preview_tree.delete(*self.preview_tree.get_children())
        column_ids = [f"c{index}" for index in range(len(header))]
        self.preview_tree.configure(columns=column_ids, show="headings")
        for column_id, label in zip(column_ids, header):
            self.preview_tree.heading(column_id, text=label)
            self.preview_tree.column(column_id, width=max(85, min(190, len(label) * 8 + 24)), stretch=False, anchor="w")
        for row in rows:
            padded = [*row, *([""] * max(0, len(header) - len(row)))]
            self.preview_tree.insert("", "end", values=padded[:len(header)])

    def _clear_preview(self, message: str) -> None:
        self.preview_tree.delete(*self.preview_tree.get_children())
        self.preview_tree.configure(columns=(), show="headings")
        self.preview_message.configure(text=message)
        self.preview_message.place(relx=.5, rely=.5, anchor="center")

    def open_result_file(self) -> None:
        if self.selected_history is None or self.selected_result is None:
            return
        path = Path(self.selected_history["output_dir"]) / self.selected_result["name"]
        if not path.is_file():
            messagebox.showerror("Result", "The selected result file no longer exists.", parent=self)
            return
        os.startfile(path)  # type: ignore[attr-defined]

    def open_result_folder(self) -> None:
        if self.selected_history is None:
            return
        path = Path(self.selected_history["output_dir"])
        if not path.is_dir():
            messagebox.showerror("Result", "The analysis output folder no longer exists.", parent=self)
            return
        os.startfile(path)  # type: ignore[attr-defined]

    def _process_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "analysis_log":
                    self._append_analysis_log(str(event[1]))
                elif event[0] == "analysis_done":
                    self._finish_analysis(int(event[1]))
                elif event[0] == "pi_snapshot":
                    self._handle_pi_snapshot(int(event[1]), str(event[2]), event[3], float(event[4]))
                elif event[0] == "pi_error":
                    self._handle_pi_error(int(event[1]), str(event[2]), str(event[3]))
                elif event[0] == "update_check":
                    self._handle_update_check(str(event[1]), bool(event[2]))
                elif event[0] == "update_error":
                    self._handle_update_error(str(event[1]), bool(event[2]))
                elif event[0] == "update_staged":
                    self._launch_update_and_close(Path(event[1]["source_root"]))
                elif event[0] == "update_stage_error":
                    self.update_button.configure(text=f"Install update v{self.update_available_version}", state="normal")
                    messagebox.showerror("Software update", f"Could not prepare the update:\n\n{event[1]}", parent=self)
        except queue.Empty:
            pass
        self.after(100, self._process_events)

    def _save_state_safely(self) -> None:
        try:
            save_state(self.state_data)
        except OSError as exc:
            messagebox.showerror("Settings", f"Could not save desktop settings: {exc}", parent=self)

    def check_for_updates(self, manual: bool = True) -> None:
        if self.update_check_in_progress:
            return
        self.update_check_in_progress = True
        self.update_button.configure(text="Checking for updates...", state="disabled")
        threading.Thread(target=self._check_update_worker, args=(manual,), daemon=True).start()

    def _check_update_worker(self, manual: bool) -> None:
        try:
            remote = fetch_remote_version()
            self.events.put(("update_check", remote, manual))
        except Exception as exc:
            self.events.put(("update_error", str(exc), manual))

    def _handle_update_check(self, remote: str, manual: bool) -> None:
        self.update_check_in_progress = False
        if update_available(CURRENT_VERSION, remote):
            self.update_available_version = remote
            self.update_button.configure(text=f"Install update v{remote}", state="normal", style="Primary.TButton")
            return
        self.update_available_version = ""
        self.update_button.configure(text=f"v{CURRENT_VERSION} | Up to date", state="normal", style="Secondary.TButton")
        if manual:
            messagebox.showinfo("Software update", f"VN300 Team Tools v{CURRENT_VERSION} is up to date.", parent=self)

    def _handle_update_error(self, error: str, manual: bool) -> None:
        self.update_check_in_progress = False
        self.update_button.configure(text=f"v{CURRENT_VERSION} | Check updates", state="normal", style="Secondary.TButton")
        if manual:
            messagebox.showerror("Software update", f"Could not check GitHub for updates:\n\n{error}", parent=self)

    def update_action(self) -> None:
        if self.update_available_version:
            self.install_update()
        else:
            self.check_for_updates(manual=True)

    def install_update(self) -> None:
        if self.analysis_process is not None:
            messagebox.showwarning("Software update", "Wait for the running analysis to finish before updating.", parent=self)
            return
        version = self.update_available_version
        if not version:
            self.check_for_updates(manual=True)
            return
        if not messagebox.askyesno(
            "Install software update",
            f"Install VN300 Team Tools v{version} now?\n\nThe application will close, update from GitHub, and restart.",
            parent=self,
        ):
            return
        self.update_button.configure(text="Preparing update...", state="disabled")
        if has_git_checkout(REPO_ROOT):
            self._launch_update_and_close(None)
            return
        threading.Thread(target=self._stage_update_worker, args=(version,), daemon=True).start()

    def _stage_update_worker(self, expected_version: str) -> None:
        try:
            staged = stage_branch_archive(STATE_DIR, expected_version=expected_version)
            self.events.put(("update_staged", staged))
        except Exception as exc:
            self.events.put(("update_stage_error", str(exc)))

    def _launch_update_and_close(self, source_root: Path | None) -> None:
        self.state_data["geometry"] = self.geometry()
        try:
            save_state(self.state_data)
            launch_update_helper(REPO_ROOT, STATE_DIR, source_root, os.getpid(), UPDATE_BRANCH)
        except OSError as exc:
            self.update_button.configure(text=f"Install update v{self.update_available_version}", state="normal")
            messagebox.showerror("Software update", f"Could not start the updater:\n\n{exc}", parent=self)
            return
        self.destroy()

    def _show_last_update_status(self) -> None:
        status = read_update_status(STATE_DIR)
        if not status:
            return
        if status.get("ok"):
            messagebox.showinfo("Software update", str(status.get("message", "Update completed.")), parent=self)
        else:
            messagebox.showerror("Software update", str(status.get("message", "Update failed.")), parent=self)

    def on_close(self) -> None:
        if self.analysis_process is not None:
            if not messagebox.askyesno("Analysis running", "Cancel the running analysis and close?", parent=self):
                return
            try:
                self.analysis_process.terminate()
            except OSError:
                pass
        self.state_data["geometry"] = self.geometry()
        try:
            save_state(self.state_data)
        except OSError:
            pass
        self.destroy()


def main() -> None:
    app = VN300DesktopApp()
    app.mainloop()


if __name__ == "__main__":
    main()
