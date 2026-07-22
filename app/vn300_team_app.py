#!/usr/bin/env python3
"""Local VN300 team workspace for offline analysis and the Pi dashboard."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


IS_FROZEN = bool(getattr(sys, "frozen", False))
APP_DIR = Path(__file__).resolve().parent
REPO_ROOT = Path(sys.executable).resolve().parent if IS_FROZEN else APP_DIR.parent
STATIC_DIR = APP_DIR / "static"
ANALYZER_PATH = REPO_ROOT / "VN300Analyzer.exe" if IS_FROZEN else REPO_ROOT / "analysis" / "vn300_lap_analysis.py"
STATE_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "VN300TeamTools"
STATE_PATH = STATE_DIR / "app_state.json"
DEFAULT_OUTPUT_ROOT = (
    Path.home() / "Documents" / "VN300 Team Tools" / "Analysis"
    if IS_FROZEN
    else REPO_ROOT / "analysis_output" / "app_runs"
)

DEFAULT_SETTINGS = {
    "pi_url": "http://raspberrypi.local:8080/",
    "input_path": "",
    "output_root": str(DEFAULT_OUTPUT_ROOT),
    "mode": "auto",
    "driver_order": "",
    "driver_order_offset": 0,
    "auto_sectors": 3,
    "sector_report_min_seconds": 20.0,
    "gg_enabled": True,
    "include_ascii": False,
}

RESULT_LABELS = {
    "report.html": "Analysis report",
    "lap_times_sector_splits.html": "Lap and sector report",
    "overlay.html": "Lap overlay",
    "car_gg_lap_prediction.csv": "G-G lap prediction",
    "summary.csv": "Run summary",
    "sector_summary.csv": "Sector summary",
    "lap_sector_splits.csv": "Lap sector data",
    "theoretical_best_by_driver.csv": "Driver theoretical bests",
    "overall_best_sectors.csv": "Overall best sectors",
    "car_gg_envelope.csv": "Car G-G envelope",
    "car_gg_driver_envelopes.csv": "Driver G-G envelopes",
    "car_gg_lap_trace.csv": "Predicted lap trace",
}


def utc_timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def coerce_int(value: Any, name: str, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a whole number.") from exc
    if not minimum <= number <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}.")
    return number


def coerce_float(value: Any, name: str, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number.") from exc
    if not minimum <= number <= maximum:
        raise ValueError(f"{name} must be between {minimum:g} and {maximum:g}.")
    return number


def normalize_pi_url(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Pi dashboard URL is required.")
    if "://" not in value:
        value = f"http://{value}"
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Pi dashboard URL must use http:// or https://.")
    base_path = parsed.path if parsed.path.endswith("/") else f"{parsed.path}/"
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, base_path, "", "", ""))


def path_is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


class StateStore:
    def __init__(self, path: Path = STATE_PATH):
        self.path = path
        self.lock = threading.Lock()
        self.data = {"settings": dict(DEFAULT_SETTINGS), "history": []}
        self._load()

    def _load(self) -> None:
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return
        if isinstance(loaded, dict):
            settings = loaded.get("settings")
            history = loaded.get("history")
            if isinstance(settings, dict):
                self.data["settings"].update(settings)
            if isinstance(history, list):
                self.data["history"] = history[:20]

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return json.loads(json.dumps(self.data))

    def update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        allowed = DEFAULT_SETTINGS.keys()
        with self.lock:
            self.data["settings"].update({key: values[key] for key in allowed if key in values})
            self._save_locked()
            return dict(self.data["settings"])

    def add_history(self, entry: dict[str, Any]) -> None:
        with self.lock:
            history = [item for item in self.data["history"] if item.get("id") != entry.get("id")]
            self.data["history"] = [entry, *history][:20]
            self._save_locked()


def result_files(output_dir: Path) -> list[dict[str, str]]:
    files = []
    for name, label in RESULT_LABELS.items():
        path = output_dir / name
        if path.is_file():
            files.append({"name": name, "label": label, "kind": path.suffix.lstrip(".")})
    known = {item["name"] for item in files}
    for path in sorted(output_dir.glob("*")):
        if path.is_file() and path.name not in known and path.suffix.lower() in (".html", ".csv"):
            files.append({"name": path.name, "label": path.stem.replace("_", " ").title(), "kind": path.suffix.lstrip(".")})
    return files


def build_analysis_command(payload: dict[str, Any], output_dir: Path) -> tuple[list[str], dict[str, Any]]:
    input_path = Path(str(payload.get("input_path", "")).strip()).expanduser()
    if not input_path.exists():
        raise ValueError("The selected data path does not exist.")

    mode = str(payload.get("mode", "auto")).lower()
    if mode not in ("auto", "lap", "autocross"):
        raise ValueError("Timing mode must be Auto, Lap, or Autocross.")
    driver_order = str(payload.get("driver_order", "")).strip()
    driver_order_offset = coerce_int(payload.get("driver_order_offset", 0), "Driver offset", 0, 10000)
    auto_sectors = coerce_int(payload.get("auto_sectors", 3), "Automatic sectors", 0, 20)
    sector_minimum = coerce_float(
        payload.get("sector_report_min_seconds", 20.0), "Sector report minimum", 0.0, 3600.0
    )

    command = [
        *([str(ANALYZER_PATH)] if IS_FROZEN else [sys.executable, str(ANALYZER_PATH)]),
        str(input_path.resolve()),
        "--no-prompts",
        "--out",
        str(output_dir),
        "--auto-sectors",
        str(auto_sectors),
        "--sector-report-min-seconds",
        f"{sector_minimum:g}",
    ]
    if mode != "auto":
        command.extend(("--mode", mode))
    if driver_order:
        command.extend(("--driver-order", driver_order, "--driver-order-offset", str(driver_order_offset)))
    if bool(payload.get("include_ascii", False)):
        command.append("--include-ascii")
    if not bool(payload.get("gg_enabled", True)):
        command.append("--no-gg-lap-prediction")

    settings = {
        "input_path": str(input_path.resolve()),
        "output_root": str(output_dir.parent),
        "mode": mode,
        "driver_order": driver_order,
        "driver_order_offset": driver_order_offset,
        "auto_sectors": auto_sectors,
        "sector_report_min_seconds": sector_minimum,
        "gg_enabled": bool(payload.get("gg_enabled", True)),
        "include_ascii": bool(payload.get("include_ascii", False)),
    }
    return command, settings


class AnalysisJobs:
    def __init__(self, store: StateStore):
        self.store = store
        self.lock = threading.Lock()
        self.current: dict[str, Any] | None = None
        self.process: subprocess.Popen[str] | None = None

    def public_current(self) -> dict[str, Any] | None:
        with self.lock:
            if self.current is None:
                return None
            return json.loads(json.dumps(self.current))

    def start(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            if self.current and self.current["status"] in ("queued", "running", "cancelling"):
                raise RuntimeError("An analysis is already running.")

        output_root = Path(str(payload.get("output_root", "")).strip()).expanduser()
        if not str(payload.get("output_root", "")).strip():
            raise ValueError("An output folder is required.")
        if output_root.exists() and not output_root.is_dir():
            raise ValueError("The output location must be a folder.")
        output_root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("analysis_%Y%m%d_%H%M%S")
        output_dir = output_root / stamp
        suffix = 2
        while output_dir.exists():
            output_dir = output_root / f"{stamp}_{suffix}"
            suffix += 1
        output_dir.mkdir(parents=True)

        try:
            command, settings = build_analysis_command(payload, output_dir)
        except Exception:
            output_dir.rmdir()
            raise
        self.store.update_settings(settings)

        job = {
            "id": uuid.uuid4().hex[:12],
            "status": "queued",
            "created_at": utc_timestamp(),
            "completed_at": None,
            "input_path": settings["input_path"],
            "output_dir": str(output_dir.resolve()),
            "mode": settings["mode"],
            "log": [],
            "results": [],
            "exit_code": None,
        }
        with self.lock:
            if self.current and self.current["status"] in ("queued", "running", "cancelling"):
                output_dir.rmdir()
                raise RuntimeError("An analysis is already running.")
            self.current = job
        threading.Thread(target=self._run, args=(command,), daemon=True).start()
        return self.public_current() or job

    def _append_log(self, line: str) -> None:
        with self.lock:
            if self.current is not None:
                self.current["log"].append(line.rstrip())
                self.current["log"] = self.current["log"][-500:]

    def _run(self, command: list[str]) -> None:
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            process = subprocess.Popen(
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
            self._finish("failed", -1, f"Could not start analyzer: {exc}")
            return

        with self.lock:
            self.process = process
            should_cancel = bool(self.current and self.current["status"] == "cancelling")
            if self.current is not None and not should_cancel:
                self.current["status"] = "running"
        if should_cancel:
            process.terminate()
        assert process.stdout is not None
        for line in process.stdout:
            self._append_log(line)
        exit_code = process.wait()
        with self.lock:
            was_cancelling = bool(self.current and self.current["status"] == "cancelling")
        status = "cancelled" if was_cancelling else ("completed" if exit_code == 0 else "failed")
        self._finish(status, exit_code)

    def _finish(self, status: str, exit_code: int, error: str | None = None) -> None:
        with self.lock:
            if self.current is None:
                return
            if error:
                self.current["log"].append(error)
            output_dir = Path(self.current["output_dir"])
            self.current["status"] = status
            self.current["exit_code"] = exit_code
            self.current["completed_at"] = utc_timestamp()
            self.current["results"] = result_files(output_dir)
            entry = json.loads(json.dumps(self.current))
            entry.pop("log", None)
            self.process = None
        self.store.add_history(entry)

    def cancel(self) -> dict[str, Any]:
        with self.lock:
            if not self.current or self.current["status"] not in ("queued", "running"):
                raise RuntimeError("There is no running analysis to cancel.")
            self.current["status"] = "cancelling"
            process = self.process
        if process is not None:
            process.terminate()
        return self.public_current() or {}

    def output_for_id(self, job_id: str) -> Path | None:
        current = self.public_current()
        if current and current.get("id") == job_id:
            return Path(current["output_dir"])
        for entry in self.store.snapshot()["history"]:
            if entry.get("id") == job_id and entry.get("output_dir"):
                return Path(entry["output_dir"])
        return None


class AppContext:
    def __init__(self, state_path: Path = STATE_PATH):
        self.store = StateStore(state_path)
        self.jobs = AnalysisJobs(self.store)


CONTEXT = AppContext()


class AppHandler(BaseHTTPRequestHandler):
    server_version = "VN300TeamApp/0.1"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def send_bytes(self, body: bytes, content_type: str, status: int = 200, cache: str = "no-store") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def send_json(self, payload: Any, status: int = 200) -> None:
        self.send_bytes(json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8", status)

    def read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Invalid request length.") from exc
        if length > 1_000_000:
            raise ValueError("Request body is too large.")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Request body must be valid JSON.") from exc
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object.")
        return payload

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/":
            self._send_static("index.html")
        elif path.startswith("/static/"):
            self._send_static(path.removeprefix("/static/"))
        elif path == "/api/bootstrap":
            state = CONTEXT.store.snapshot()
            self.send_json({"settings": state["settings"], "history": state["history"], "job": CONTEXT.jobs.public_current()})
        elif path == "/api/job":
            self.send_json({"job": CONTEXT.jobs.public_current()})
        elif path == "/api/pi/check":
            self._check_pi(urllib.parse.parse_qs(parsed.query).get("url", [""])[0])
        elif path.startswith("/results/"):
            self._send_result(path)
        else:
            self.send_json({"error": "Not found."}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        try:
            payload = self.read_json()
            if parsed.path == "/api/settings":
                if "pi_url" in payload:
                    payload["pi_url"] = normalize_pi_url(str(payload["pi_url"]))
                self.send_json({"settings": CONTEXT.store.update_settings(payload)})
            elif parsed.path == "/api/analysis/start":
                self.send_json({"job": CONTEXT.jobs.start(payload)}, HTTPStatus.ACCEPTED)
            elif parsed.path == "/api/analysis/cancel":
                self.send_json({"job": CONTEXT.jobs.cancel()})
            elif parsed.path == "/api/select-folder":
                self._select_folder(payload)
            elif parsed.path == "/api/open-output":
                self._open_output(payload)
            else:
                self.send_json({"error": "Not found."}, HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except RuntimeError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.CONFLICT)
        except OSError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _send_static(self, relative: str) -> None:
        candidate = (STATIC_DIR / relative).resolve()
        if not path_is_within(candidate, STATIC_DIR) or not candidate.is_file():
            self.send_json({"error": "Not found."}, HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in ("application/javascript", "application/json"):
            content_type += "; charset=utf-8"
        self.send_bytes(candidate.read_bytes(), content_type, cache="no-cache")

    def _send_result(self, request_path: str) -> None:
        parts = request_path.split("/", 3)
        if len(parts) != 4:
            self.send_json({"error": "Result path is incomplete."}, HTTPStatus.NOT_FOUND)
            return
        job_id, relative = parts[2], urllib.parse.unquote(parts[3])
        root = CONTEXT.jobs.output_for_id(job_id)
        if root is None:
            self.send_json({"error": "Analysis result was not found."}, HTTPStatus.NOT_FOUND)
            return
        candidate = (root / relative).resolve()
        if not path_is_within(candidate, root) or not candidate.is_file():
            self.send_json({"error": "Analysis result was not found."}, HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if content_type.startswith("text/"):
            content_type += "; charset=utf-8"
        self.send_bytes(candidate.read_bytes(), content_type)

    def _check_pi(self, value: str) -> None:
        try:
            base_url = normalize_pi_url(value)
            api_url = urllib.parse.urljoin(base_url, "api/latest")
            request = urllib.request.Request(api_url, headers={"User-Agent": self.server_version})
            started = time.monotonic()
            with urllib.request.urlopen(request, timeout=2.5) as response:
                raw = response.read(1_000_000)
            data = json.loads(raw.decode("utf-8"))
            self.send_json({
                "online": True,
                "url": base_url,
                "latency_ms": round((time.monotonic() - started) * 1000),
                "status": data.get("status", "online"),
                "logging": bool(data.get("logging")),
                "session": data.get("session", ""),
            })
        except (ValueError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            self.send_json({"online": False, "error": str(exc)}, HTTPStatus.BAD_GATEWAY)

    def _select_folder(self, payload: dict[str, Any]) -> None:
        initial = Path(str(payload.get("initial", "")).strip()).expanduser()
        if not initial.is_dir():
            initial = REPO_ROOT
        try:
            import tkinter as tk
            from tkinter import filedialog
        except ImportError as exc:
            raise RuntimeError(f"Folder picker is unavailable: {exc}") from exc
        try:
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            selected = filedialog.askdirectory(initialdir=str(initial), mustexist=True)
            root.destroy()
        except tk.TclError as exc:
            raise RuntimeError(f"Folder picker is unavailable: {exc}") from exc
        self.send_json({"path": selected})

    def _open_output(self, payload: dict[str, Any]) -> None:
        job_id = str(payload.get("job_id", ""))
        output = CONTEXT.jobs.output_for_id(job_id)
        if output is None or not output.is_dir():
            raise ValueError("Analysis output folder was not found.")
        if os.name == "nt":
            os.startfile(output)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(output)])
        else:
            subprocess.Popen(["xdg-open", str(output)])
        self.send_json({"ok": True})


def create_server(host: str, preferred_port: int) -> tuple[ThreadingHTTPServer, int]:
    last_error: OSError | None = None
    for port in range(preferred_port, preferred_port + 10):
        try:
            return ThreadingHTTPServer((host, port), AppHandler), port
        except OSError as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    server, port = create_server(args.host, args.port)
    url = f"http://{args.host}:{port}/"
    print(f"VN300 Team Tools app: {url}", flush=True)
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
