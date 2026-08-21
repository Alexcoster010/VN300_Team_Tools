#!/usr/bin/env python3
"""
VN-300 button-controlled logger and lightweight live dashboard for Raspberry Pi.

Behavior:
- Starts at boot and waits idle.
- LOG button toggles logging on/off.
- POWER button hold stops logging and shuts the Pi down.
- While logging, writes raw .bin and parsed per-message CSV files.
- While running, serves a browser dashboard on http://<pi-ip>:8080/.

Default wiring uses buttons from GPIO pin to GND with internal pull-ups:
- LOG button: BCM GPIO 17
- POWER button: BCM GPIO 27
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import getpass
import json
import logging
import math
import os
import re
import shlex
import shutil
import signal
import subprocess
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

import serial
from serial.tools import list_ports

try:
    import can
except ImportError:
    can = None


READ_SIZE = 4096
SERIAL_TIMEOUT_S = 0.25
FLUSH_INTERVAL_S = 1.0
RECONNECT_DELAY_S = 2.0
BUTTON_DEBOUNCE_S = 0.35
POWER_HOLD_S = 2.0
MAX_LIVE_DELTA_POS_UNCERTAINTY_M = 4.0
MAX_ASCII_BUFFER_BYTES = 16384
MIN_FREE_SPACE_BYTES = 250 * 1024 * 1024
USB_LOG_WAIT_S = 20.0
USB_LOG_RETRY_S = 1.0
LOCAL_FALLBACK = Path.home() / "vn300_logs"
DEFAULT_CAN_SIGNAL_MAP = Path(__file__).with_name("motec_can_signal_map.csv")
LOGGER_VERSION_PATH = Path(__file__).with_name("PI_LOGGER_VERSION")
ASCII_MESSAGE_RE = re.compile(r"^VN[A-Z0-9]{2,12}$")
VN300_BINARY_HEADER = bytes.fromhex("fa 7f f9 1f 4c 00 0d 06 bf a0 04 00 c6 00 1b 06 18 a2 02 00")
VN300_BINARY_PACKET_LEN = 506
VN300_BINARY_PAYLOAD_LEN = 484
RUN_FILE_RE = re.compile(r"^VN300_(\d{4}-\d{2}-\d{2})_RUN(\d{3})_")
RUN_METADATA_FIELDS = [
    "session_file",
    "run_id",
    "run_number",
    "driver",
    "date",
    "test_location",
    "test_type",
    "course",
    "car_config",
    "tire_compound",
    "cold_fl_psi",
    "cold_fr_psi",
    "cold_rl_psi",
    "cold_rr_psi",
    "hot_fl_psi",
    "hot_fr_psi",
    "hot_rl_psi",
    "hot_rr_psi",
    "ambient_temp_f",
    "track_temp_f",
    "front_camber_deg",
    "rear_camber_deg",
    "front_toe_deg",
    "rear_toe_deg",
    "ride_height_front_mm",
    "ride_height_rear_mm",
    "damper_front",
    "damper_rear",
    "anti_roll_bar_front",
    "anti_roll_bar_rear",
    "brake_bias",
    "aero_config",
    "battery_or_fuel_state",
    "valid_run",
    "notes",
]


def read_logger_version() -> str:
    try:
        version = LOGGER_VERSION_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return "development"
    return version or "development"


LOGGER_VERSION = read_logger_version()

FIELD_NAMES = {
    "VNYPR": ["Yaw_deg", "Pitch_deg", "Roll_deg"],
    "VNIMU": [
        "Mag_X", "Mag_Y", "Mag_Z",
        "Accel_X", "Accel_Y", "Accel_Z",
        "Gyro_X", "Gyro_Y", "Gyro_Z",
        "Temp_C", "Pressure_kPa",
    ],
    "VNINS": [
        "Time_s", "GpsWeek", "InsStatus",
        "Yaw_deg", "Pitch_deg", "Roll_deg",
        "Latitude_deg", "Longitude_deg", "Altitude_m",
        "Vel_N_mps", "Vel_E_mps", "Vel_D_mps",
        "AttUncertainty_deg", "PosUncertainty_m", "VelUncertainty_mps",
    ],
    "VNISL": [
        "Yaw_deg", "Pitch_deg", "Roll_deg",
        "Latitude_deg", "Longitude_deg", "Altitude_m",
        "Vel_N_mps", "Vel_E_mps", "Vel_D_mps",
        "Accel_X_mps2", "Accel_Y_mps2", "Accel_Z_mps2",
        "Gyro_X_rps", "Gyro_Y_rps", "Gyro_Z_rps",
    ],
    "VNMAR": [
        "Mag_X", "Mag_Y", "Mag_Z",
        "Accel_X", "Accel_Y", "Accel_Z",
        "Gyro_X", "Gyro_Y", "Gyro_Z",
    ],
    "VNGPS": [
        "GpsTow_s", "GpsWeek", "GpsFix", "NumSats",
        "Latitude_deg", "Longitude_deg", "Altitude_m",
        "Vel_N_mps", "Vel_E_mps", "Vel_D_mps",
        "PosAcc_N_m", "PosAcc_E_m", "PosAcc_D_m",
        "SpeedAcc_mps", "TimeAcc_s",
    ],
}

stop_requested = threading.Event()
logging_requested = threading.Event()
setup_stream_requested = threading.Event()
active_session_stop = threading.Event()
shutdown_requested = threading.Event()
state_lock = threading.Lock()
run_metadata_lock = threading.Lock()
active_log_dir = None
active_base_log_dir = None
active_log_destination_type = None
latest_packet = {
    "logger_version": LOGGER_VERSION,
    "status": "idle",
    "logging": False,
    "setup_streaming": False,
    "session": None,
    "message_type": None,
    "updated_monotonic": None,
    "fields": {},
    "checksum_ok": None,
    "raw_bytes": 0,
    "ascii_packets": 0,
    "binary_bytes": 0,
    "binary_packets": 0,
    "bad_binary_packets": 0,
    "parse_source": None,
    "warning": None,
    "log_dir": None,
    "base_log_dir": None,
    "log_destination": None,
    "log_health": "unknown",
    "log_write_error": None,
    "free_space_mb": None,
    "last_flush_s": None,
    "can": {
        "enabled": False,
        "status": "disabled",
        "channel": None,
        "frames": 0,
        "decoded_frames": 0,
        "decode_errors": 0,
        "last_id": None,
        "last_age_s": None,
    },
}
run_metadata = {field: "" for field in RUN_METADATA_FIELDS if field not in ("session_file", "run_id", "run_number", "date")}
run_metadata.update({
    "driver": "",
    "test_location": "",
    "test_type": "",
    "course": "",
    "car_config": "",
    "tire_compound": "",
    "valid_run": "yes",
    "notes": "",
})

timing_lock = threading.Lock()
track_config = {
    "mode": None,
    "start_line": None,
    "finish_line": None,
    "min_speed_mph": 5.0,
    "min_gap_s": 8.0,
}
timing_state = {
    "configured": False,
    "active": False,
    "status": "not configured",
    "lap_count": 0,
    "current_start_s": None,
    "current_elapsed_s": None,
    "best_lap_s": None,
    "last_lap_s": None,
    "last_delta_s": None,
    "live_delta_s": None,
    "live_delta_available": False,
    "live_delta_error": None,
    "current_distance_m": None,
    "current_start_warning": "",
    "completed": [],
    "current_trace": [],
    "best_trace": [],
    "last_start_cross_s": None,
    "last_finish_cross_s": None,
    "last_point": None,
    "last_segment_point": None,
}


def stop_handler(signum, frame):
    stop_requested.set()
    setup_stream_requested.clear()
    active_session_stop.set()


def find_serial_port(requested: Optional[str]) -> Optional[str]:
    if requested:
        return requested if Path(requested).exists() else None

    ports = list(list_ports.comports())
    for port in ports:
        text = f"{port.device} {port.description} {port.manufacturer or ''}".lower()
        if any(term in text for term in ("vectornav", "ftdi", "usb serial", "usb-serial")):
            return port.device

    for port in ports:
        if port.device.startswith(("/dev/ttyUSB", "/dev/ttyACM")):
            return port.device

    return None


def find_usb_log_directory() -> Optional[Path]:
    candidates = []
    seen = set()

    def add_candidate(path: Path):
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved in seen:
            return
        seen.add(resolved)
        candidates.append(path)

    try:
        mount_lines = Path("/proc/mounts").read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        mount_lines = []
    for mount_path in mount_lines:
        parts = mount_path.split()
        if len(parts) < 2:
            continue
        mount_point = Path(parts[1].replace("\\040", " "))
        if mount_point in (Path("/media"), Path("/mnt")):
            continue
        if any(mount_point == root or root in mount_point.parents for root in (Path("/media"), Path("/mnt"))):
            add_candidate(mount_point)

    for root in (Path("/media"), Path("/mnt")):
        if not root.exists():
            continue
        for first in root.iterdir():
            if first.is_dir():
                try:
                    for child in first.iterdir():
                        if child.is_dir():
                            try:
                                if child.is_mount():
                                    add_candidate(child)
                            except OSError:
                                pass
                except PermissionError:
                    pass
                if first.name != getpass.getuser() and first.is_mount():
                    add_candidate(first)

    for candidate in candidates:
        try:
            test = candidate / ".vn300_write_test"
            test.write_text("ok")
            test.unlink()
            log_dir = candidate / "VN300_LOGS"
            log_dir.mkdir(parents=True, exist_ok=True)
            return log_dir
        except (OSError, PermissionError):
            continue
    return None


def find_log_directory() -> Path:
    deadline = time.monotonic() + USB_LOG_WAIT_S
    while True:
        log_dir = find_usb_log_directory()
        if log_dir is not None:
            return log_dir
        if time.monotonic() >= deadline:
            break
        logging.info("No writable USB log drive found; retrying for %.0f seconds", USB_LOG_RETRY_S)
        time.sleep(USB_LOG_RETRY_S)

    LOCAL_FALLBACK.mkdir(parents=True, exist_ok=True)
    return LOCAL_FALLBACK


def log_destination_type(base_log_dir: Path) -> str:
    try:
        resolved = base_log_dir.resolve()
        fallback = LOCAL_FALLBACK.resolve()
    except OSError:
        resolved = base_log_dir
        fallback = LOCAL_FALLBACK
    if resolved == fallback:
        return "pi local fallback"
    return "flash drive"


def cpu_temperature_c() -> Optional[float]:
    try:
        raw = Path("/sys/class/thermal/thermal_zone0/temp").read_text(encoding="utf-8").strip()
        return float(raw) / 1000.0
    except (OSError, ValueError):
        return None


def set_log_health(
    health: str,
    *,
    log_dir: Optional[Path] = None,
    base_log_dir: Optional[Path] = None,
    destination: Optional[str] = None,
    free_space: Optional[int] = None,
    write_error: Optional[str] = None,
    last_flush_s: Optional[float] = None,
):
    with state_lock:
        latest_packet["log_health"] = health
        if log_dir is not None:
            latest_packet["log_dir"] = str(log_dir)
        if base_log_dir is not None:
            latest_packet["base_log_dir"] = str(base_log_dir)
        if destination is not None:
            latest_packet["log_destination"] = destination
        if free_space is not None:
            latest_packet["free_space_mb"] = free_space // (1024 * 1024)
        if write_error is not None:
            latest_packet["log_write_error"] = write_error
        if last_flush_s is not None:
            latest_packet["last_flush_s"] = last_flush_s


def create_boot_log_directory(base_log_dir: Path) -> Path:
    timestamp = dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    for index in range(100):
        suffix = "" if index == 0 else f"_{index:02d}"
        boot_dir = base_log_dir / f"VN300_BOOT_{timestamp}{suffix}"
        try:
            boot_dir.mkdir(parents=True, exist_ok=False)
            return boot_dir
        except FileExistsError:
            continue
    raise FileExistsError(f"Could not create a unique boot log folder under {base_log_dir}")


def safe_run_label(value: str, max_len: int = 24) -> str:
    cleaned = "".join(char.upper() if char.isalnum() else "_" for char in value.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned[:max_len]


def next_run_number(base_log_dir: Path, date_text: str) -> int:
    highest = 0
    if not base_log_dir.exists():
        return 1
    for path in base_log_dir.rglob(f"VN300_{date_text}_RUN*"):
        match = RUN_FILE_RE.match(path.name)
        if match and match.group(1) == date_text:
            highest = max(highest, int(match.group(2)))
    return highest + 1


def build_run_identity(base_log_dir: Path, metadata_snapshot: dict, date_text: Optional[str] = None, date_source: str = "pi_clock") -> dict:
    date_text = date_text or dt.datetime.now().strftime("%Y-%m-%d")
    run_number = next_run_number(base_log_dir, date_text)
    run_id = f"VN300_{date_text}_RUN{run_number:03d}"
    return {
        "date": date_text,
        "date_source": date_source,
        "run_number": run_number,
        "run_id": run_id,
        "file_prefix": run_id,
    }


def valid_vn_utc_datetime(fields: dict, prefix: str = "TimeUtc") -> Optional[dt.datetime]:
    try:
        year = int(fields.get(f"{prefix}_Year"))
        month = int(fields.get(f"{prefix}_Month"))
        day = int(fields.get(f"{prefix}_Day"))
        hour = int(fields.get(f"{prefix}_Hour", 0))
        minute = int(fields.get(f"{prefix}_Minute", 0))
        second = int(fields.get(f"{prefix}_Second", 0))
        millisecond = int(fields.get(f"{prefix}_Millisecond", 0))
    except (TypeError, ValueError):
        return None
    if not (2020 <= year <= 2100):
        return None
    try:
        return dt.datetime(year, month, day, hour, minute, second, millisecond * 1000, tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def sensor_utc_datetime_from_fields(fields: dict) -> Optional[dt.datetime]:
    return valid_vn_utc_datetime(fields, "TimeUtc") or valid_vn_utc_datetime(fields, "Gnss1Utc")


def rename_run_files(log_dir: Path, old_prefix: str, new_prefix: str) -> list[dict]:
    if old_prefix == new_prefix:
        return []
    paths = sorted(log_dir.glob(f"{old_prefix}*"))
    blocked = [
        {
            "source": str(path),
            "target": str(path.with_name(new_prefix + path.name[len(old_prefix):])),
            "renamed": False,
            "error": "target exists",
        }
        for path in paths
        if path.with_name(new_prefix + path.name[len(old_prefix):]).exists()
    ]
    if blocked:
        return blocked
    results = []
    for path in paths:
        target = path.with_name(new_prefix + path.name[len(old_prefix):])
        try:
            path.replace(target)
            results.append({
                "source": str(path),
                "target": str(target),
                "renamed": True,
                "error": "",
            })
        except OSError as exc:
            results.append({
                "source": str(path),
                "target": str(target),
                "renamed": False,
                "error": repr(exc),
            })
    return results


def free_space_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


def write_session_metadata(path: Path, metadata: dict):
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)


def write_run_metadata_csv(log_dir: Path, row: dict):
    path = log_dir / "VN300_run_metadata.csv"
    exists = path.exists()
    with path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=RUN_METADATA_FIELDS, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in RUN_METADATA_FIELDS})


def write_log_status_file(log_dir: Path, payload: dict):
    path = log_dir / "VN300_logger_status.json"
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)


def parse_bool_text(value, default: bool = False) -> bool:
    if value in ("", None):
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "y", "signed")


def parse_int_text(value, default: Optional[int] = None) -> Optional[int]:
    if value in ("", None):
        return default
    text = str(value).strip()
    try:
        return int(text, 0)
    except ValueError:
        return int(text, 16)


def load_can_signal_map(path: Optional[Path]) -> dict[int, list[dict]]:
    if not path or not path.exists():
        return {}
    signal_map: dict[int, list[dict]] = {}
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            channel = (row.get("channel") or row.get("name") or "").strip()
            can_id_text = row.get("can_id") or row.get("can_id_hex")
            can_id = parse_int_text(can_id_text, None)
            if not channel or can_id is None:
                continue
            spec = {
                "channel": safe_filename_part(channel),
                "can_id": can_id,
                "start_bit": int(float(row.get("start_bit") or 0)),
                "length_bits": int(float(row.get("length_bits") or row.get("bit_length") or 0)),
                "byte_order": (row.get("byte_order") or "little").strip().lower(),
                "signed": parse_bool_text(row.get("signed"), False),
                "scale": float(row.get("scale") or 1.0),
                "offset": float(row.get("offset") or 0.0),
                "unit": (row.get("unit") or "").strip(),
            }
            if spec["length_bits"] <= 0:
                continue
            signal_map.setdefault(can_id, []).append(spec)
    return signal_map


def decode_can_signal(data: bytes, spec: dict) -> tuple[int, float]:
    length_bits = spec["length_bits"]
    start_bit = spec["start_bit"]
    total_bits = len(data) * 8
    byte_order = spec["byte_order"]
    if byte_order in ("big", "motorola", "msb"):
        raw_full = int.from_bytes(data, byteorder="big", signed=False)
        shift = total_bits - start_bit - length_bits
    else:
        raw_full = int.from_bytes(data, byteorder="little", signed=False)
        shift = start_bit
    if shift < 0 or start_bit + length_bits > total_bits:
        raise ValueError(f"signal {spec['channel']} does not fit in CAN payload")
    raw_value = (raw_full >> shift) & ((1 << length_bits) - 1)
    if spec["signed"] and raw_value & (1 << (length_bits - 1)):
        raw_value -= 1 << length_bits
    physical_value = raw_value * spec["scale"] + spec["offset"]
    return raw_value, physical_value


class CanCsvWriter:
    def __init__(self, log_dir: Path, file_prefix: str):
        self.raw_path = log_dir / f"{file_prefix}_MOTEC_RAW_CAN.csv"
        self.channels_path = log_dir / f"{file_prefix}_MOTEC_CHANNELS.csv"
        self.raw_file = self.raw_path.open("w", newline="", buffering=1)
        self.channel_file = self.channels_path.open("w", newline="", buffering=1)
        self.raw_writer = csv.writer(self.raw_file)
        self.channel_writer = csv.writer(self.channel_file)
        self.raw_writer.writerow([
            "Pi_Logger_Elapsed_Time_s",
            "System_Time_ISO",
            "CAN_Interface",
            "CAN_ID",
            "DLC",
            "Data_Hex",
            "Is_Extended_ID",
            "Is_Error_Frame",
        ])
        self.channel_writer.writerow([
            "Pi_Logger_Elapsed_Time_s",
            "System_Time_ISO",
            "CAN_ID",
            "Channel",
            "Value",
            "Unit",
            "Raw_Value",
        ])
        logging.info("CAN raw CSV output: %s", self.raw_path)
        logging.info("CAN decoded CSV output: %s", self.channels_path)

    def write_raw(self, elapsed_s: float, interface_name: str, msg):
        timestamp = dt.datetime.now().isoformat(timespec="milliseconds")
        data_hex = bytes(msg.data).hex(" ").upper()
        self.raw_writer.writerow([
            f"{elapsed_s:.6f}",
            timestamp,
            interface_name,
            f"0x{msg.arbitration_id:X}",
            len(msg.data),
            data_hex,
            bool(msg.is_extended_id),
            bool(getattr(msg, "is_error_frame", False)),
        ])

    def write_channels(self, elapsed_s: float, msg, decoded_rows: list[tuple[dict, int, float]]):
        timestamp = dt.datetime.now().isoformat(timespec="milliseconds")
        for spec, raw_value, physical_value in decoded_rows:
            self.channel_writer.writerow([
                f"{elapsed_s:.6f}",
                timestamp,
                f"0x{msg.arbitration_id:X}",
                spec["channel"],
                f"{physical_value:.6f}",
                spec["unit"],
                raw_value,
            ])

    def flush(self):
        for file_obj in (self.raw_file, self.channel_file):
            file_obj.flush()
            os.fsync(file_obj.fileno())

    def close(self):
        for file_obj in (self.raw_file, self.channel_file):
            try:
                file_obj.flush()
                os.fsync(file_obj.fileno())
            finally:
                file_obj.close()


def update_can_status(**updates):
    with state_lock:
        can_state = latest_packet.setdefault("can", {})
        can_state.update(updates)
        if "_last_monotonic" in can_state:
            can_state["last_age_s"] = time.monotonic() - can_state["_last_monotonic"]


def run_can_logger(
    config: dict,
    log_dir: Path,
    file_prefix: str,
    session_start_monotonic: float,
    stop_event: threading.Event,
):
    if not config.get("enabled"):
        update_can_status(enabled=False, status="disabled")
        return
    if can is None:
        update_can_status(enabled=True, status="python-can missing", decode_errors=1)
        logging.error("CAN logging requested but python-can is not installed")
        return

    signal_map = load_can_signal_map(config.get("signal_map"))
    interface_name = config["channel"]
    writers = None
    bus = None
    frames = 0
    decoded_frames = 0
    decode_errors = 0
    try:
        bus_kwargs = {
            "interface": config["interface"],
            "channel": config["channel"],
        }
        if config.get("bitrate"):
            bus_kwargs["bitrate"] = config["bitrate"]
        bus = can.Bus(**bus_kwargs)
        writers = CanCsvWriter(log_dir, file_prefix)
        update_can_status(
            enabled=True,
            status="online",
            channel=config["channel"],
            frames=0,
            decoded_frames=0,
            decode_errors=0,
            last_id=None,
            last_age_s=None,
        )
        logging.info(
            "CAN logging enabled: interface=%s channel=%s bitrate=%s signals=%d",
            config["interface"],
            config["channel"],
            config.get("bitrate") or "",
            sum(len(items) for items in signal_map.values()),
        )
        while not stop_requested.is_set() and not stop_event.is_set():
            msg = bus.recv(timeout=0.2)
            if msg is None:
                update_can_status(status="online")
                continue
            elapsed_s = time.monotonic() - session_start_monotonic
            frames += 1
            writers.write_raw(elapsed_s, interface_name, msg)
            decoded_rows = []
            for spec in signal_map.get(msg.arbitration_id, []):
                try:
                    raw_value, physical_value = decode_can_signal(bytes(msg.data), spec)
                    decoded_rows.append((spec, raw_value, physical_value))
                except (ValueError, OverflowError) as exc:
                    decode_errors += 1
                    logging.warning("CAN decode failed for %s: %s", spec["channel"], exc)
            if decoded_rows:
                decoded_frames += 1
                writers.write_channels(elapsed_s, msg, decoded_rows)
            update_can_status(
                enabled=True,
                status="online",
                channel=config["channel"],
                frames=frames,
                decoded_frames=decoded_frames,
                decode_errors=decode_errors,
                last_id=f"0x{msg.arbitration_id:X}",
                _last_monotonic=time.monotonic(),
            )
    except Exception as exc:
        decode_errors += 1
        update_can_status(enabled=True, status=f"error: {exc}", decode_errors=decode_errors)
        logging.error("CAN logger stopped: %s", exc)
    finally:
        if writers:
            writers.close()
        if bus:
            try:
                bus.shutdown()
            except Exception:
                pass


def set_latest_warning(message: Optional[str]):
    with state_lock:
        latest_packet["warning"] = message


def increment_bad_binary_packets():
    with state_lock:
        latest_packet["bad_binary_packets"] += 1


def reset_latest_for_session(session_name: str):
    with state_lock:
        latest_packet.update({
            "status": "logging",
            "logging": True,
            "setup_streaming": False,
            "session": session_name,
            "message_type": None,
            "updated_monotonic": None,
            "fields": {},
            "checksum_ok": None,
            "raw_bytes": 0,
            "ascii_packets": 0,
            "binary_bytes": 0,
            "binary_packets": 0,
            "bad_binary_packets": 0,
            "parse_source": None,
            "warning": None,
        })


def reset_latest_for_setup_stream():
    with state_lock:
        latest_packet.update({
            "status": "track setup",
            "logging": False,
            "setup_streaming": True,
            "session": None,
            "message_type": None,
            "updated_monotonic": None,
            "fields": {},
            "checksum_ok": None,
            "raw_bytes": 0,
            "ascii_packets": 0,
            "binary_bytes": 0,
            "binary_packets": 0,
            "bad_binary_packets": 0,
            "parse_source": None,
            "warning": None,
        })


def clean_metadata_payload(payload: dict) -> dict:
    cleaned = {}
    editable_fields = [field for field in RUN_METADATA_FIELDS if field not in ("session_file", "run_id", "run_number", "date")]
    for field in editable_fields:
        value = payload.get(field, "")
        if value is None:
            value = ""
        cleaned[field] = str(value).strip()
    cleaned["valid_run"] = cleaned.get("valid_run") or "yes"
    return cleaned


def update_run_metadata(payload: dict):
    cleaned = clean_metadata_payload(payload)
    with run_metadata_lock:
        run_metadata.update(cleaned)


def current_run_metadata():
    with run_metadata_lock:
        return dict(run_metadata)


def public_run_metadata_snapshot():
    base_dir = active_base_log_dir or active_log_dir or LOCAL_FALLBACK
    date_text = dt.datetime.now().strftime("%Y-%m-%d")
    try:
        next_number = next_run_number(base_dir, date_text)
    except OSError:
        next_number = None
    snapshot = current_run_metadata()
    return {
        "metadata": snapshot,
        "date": date_text,
        "next_run_number": next_number,
        "next_run_id": None if next_number is None else f"VN300_{date_text}_RUN{next_number:03d}",
    }


def checksum_ok(sentence_without_dollar: str, checksum_text: str):
    if not checksum_text:
        return ""
    value = 0
    for char in sentence_without_dollar:
        value ^= ord(char)
    try:
        return value == int(checksum_text[:2], 16)
    except ValueError:
        return False


def field_names_for(message_type: str, count: int):
    known = FIELD_NAMES.get(message_type, [])
    return [known[i] if i < len(known) else f"Field_{i + 1}" for i in range(count)]


def safe_filename_part(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in value)
    return cleaned[:32] or "UNKNOWN"


def parse_ascii_line(line: str):
    text = line.strip("\x00\r\n ")
    if not text.startswith("$"):
        return None
    if "\x00" in text:
        return None

    body = text[1:]
    checksum_text = ""
    if "*" in body:
        payload, checksum_text = body.rsplit("*", 1)
    else:
        payload = body

    fields = payload.split(",")
    if not fields:
        return None
    message_type = fields[0].strip()
    if not ASCII_MESSAGE_RE.fullmatch(message_type):
        return None

    return {
        "message_type": message_type,
        "values": fields[1:],
        "checksum": checksum_text[:2],
        "checksum_ok": checksum_ok(payload, checksum_text),
        "raw": text,
    }


def vn_utc_dict(raw: bytes):
    if len(raw) != 8:
        return {}
    return {
        "Year": 2000 + raw[0],
        "Month": raw[1],
        "Day": raw[2],
        "Hour": raw[3],
        "Minute": raw[4],
        "Second": raw[5],
        "Millisecond": int.from_bytes(raw[6:8], "little"),
    }


class BinaryReader:
    def __init__(self, data: bytes, offset: int = 0):
        self.data = data
        self.offset = offset

    def u8(self):
        value = self.data[self.offset]
        self.offset += 1
        return value

    def u16(self):
        value = struct.unpack_from("<H", self.data, self.offset)[0]
        self.offset += 2
        return value

    def u64(self):
        value = struct.unpack_from("<Q", self.data, self.offset)[0]
        self.offset += 8
        return value

    def f32(self):
        value = struct.unpack_from("<f", self.data, self.offset)[0]
        self.offset += 4
        return value

    def f64(self):
        value = struct.unpack_from("<d", self.data, self.offset)[0]
        self.offset += 8
        return value

    def vecf(self, count: int):
        values = struct.unpack_from("<" + "f" * count, self.data, self.offset)
        self.offset += 4 * count
        return values

    def vecd(self, count: int):
        values = struct.unpack_from("<" + "d" * count, self.data, self.offset)
        self.offset += 8 * count
        return values

    def raw(self, count: int):
        value = self.data[self.offset:self.offset + count]
        self.offset += count
        return value


def add_vec(fields: dict, prefix: str, names: tuple[str, ...], values):
    for name, value in zip(names, values):
        fields[f"{prefix}_{name}"] = value


def parse_vn300_binary_packet(packet: bytes):
    if len(packet) != VN300_BINARY_PACKET_LEN or not packet.startswith(VN300_BINARY_HEADER):
        return None

    payload = packet[len(VN300_BINARY_HEADER):len(VN300_BINARY_HEADER) + VN300_BINARY_PAYLOAD_LEN]
    r = BinaryReader(payload)
    fields = {}

    fields["Binary_TimeStartup_ns"] = r.u64()
    fields["Pi_Elapsed_Time_s"] = fields["Binary_TimeStartup_ns"] / 1e9
    add_vec(fields, "Common_YPR", ("Yaw_deg", "Pitch_deg", "Roll_deg"), r.vecf(3))
    add_vec(fields, "Common_Quat", ("X", "Y", "Z", "W"), r.vecf(4))
    add_vec(fields, "Common_AngularRate", ("X_rps", "Y_rps", "Z_rps"), r.vecf(3))
    add_vec(fields, "Common_PosLla", ("Latitude_deg", "Longitude_deg", "Altitude_m"), r.vecd(3))
    add_vec(fields, "Common_VelNed", ("N_mps", "E_mps", "D_mps"), r.vecf(3))
    add_vec(fields, "Common_Accel", ("X_mps2", "Y_mps2", "Z_mps2"), r.vecf(3))
    add_vec(fields, "Common_Imu_Mag", ("X", "Y", "Z"), r.vecf(3))
    add_vec(fields, "Common_Imu_Accel", ("X_mps2", "Y_mps2", "Z_mps2"), r.vecf(3))
    add_vec(fields, "Common_Imu_Gyro", ("X_rps", "Y_rps", "Z_rps"), r.vecf(3))
    fields["Common_Imu_Temp_C"] = r.f32()
    fields["Common_Imu_Pressure_kPa"] = r.f32()
    add_vec(fields, "Common_MagPres_Mag", ("X", "Y", "Z"), r.vecf(3))
    fields["Common_MagPres_Pressure_kPa"] = r.f32()
    add_vec(fields, "Common_Deltas", ("DeltaTime_s", "DeltaTheta_X", "DeltaTheta_Y"), r.vecf(3))
    fields["Common_InsStatus"] = r.u16()

    fields["Time_GpsTow_ns"] = r.u64()
    fields["Time_GpsTow_s"] = fields["Time_GpsTow_ns"] / 1e9
    fields["Time_GpsWeek"] = r.u16()
    utc = vn_utc_dict(r.raw(8))
    for key, value in utc.items():
        fields[f"TimeUtc_{key}"] = value

    fields["ImuStatus"] = r.u16()
    add_vec(fields, "Imu_UncompAccel", ("X_mps2", "Y_mps2", "Z_mps2"), r.vecf(3))
    add_vec(fields, "Imu_UncompGyro", ("X_rps", "Y_rps", "Z_rps"), r.vecf(3))
    add_vec(fields, "Imu_Accel", ("X_mps2", "Y_mps2", "Z_mps2"), r.vecf(3))
    add_vec(fields, "Imu_AngularRate", ("X_rps", "Y_rps", "Z_rps"), r.vecf(3))

    utc = vn_utc_dict(r.raw(8))
    for key, value in utc.items():
        fields[f"Gnss1Utc_{key}"] = value
    fields["Gnss1Tow_ns"] = r.u64()
    fields["Gnss1Tow_s"] = fields["Gnss1Tow_ns"] / 1e9
    fields["Gnss1Week"] = r.u16()
    fields["Gnss1NumSats"] = r.u8()
    fields["Gnss1Fix"] = r.u8()
    add_vec(fields, "Gnss1PosLla", ("Latitude_deg", "Longitude_deg", "Altitude_m"), r.vecd(3))
    add_vec(fields, "Gnss1VelNed", ("N_mps", "E_mps", "D_mps"), r.vecf(3))
    add_vec(fields, "Gnss1Dop", ("G", "P", "T", "V", "H", "N", "E"), r.vecf(7))
    fields["Gnss1AltMSL_m"] = r.f64()

    add_vec(fields, "Attitude_YPR", ("Yaw_deg", "Pitch_deg", "Roll_deg"), r.vecf(3))
    add_vec(fields, "Attitude_Quat", ("X", "Y", "Z", "W"), r.vecf(4))
    add_vec(fields, "Attitude_LinBodyAcc", ("X_mps2", "Y_mps2", "Z_mps2"), r.vecf(3))
    add_vec(fields, "Attitude_LinAccelNed", ("N_mps2", "E_mps2", "D_mps2"), r.vecf(3))

    fields["InsStatus"] = r.u16()
    add_vec(fields, "Ins_PosLla", ("Latitude_deg", "Longitude_deg", "Altitude_m"), r.vecd(3))
    add_vec(fields, "Ins_VelBody", ("X_mps", "Y_mps", "Z_mps"), r.vecf(3))
    add_vec(fields, "Ins_VelNed", ("N_mps", "E_mps", "D_mps"), r.vecf(3))
    fields["Ins_PosU_m"] = r.f32()
    fields["Ins_VelU_mps"] = r.f32()

    fields["Gnss2NumSats"] = r.u8()
    fields["Gnss2Fix"] = r.u8()
    add_vec(fields, "Gnss2PosUncertainty", ("N_m", "E_m", "D_m"), r.vecf(3))
    add_vec(fields, "Gnss2Dop", ("G", "P", "T", "V", "H", "N", "E"), r.vecf(7))

    fields["Checksum_LowByte"] = packet[-2]
    fields["Checksum_HighByte"] = packet[-1]

    fields["Yaw_deg"] = fields["Common_YPR_Yaw_deg"]
    fields["Pitch_deg"] = fields["Common_YPR_Pitch_deg"]
    fields["Roll_deg"] = fields["Common_YPR_Roll_deg"]
    fields["Latitude_deg"] = fields["Common_PosLla_Latitude_deg"]
    fields["Longitude_deg"] = fields["Common_PosLla_Longitude_deg"]
    fields["Altitude_m"] = fields["Common_PosLla_Altitude_m"]
    fields["Vel_N_mps"] = fields["Common_VelNed_N_mps"]
    fields["Vel_E_mps"] = fields["Common_VelNed_E_mps"]
    fields["Vel_D_mps"] = fields["Common_VelNed_D_mps"]
    speed_mps = math.sqrt(fields["Vel_N_mps"] ** 2 + fields["Vel_E_mps"] ** 2 + fields["Vel_D_mps"] ** 2)
    fields["Speed_mps"] = speed_mps
    fields["Speed_mph"] = speed_mps * 2.2369362921
    fields["PosUncertainty_m"] = fields["Ins_PosU_m"]
    fields["VelUncertainty_mps"] = fields["Ins_VelU_mps"]
    fields["Longitudinal_Accel_mps2"] = fields["Common_Accel_X_mps2"]
    fields["Lateral_Accel_mps2"] = fields["Common_Accel_Y_mps2"]
    fields["Vertical_Accel_mps2"] = fields["Common_Accel_Z_mps2"]
    fields["Longitudinal_G"] = fields["Longitudinal_Accel_mps2"] / 9.80665
    fields["Lateral_G"] = fields["Lateral_Accel_mps2"] / 9.80665
    fields["Vertical_G"] = fields["Vertical_Accel_mps2"] / 9.80665
    sensor_utc = sensor_utc_datetime_from_fields(fields)
    if sensor_utc:
        fields["Sensor_UTC_Date"] = sensor_utc.strftime("%Y-%m-%d")
        fields["Sensor_UTC_DateTime"] = sensor_utc.isoformat()
    return fields


def values_as_fields(parsed: dict):
    names = field_names_for(parsed["message_type"], len(parsed["values"]))
    fields = {}
    for name, value in zip(names, parsed["values"]):
        try:
            fields[name] = float(value)
        except ValueError:
            fields[name] = value

    if {"Vel_N_mps", "Vel_E_mps", "Vel_D_mps"}.issubset(fields):
        vn = float(fields["Vel_N_mps"])
        ve = float(fields["Vel_E_mps"])
        vd = float(fields["Vel_D_mps"])
        speed_mps = math.sqrt(vn * vn + ve * ve + vd * vd)
        fields["Speed_mps"] = speed_mps
        fields["Speed_mph"] = speed_mps * 2.2369362921
    return fields


def project_xy(lat: float, lon: float, origin_lat: float, origin_lon: float):
    earth_radius_m = 6371000.0
    x = math.radians(lon - origin_lon) * earth_radius_m * math.cos(math.radians(origin_lat))
    y = math.radians(lat - origin_lat) * earth_radius_m
    return x, y


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    earth_radius_m = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * earth_radius_m * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def segment_intersection_fraction(p1, p2, q1, q2):
    px, py = p1
    rx, ry = p2[0] - p1[0], p2[1] - p1[1]
    qx, qy = q1
    sx, sy = q2[0] - q1[0], q2[1] - q1[1]
    denom = rx * sy - ry * sx
    if abs(denom) < 1e-9:
        return None
    qpx, qpy = qx - px, qy - py
    t = (qpx * sy - qpy * sx) / denom
    u = (qpx * ry - qpy * rx) / denom
    if 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0:
        return t
    return None


def line_crossing_time(prev_point: dict, cur_point: dict, line: dict):
    origin_lat = (line["lat1"] + line["lat2"]) / 2.0
    origin_lon = (line["lon1"] + line["lon2"]) / 2.0
    p1 = project_xy(prev_point["lat"], prev_point["lon"], origin_lat, origin_lon)
    p2 = project_xy(cur_point["lat"], cur_point["lon"], origin_lat, origin_lon)
    q1 = project_xy(line["lat1"], line["lon1"], origin_lat, origin_lon)
    q2 = project_xy(line["lat2"], line["lon2"], origin_lat, origin_lon)
    frac = segment_intersection_fraction(p1, p2, q1, q2)
    if frac is None:
        return None
    return prev_point["t"] + frac * (cur_point["t"] - prev_point["t"])


def gps_crossing_warning(prev_point: dict, cur_point: dict) -> str:
    pos_uncertainty = max(
        prev_point.get("pos_uncertainty_m") or 0.0,
        cur_point.get("pos_uncertainty_m") or 0.0,
    )
    if pos_uncertainty > MAX_LIVE_DELTA_POS_UNCERTAINTY_M:
        return f"Invalid GPS: uncertainty {pos_uncertainty:.1f} m > {MAX_LIVE_DELTA_POS_UNCERTAINTY_M:.1f} m"
    return ""


def reset_timing_state(status: str = "ready"):
    timing_state.update({
        "active": False,
        "status": status,
        "lap_count": 0,
        "current_start_s": None,
        "current_elapsed_s": None,
        "best_lap_s": None,
        "last_lap_s": None,
        "last_delta_s": None,
        "live_delta_s": None,
        "live_delta_available": False,
        "live_delta_error": None,
        "current_distance_m": None,
        "current_start_warning": "",
        "completed": [],
        "current_trace": [],
        "best_trace": [],
        "last_start_cross_s": None,
        "last_finish_cross_s": None,
        "last_point": None,
        "last_segment_point": None,
    })


def parse_line_config(payload: dict, prefix: str):
    return {
        "lat1": float(payload[f"{prefix}_lat1"]),
        "lon1": float(payload[f"{prefix}_lon1"]),
        "lat2": float(payload[f"{prefix}_lat2"]),
        "lon2": float(payload[f"{prefix}_lon2"]),
    }


def write_timing_config_csv(log_dir: Optional[Path], mode: str, start_line: dict, finish_line: Optional[dict]):
    if log_dir is None:
        return

    path = log_dir / "VN300_dashboard_timing_config.csv"
    exists = path.exists()
    with path.open("a", newline="") as f:
        writer = csv.writer(f)
        if not exists:
            writer.writerow([
                "Configured_At_Local",
                "Mode",
                "Start_Lat_1",
                "Start_Lon_1",
                "Start_Lat_2",
                "Start_Lon_2",
                "Finish_Lat_1",
                "Finish_Lon_1",
                "Finish_Lat_2",
                "Finish_Lon_2",
                "Min_Speed_mph",
                "Min_Gap_s",
            ])
        finish_line = finish_line or {}
        writer.writerow([
            dt.datetime.now().isoformat(timespec="seconds"),
            mode,
            start_line.get("lat1", ""),
            start_line.get("lon1", ""),
            start_line.get("lat2", ""),
            start_line.get("lon2", ""),
            finish_line.get("lat1", ""),
            finish_line.get("lon1", ""),
            finish_line.get("lat2", ""),
            finish_line.get("lon2", ""),
            track_config["min_speed_mph"],
            track_config["min_gap_s"],
        ])


def configure_timing(payload: dict):
    mode = str(payload.get("mode", "")).lower().strip()
    if mode not in ("lap", "autocross"):
        raise ValueError("mode must be lap or autocross")

    start_line = parse_line_config(payload, "start")
    finish_line = None
    if mode == "autocross":
        finish_line = parse_line_config(payload, "finish")

    with timing_lock:
        track_config["mode"] = mode
        track_config["start_line"] = start_line
        track_config["finish_line"] = finish_line
        track_config["min_speed_mph"] = float(payload.get("min_speed_mph", 5.0))
        track_config["min_gap_s"] = float(payload.get("min_gap_s", 8.0))
        timing_state["configured"] = True
        reset_timing_state("waiting for start")
        write_timing_config_csv(active_log_dir, mode, start_line, finish_line)


def start_timed_segment(start_s: float, cur_point: dict, status: str, warning: str = ""):
    timing_state["active"] = True
    timing_state["current_start_s"] = start_s
    timing_state["current_elapsed_s"] = 0.0
    timing_state["current_distance_m"] = 0.0
    timing_state["current_start_warning"] = warning
    timing_state["live_delta_s"] = None
    timing_state["live_delta_available"] = bool(timing_state["best_trace"])
    timing_state["live_delta_error"] = None
    timing_state["current_trace"] = [{
        "elapsed_s": 0.0,
        "distance_m": 0.0,
        "lat": cur_point["lat"],
        "lon": cur_point["lon"],
    }]
    timing_state["last_segment_point"] = cur_point
    timing_state["status"] = warning or status


def best_time_at_distance(distance_m: float):
    trace = timing_state["best_trace"]
    if len(trace) < 2:
        return None
    if distance_m <= trace[0]["distance_m"]:
        return trace[0]["elapsed_s"]
    for prev, cur in zip(trace, trace[1:]):
        if prev["distance_m"] <= distance_m <= cur["distance_m"]:
            span = cur["distance_m"] - prev["distance_m"]
            if span <= 0:
                return cur["elapsed_s"]
            frac = (distance_m - prev["distance_m"]) / span
            return prev["elapsed_s"] + frac * (cur["elapsed_s"] - prev["elapsed_s"])
    if timing_state["best_lap_s"] is not None:
        return timing_state["best_lap_s"]
    return None


def append_current_trace(cur_point: dict):
    if not timing_state["active"] or timing_state["current_start_s"] is None:
        return

    prev_segment_point = timing_state["last_segment_point"]
    if prev_segment_point is None:
        timing_state["last_segment_point"] = cur_point
        return

    elapsed_s = max(0.0, cur_point["t"] - timing_state["current_start_s"])
    current_distance = timing_state["current_distance_m"] or 0.0
    current_distance += haversine_m(
        prev_segment_point["lat"],
        prev_segment_point["lon"],
        cur_point["lat"],
        cur_point["lon"],
    )
    timing_state["current_elapsed_s"] = elapsed_s
    timing_state["current_distance_m"] = current_distance
    timing_state["last_segment_point"] = cur_point

    trace = timing_state["current_trace"]
    if not trace or elapsed_s - trace[-1]["elapsed_s"] >= 0.2:
        trace.append({
            "elapsed_s": elapsed_s,
            "distance_m": current_distance,
            "lat": cur_point["lat"],
            "lon": cur_point["lon"],
        })
        timing_state["current_trace"] = trace[-2500:]

    pos_uncertainty = cur_point.get("pos_uncertainty_m")
    if pos_uncertainty is not None and pos_uncertainty > MAX_LIVE_DELTA_POS_UNCERTAINTY_M:
        timing_state["live_delta_s"] = None
        timing_state["live_delta_available"] = False
        timing_state["live_delta_error"] = f"GPS uncertainty {pos_uncertainty:.1f} m > {MAX_LIVE_DELTA_POS_UNCERTAINTY_M:.1f} m"
        return

    reference_time = best_time_at_distance(current_distance)
    if reference_time is None:
        timing_state["live_delta_s"] = None
        timing_state["live_delta_available"] = False
        timing_state["live_delta_error"] = None
    else:
        timing_state["live_delta_s"] = elapsed_s - reference_time
        timing_state["live_delta_available"] = True
        timing_state["live_delta_error"] = None


def complete_timed_segment(cross_t: float, segment_type: str, warning: str = ""):
    start_s = timing_state["current_start_s"]
    if start_s is None:
        return
    duration = cross_t - start_s
    if duration <= 0:
        return

    best_before = timing_state["best_lap_s"]
    delta = None if best_before is None else duration - best_before
    trace = list(timing_state["current_trace"])
    distance_m = timing_state["current_distance_m"]
    warnings = [value for value in (timing_state.get("current_start_warning", ""), warning) if value]
    segment_warning = "; ".join(dict.fromkeys(warnings))
    if not segment_warning and (best_before is None or duration < best_before):
        timing_state["best_lap_s"] = duration
        timing_state["best_trace"] = trace

    timing_state["lap_count"] += 1
    timing_state["last_lap_s"] = duration
    timing_state["last_delta_s"] = delta
    timing_state["live_delta_s"] = None
    timing_state["live_delta_available"] = bool(timing_state["best_trace"])
    timing_state["live_delta_error"] = None
    timing_state["completed"].append({
        "number": timing_state["lap_count"],
        "type": segment_type,
        "duration_s": duration,
        "delta_to_best_s": delta,
        "distance_m": distance_m,
        "start_s": start_s,
        "end_s": cross_t,
        "warning": segment_warning,
    })
    timing_state["completed"] = timing_state["completed"][-20:]


def update_timing(sample_t: float, fields: dict):
    if not {"Latitude_deg", "Longitude_deg", "Speed_mph"}.issubset(fields):
        return

    cur_point = {
        "t": sample_t,
        "lat": float(fields["Latitude_deg"]),
        "lon": float(fields["Longitude_deg"]),
        "speed_mph": float(fields["Speed_mph"]),
        "pos_uncertainty_m": float(fields.get("PosUncertainty_m", 0.0)),
    }

    with timing_lock:
        if not timing_state["configured"]:
            timing_state["last_point"] = cur_point
            return

        prev_point = timing_state["last_point"]
        timing_state["last_point"] = cur_point
        if prev_point is None:
            return

        append_current_trace(cur_point)

        min_speed = float(track_config["min_speed_mph"])
        if max(prev_point["speed_mph"], cur_point["speed_mph"]) < min_speed:
            return

        mode = track_config["mode"]
        min_gap_s = float(track_config["min_gap_s"])
        start_cross_t = line_crossing_time(prev_point, cur_point, track_config["start_line"])
        start_warning = gps_crossing_warning(prev_point, cur_point)

        if start_cross_t is not None:
            last_start = timing_state["last_start_cross_s"]
            if last_start is None or start_cross_t - last_start >= min_gap_s:
                timing_state["last_start_cross_s"] = start_cross_t
                if mode == "lap":
                    if timing_state["active"]:
                        complete_timed_segment(start_cross_t, "lap", start_warning)
                    start_timed_segment(start_cross_t, cur_point, "lap running", start_warning)
                elif mode == "autocross" and not timing_state["active"]:
                    start_timed_segment(start_cross_t, cur_point, "run running", start_warning)

        if mode == "autocross" and timing_state["active"] and track_config["finish_line"]:
            finish_cross_t = line_crossing_time(prev_point, cur_point, track_config["finish_line"])
            finish_warning = gps_crossing_warning(prev_point, cur_point)
            if finish_cross_t is not None:
                last_finish = timing_state["last_finish_cross_s"]
                if last_finish is None or finish_cross_t - last_finish >= min_gap_s:
                    timing_state["last_finish_cross_s"] = finish_cross_t
                    complete_timed_segment(finish_cross_t, "run", finish_warning)
                    timing_state["active"] = False
                    timing_state["current_start_s"] = None
                    timing_state["current_elapsed_s"] = None
                    timing_state["current_distance_m"] = None
                    timing_state["current_start_warning"] = ""
                    timing_state["current_trace"] = []
                    timing_state["last_segment_point"] = None
                    timing_state["status"] = "waiting for start"


def public_timing_snapshot():
    with timing_lock:
        hidden = {"last_point", "last_segment_point", "current_trace", "best_trace"}
        snapshot = {k: v for k, v in timing_state.items() if k not in hidden}
        snapshot["config"] = dict(track_config)
        snapshot["current_trace"] = list(timing_state["current_trace"][-1200:])
        snapshot["best_trace"] = list(timing_state["best_trace"][-1200:])
        return snapshot


class CsvPacketWriter:
    def __init__(self, log_dir: Path, file_prefix: str):
        self.log_dir = log_dir
        self.file_prefix = file_prefix
        self.files = {}
        self.writers = {}

    def write(self, elapsed_s: float, parsed: dict):
        msg = parsed["message_type"]
        safe_msg = safe_filename_part(msg)
        values = parsed["values"]
        if msg not in self.writers:
            path = self.log_dir / f"{self.file_prefix}_{safe_msg}.csv"
            file_obj = path.open("w", newline="", buffering=1)
            writer = csv.writer(file_obj)
            writer.writerow([
                "Pi_Elapsed_Time_s",
                *field_names_for(msg, len(values)),
                "Checksum",
                "Checksum_OK",
                "Raw_Line",
            ])
            self.files[msg] = file_obj
            self.writers[msg] = writer
            logging.info("CSV output: %s", path)

        self.writers[msg].writerow([
            f"{elapsed_s:.6f}",
            *values,
            parsed["checksum"],
            parsed["checksum_ok"],
            parsed["raw"],
        ])

    def flush(self):
        for file_obj in self.files.values():
            file_obj.flush()
            os.fsync(file_obj.fileno())

    def close(self):
        for file_obj in self.files.values():
            try:
                file_obj.flush()
                os.fsync(file_obj.fileno())
            finally:
                file_obj.close()


class BinaryCsvWriter:
    def __init__(self, log_dir: Path, file_prefix: str):
        self.path = log_dir / f"{file_prefix}_BINARY.csv"
        self.file_obj = None
        self.writer = None
        self.headers = None

    def write(self, elapsed_s: float, fields: dict):
        if self.writer is None:
            self.headers = ["Pi_Logger_Elapsed_Time_s", *fields.keys()]
            self.file_obj = self.path.open("w", newline="", buffering=1)
            self.writer = csv.DictWriter(self.file_obj, fieldnames=self.headers, extrasaction="ignore")
            self.writer.writeheader()
            logging.info("Binary CSV output: %s", self.path)
        row = {"Pi_Logger_Elapsed_Time_s": f"{elapsed_s:.6f}"}
        row.update(fields)
        self.writer.writerow(row)

    def flush(self):
        if self.file_obj:
            self.file_obj.flush()
            os.fsync(self.file_obj.fileno())

    def close(self):
        if self.file_obj:
            try:
                self.file_obj.flush()
                os.fsync(self.file_obj.fileno())
            finally:
                self.file_obj.close()


def update_latest(
    status: str,
    logging_active: bool,
    session: Optional[str] = None,
    parsed: Optional[dict] = None,
    sample_time_s: Optional[float] = None,
    raw_bytes: Optional[int] = None,
    binary_bytes: Optional[int] = None,
    allow_timing: bool = True,
    timing_active: bool = True,
):
    fields_for_timing = None
    with state_lock:
        latest_packet["status"] = status
        latest_packet["logging"] = logging_active
        latest_packet["setup_streaming"] = setup_stream_requested.is_set()
        if session is not None:
            latest_packet["session"] = session
        if parsed is not None:
            fields = values_as_fields(parsed)
            if sample_time_s is not None:
                fields["Sample_Time_s"] = sample_time_s
            if allow_timing:
                latest_packet["message_type"] = parsed["message_type"]
                latest_packet["fields"] = fields
                latest_packet["checksum_ok"] = parsed["checksum_ok"]
                latest_packet["updated_monotonic"] = time.monotonic()
                latest_packet["parse_source"] = "ascii"
            latest_packet["ascii_packets"] += 1
            if allow_timing:
                fields_for_timing = fields
        if raw_bytes is not None:
            latest_packet["raw_bytes"] = raw_bytes
        if binary_bytes is not None:
            latest_packet["binary_bytes"] = binary_bytes
    if timing_active and allow_timing and fields_for_timing is not None and sample_time_s is not None:
        update_timing(sample_time_s, fields_for_timing)


def update_latest_fields(
    status: str,
    logging_active: bool,
    session: Optional[str],
    fields: dict,
    raw_bytes: Optional[int] = None,
    binary_bytes: Optional[int] = None,
    timing_active: bool = True,
):
    with state_lock:
        latest_packet["status"] = status
        latest_packet["logging"] = logging_active
        latest_packet["setup_streaming"] = setup_stream_requested.is_set()
        if session is not None:
            latest_packet["session"] = session
        latest_packet["message_type"] = "BINARY"
        latest_packet["fields"] = fields
        latest_packet["checksum_ok"] = ""
        latest_packet["updated_monotonic"] = time.monotonic()
        latest_packet["binary_packets"] += 1
        latest_packet["parse_source"] = "binary"
        if raw_bytes is not None:
            latest_packet["raw_bytes"] = raw_bytes
        if binary_bytes is not None:
            latest_packet["binary_bytes"] = binary_bytes
    sample_time = fields.get("Pi_Elapsed_Time_s")
    if timing_active and sample_time is not None:
        update_timing(sample_time, fields)


def run_setup_stream(port: str, baud: int, parse_mode: str = "auto"):
    """Publish live VN-300 fields without creating a run or writing files."""
    start_time = time.monotonic()
    byte_count = 0
    binary_byte_count = 0
    binary_active = False
    line_buffer = bytearray()
    binary_buffer = bytearray()

    logging.info("Opening %s at %d baud for non-recording track setup", port, baud)
    reset_latest_for_setup_stream()
    try:
        with serial.Serial(
            port=port,
            baudrate=baud,
            timeout=SERIAL_TIMEOUT_S,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
        ) as ser:
            while (
                not stop_requested.is_set()
                and setup_stream_requested.is_set()
                and not logging_requested.is_set()
            ):
                chunk = ser.read(READ_SIZE)
                if not chunk:
                    continue
                now = time.monotonic()
                byte_count += len(chunk)
                binary_byte_count += sum(
                    1 for byte in chunk if byte not in b"\r\n\t" and (byte < 32 or byte > 126)
                )
                if parse_mode in ("auto", "binary"):
                    binary_buffer.extend(chunk)

                while parse_mode in ("auto", "binary"):
                    packet_start = binary_buffer.find(VN300_BINARY_HEADER)
                    if packet_start < 0:
                        if len(binary_buffer) > len(VN300_BINARY_HEADER):
                            del binary_buffer[:-len(VN300_BINARY_HEADER)]
                        break
                    if packet_start > 0:
                        del binary_buffer[:packet_start]
                    if len(binary_buffer) < VN300_BINARY_PACKET_LEN:
                        break
                    packet = bytes(binary_buffer[:VN300_BINARY_PACKET_LEN])
                    del binary_buffer[:VN300_BINARY_PACKET_LEN]
                    parse_failed = False
                    try:
                        fields = parse_vn300_binary_packet(packet)
                    except (struct.error, IndexError, KeyError) as exc:
                        fields = None
                        parse_failed = True
                        increment_bad_binary_packets()
                        logging.warning("Setup-stream binary packet parse failed: %s", exc)
                    if fields:
                        binary_active = True
                        update_latest_fields(
                            "track setup",
                            False,
                            None,
                            fields,
                            raw_bytes=byte_count,
                            binary_bytes=binary_byte_count,
                            timing_active=False,
                        )
                    elif not parse_failed:
                        increment_bad_binary_packets()

                parse_ascii_this_chunk = parse_mode == "ascii" or (
                    parse_mode == "auto" and not binary_active
                )
                if parse_ascii_this_chunk and (b"$VN" in chunk or line_buffer):
                    if b"$VN" in chunk and not line_buffer:
                        line_buffer.extend(chunk[chunk.find(b"$VN"):])
                    else:
                        line_buffer.extend(chunk)

                    if len(line_buffer) > MAX_ASCII_BUFFER_BYTES:
                        last_start = line_buffer.rfind(b"$VN")
                        if last_start >= 0:
                            line_buffer = bytearray(line_buffer[last_start:])
                        else:
                            line_buffer.clear()

                    while b"\n" in line_buffer:
                        raw_bytes, _, remainder = line_buffer.partition(b"\n")
                        line_buffer = bytearray(remainder)
                        parsed = parse_ascii_line(raw_bytes.decode("ascii", errors="ignore"))
                        if parsed:
                            update_latest(
                                "track setup",
                                False,
                                None,
                                parsed,
                                now - start_time,
                                raw_bytes=byte_count,
                                binary_bytes=binary_byte_count,
                                timing_active=False,
                            )
                else:
                    if binary_active and line_buffer:
                        line_buffer.clear()
                    update_latest(
                        "track setup",
                        False,
                        raw_bytes=byte_count,
                        binary_bytes=binary_byte_count,
                        timing_active=False,
                    )
    finally:
        if not setup_stream_requested.is_set():
            next_status = "starting logging" if logging_requested.is_set() else "idle"
            update_latest(next_status, False)
        logging.info("Non-recording track setup stream closed. Bytes received: %d", byte_count)


def run_session(
    port: str,
    baud: int,
    log_dir: Path,
    base_log_dir: Path,
    parse_mode: str = "auto",
    can_config: Optional[dict] = None,
):
    can_config = can_config or {"enabled": False}
    destination_type = log_destination_type(base_log_dir)
    metadata_snapshot = current_run_metadata()
    identity = build_run_identity(base_log_dir, metadata_snapshot)
    run_id = identity["run_id"]
    file_prefix = identity["file_prefix"]
    raw_path = log_dir / f"{file_prefix}_{Path(port).name}.bin"
    metadata_path = log_dir / f"{file_prefix}_session_metadata.json"
    session_name = run_id
    start_time = time.monotonic()
    last_flush = start_time
    byte_count = 0
    binary_byte_count = 0
    binary_active = False
    first_sensor_utc = None
    last_sensor_utc = None
    stop_reason = "log stopped"
    error_text = None
    line_buffer = bytearray()
    binary_buffer = bytearray()
    csv_outputs = CsvPacketWriter(log_dir, file_prefix)
    binary_outputs = BinaryCsvWriter(log_dir, file_prefix)
    can_stop_event = threading.Event()
    can_thread = None
    metadata = {
        "session": session_name,
        "run_id": run_id,
        "run_number": identity["run_number"],
        "date": identity["date"],
        "date_source": identity["date_source"],
        "file_prefix": file_prefix,
        "start_time_local": dt.datetime.now().isoformat(timespec="seconds"),
        "start_time_pi_clock": dt.datetime.now().isoformat(timespec="seconds"),
        "port": port,
        "baud": baud,
        "parse_mode": parse_mode,
        "log_dir": str(log_dir),
        "base_log_dir": str(base_log_dir),
        "log_destination": destination_type,
        "raw_path": str(raw_path),
        "metadata_path": str(metadata_path),
        "run_metadata": metadata_snapshot,
        "clean_stop": False,
        "stop_reason": None,
        "raw_bytes": 0,
        "binary_bytes_estimate": 0,
        "ascii_packets": 0,
        "binary_packets": 0,
        "bad_binary_packets": 0,
        "can_enabled": bool(can_config.get("enabled")),
        "can_interface": can_config.get("interface"),
        "can_channel": can_config.get("channel"),
        "can_bitrate": can_config.get("bitrate"),
        "can_signal_map": str(can_config.get("signal_map") or ""),
    }

    logging.info("Opening %s at %d baud", port, baud)
    logging.info("Run ID: %s", run_id)
    logging.info("Log destination: %s (%s)", log_dir, destination_type)
    logging.info("Raw output: %s", raw_path)
    reset_latest_for_session(session_name)
    with timing_lock:
        reset_timing_state("waiting for start" if timing_state["configured"] else "not configured")
    start_free = free_space_bytes(log_dir)
    metadata["free_space_start_bytes"] = start_free
    set_log_health(
        "ready",
        log_dir=log_dir,
        base_log_dir=base_log_dir,
        destination=destination_type,
        free_space=start_free,
        write_error="",
    )
    if start_free < MIN_FREE_SPACE_BYTES:
        stop_reason = "low disk space before session start"
        metadata["stop_reason"] = stop_reason
        metadata["free_space_end_bytes"] = start_free
        metadata["end_time_local"] = dt.datetime.now().isoformat(timespec="seconds")
        write_session_metadata(metadata_path, metadata)
        set_latest_warning(f"Low disk space: {start_free // (1024 * 1024)} MB free")
        set_log_health("low disk space", free_space=start_free)
        update_latest("low disk space", False, session_name)
        logging.error("%s", stop_reason)
        return

    try:
        write_session_metadata(metadata_path, metadata)
        write_log_status_file(log_dir, {
            "status": "session started",
            "session": session_name,
            "log_destination": destination_type,
            "log_dir": str(log_dir),
            "base_log_dir": str(base_log_dir),
            "raw_path": str(raw_path),
            "start_time_pi_clock": metadata["start_time_pi_clock"],
            "free_space_start_bytes": start_free,
        })
        set_log_health("writing", last_flush_s=0.0)
        if can_config.get("enabled"):
            can_thread = threading.Thread(
                target=run_can_logger,
                args=(can_config, log_dir, file_prefix, start_time, can_stop_event),
                daemon=True,
            )
            can_thread.start()
        with serial.Serial(
            port=port,
            baudrate=baud,
            timeout=SERIAL_TIMEOUT_S,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
        ) as ser, raw_path.open("ab", buffering=0) as raw_file:
            while not stop_requested.is_set() and not active_session_stop.is_set():
                chunk = ser.read(READ_SIZE)
                now = time.monotonic()

                if chunk:
                    try:
                        raw_file.write(chunk)
                    except OSError as exc:
                        error_text = repr(exc)
                        stop_reason = "log write error"
                        set_latest_warning(f"Log write error: {exc}")
                        set_log_health("write error", write_error=error_text)
                        logging.error("Raw log write failed: %s", exc)
                        active_session_stop.set()
                        break
                    byte_count += len(chunk)
                    binary_byte_count += sum(1 for byte in chunk if byte not in b"\r\n\t" and (byte < 32 or byte > 126))
                    if parse_mode in ("auto", "binary"):
                        binary_buffer.extend(chunk)

                    while parse_mode in ("auto", "binary"):
                        packet_start = binary_buffer.find(VN300_BINARY_HEADER)
                        if packet_start < 0:
                            if len(binary_buffer) > len(VN300_BINARY_HEADER):
                                del binary_buffer[:-len(VN300_BINARY_HEADER)]
                            break
                        if packet_start > 0:
                            del binary_buffer[:packet_start]
                        if len(binary_buffer) < VN300_BINARY_PACKET_LEN:
                            break
                        packet = bytes(binary_buffer[:VN300_BINARY_PACKET_LEN])
                        del binary_buffer[:VN300_BINARY_PACKET_LEN]
                        parse_failed = False
                        try:
                            fields = parse_vn300_binary_packet(packet)
                        except (struct.error, IndexError, KeyError) as exc:
                            fields = None
                            parse_failed = True
                            increment_bad_binary_packets()
                            logging.warning("Binary packet parse failed: %s", exc)
                        if fields:
                            binary_active = True
                            sensor_utc = sensor_utc_datetime_from_fields(fields)
                            if sensor_utc:
                                if first_sensor_utc is None:
                                    first_sensor_utc = sensor_utc
                                last_sensor_utc = sensor_utc
                            elapsed_s = now - start_time
                            try:
                                binary_outputs.write(elapsed_s, fields)
                            except OSError as exc:
                                error_text = repr(exc)
                                stop_reason = "log write error"
                                set_latest_warning(f"Binary CSV write error: {exc}")
                                set_log_health("write error", write_error=error_text)
                                logging.error("Binary CSV write failed: %s", exc)
                                active_session_stop.set()
                                break
                            update_latest_fields(
                                "logging",
                                True,
                                session_name,
                                fields,
                                raw_bytes=byte_count,
                                binary_bytes=binary_byte_count,
                            )
                        elif not parse_failed:
                            increment_bad_binary_packets()

                    if active_session_stop.is_set():
                        continue

                    parse_ascii_this_chunk = parse_mode == "ascii" or (parse_mode == "auto" and not binary_active)
                    if parse_ascii_this_chunk and (b"$VN" in chunk or line_buffer):
                        if b"$VN" in chunk and not line_buffer:
                            line_buffer.extend(chunk[chunk.find(b"$VN"):])
                        else:
                            line_buffer.extend(chunk)

                        if len(line_buffer) > MAX_ASCII_BUFFER_BYTES:
                            last_start = line_buffer.rfind(b"$VN")
                            if last_start >= 0:
                                line_buffer = bytearray(line_buffer[last_start:])
                            else:
                                line_buffer.clear()

                        while b"\n" in line_buffer:
                            raw_bytes, _, remainder = line_buffer.partition(b"\n")
                            line_buffer = bytearray(remainder)
                            parsed = parse_ascii_line(raw_bytes.decode("ascii", errors="ignore"))
                            if parsed:
                                elapsed_s = now - start_time
                                try:
                                    csv_outputs.write(elapsed_s, parsed)
                                except OSError as exc:
                                    error_text = repr(exc)
                                    stop_reason = "log write error"
                                    set_latest_warning(f"ASCII CSV write error: {exc}")
                                    set_log_health("write error", write_error=error_text)
                                    logging.error("ASCII CSV write failed: %s", exc)
                                    active_session_stop.set()
                                    break
                                allow_ascii_timing = parse_mode == "ascii" or not binary_active
                                update_latest(
                                    "logging",
                                    True,
                                    session_name,
                                    parsed,
                                    elapsed_s,
                                    raw_bytes=byte_count,
                                    binary_bytes=binary_byte_count,
                                    allow_timing=allow_ascii_timing,
                                )
                    else:
                        if binary_active and line_buffer:
                            line_buffer.clear()
                        update_latest(
                            "binary logging" if binary_active else "raw logging",
                            True,
                            session_name,
                            raw_bytes=byte_count,
                            binary_bytes=binary_byte_count,
                        )

                if now - last_flush >= FLUSH_INTERVAL_S:
                    free_now = free_space_bytes(log_dir)
                    if free_now < MIN_FREE_SPACE_BYTES:
                        stop_reason = "low disk space during session"
                        set_latest_warning(f"Low disk space: {free_now // (1024 * 1024)} MB free")
                        set_log_health("low disk space", free_space=free_now)
                        active_session_stop.set()
                    update_latest(
                        "binary logging" if binary_active else "logging",
                        True,
                        session_name,
                        raw_bytes=byte_count,
                        binary_bytes=binary_byte_count,
                    )
                    try:
                        raw_file.flush()
                        os.fsync(raw_file.fileno())
                        csv_outputs.flush()
                        binary_outputs.flush()
                        write_log_status_file(log_dir, {
                            "status": "logging",
                            "session": session_name,
                            "log_destination": destination_type,
                            "log_dir": str(log_dir),
                            "base_log_dir": str(base_log_dir),
                            "raw_path": str(raw_path),
                            "raw_bytes": byte_count,
                            "binary_bytes_estimate": binary_byte_count,
                            "free_space_bytes": free_now,
                            "last_flush_pi_clock": dt.datetime.now().isoformat(timespec="seconds"),
                        })
                        set_log_health("writing", free_space=free_now, write_error="", last_flush_s=now - start_time)
                    except OSError as exc:
                        error_text = repr(exc)
                        stop_reason = "log write error"
                        set_latest_warning(f"Log flush error: {exc}")
                        set_log_health("write error", write_error=error_text)
                        logging.error("Log flush failed: %s", exc)
                        active_session_stop.set()
                    last_flush = now

            try:
                raw_file.flush()
                os.fsync(raw_file.fileno())
            except OSError as exc:
                error_text = repr(exc)
                stop_reason = "log write error"
                set_latest_warning(f"Final raw flush error: {exc}")
                set_log_health("write error", write_error=error_text)
                logging.error("Final raw flush failed: %s", exc)
    except Exception as exc:
        error_text = repr(exc)
        stop_reason = "error"
        raise
    finally:
        can_stop_event.set()
        if can_thread:
            can_thread.join(timeout=2.0)
        try:
            csv_outputs.close()
        except OSError as exc:
            logging.error("ASCII CSV close failed: %s", exc)
            if error_text is None:
                error_text = repr(exc)
                stop_reason = "log write error"
                set_log_health("write error", write_error=error_text)
        try:
            binary_outputs.close()
        except OSError as exc:
            logging.error("Binary CSV close failed: %s", exc)
            if error_text is None:
                error_text = repr(exc)
                stop_reason = "log write error"
                set_log_health("write error", write_error=error_text)
        with state_lock:
            metadata["ascii_packets"] = latest_packet["ascii_packets"]
            metadata["binary_packets"] = latest_packet["binary_packets"]
            metadata["bad_binary_packets"] = latest_packet["bad_binary_packets"]
            can_state = dict(latest_packet.get("can") or {})
        can_state.pop("_last_monotonic", None)
        metadata["can"] = can_state
        metadata["raw_bytes"] = byte_count
        metadata["binary_bytes_estimate"] = binary_byte_count
        try:
            metadata["free_space_end_bytes"] = free_space_bytes(log_dir)
        except OSError as exc:
            metadata["free_space_end_error"] = repr(exc)
            metadata["free_space_end_bytes"] = None
            if error_text is None:
                error_text = repr(exc)
                stop_reason = "log write error"
                set_log_health("write error", write_error=error_text)
        metadata["end_time_local"] = dt.datetime.now().isoformat(timespec="seconds")
        metadata["end_time_pi_clock"] = dt.datetime.now().isoformat(timespec="seconds")
        if first_sensor_utc:
            metadata["sensor_start_time_utc"] = first_sensor_utc.isoformat()
            metadata["sensor_start_date"] = first_sensor_utc.strftime("%Y-%m-%d")
            metadata["sensor_date_source"] = "vn300_utc"
        if last_sensor_utc:
            metadata["sensor_end_time_utc"] = last_sensor_utc.isoformat()
        if shutdown_requested.is_set():
            stop_reason = "shutdown requested"
        metadata["stop_reason"] = stop_reason
        metadata["error"] = error_text
        metadata["clean_stop"] = error_text is None and not stop_reason.startswith("low disk")

        if first_sensor_utc:
            sensor_date = first_sensor_utc.strftime("%Y-%m-%d")
            if sensor_date == identity["date"]:
                identity["date_source"] = "vn300_utc"
            else:
                final_identity = build_run_identity(base_log_dir, metadata_snapshot, sensor_date, "vn300_utc")
                rename_results = rename_run_files(log_dir, file_prefix, final_identity["file_prefix"])
                metadata["post_session_rename"] = rename_results
                rename_failed = any(not result.get("renamed") for result in rename_results)
                if rename_failed:
                    logging.error("Could not rename all run files from %s to %s", file_prefix, final_identity["file_prefix"])
                else:
                    logging.info("Renamed run files from %s to %s using VN-300 UTC date", file_prefix, final_identity["file_prefix"])
                    identity = final_identity
                    run_id = identity["run_id"]
                    file_prefix = identity["file_prefix"]
                    session_name = run_id
                    raw_path = log_dir / f"{file_prefix}_{Path(port).name}.bin"
                    metadata_path = log_dir / f"{file_prefix}_session_metadata.json"

        metadata["session"] = session_name
        metadata["run_id"] = run_id
        metadata["run_number"] = identity["run_number"]
        metadata["date"] = identity["date"]
        metadata["date_source"] = identity["date_source"]
        metadata["file_prefix"] = file_prefix
        metadata["raw_path"] = str(raw_path)
        metadata["metadata_path"] = str(metadata_path)
        try:
            write_session_metadata(metadata_path, metadata)
        except OSError as exc:
            logging.error("Could not write session metadata: %s", exc)
        try:
            write_log_status_file(log_dir, {
                "status": metadata["stop_reason"],
                "session": session_name,
                "run_id": run_id,
                "log_destination": destination_type,
                "log_dir": str(log_dir),
                "base_log_dir": str(base_log_dir),
                "raw_path": str(raw_path),
                "raw_bytes": byte_count,
                "binary_bytes_estimate": binary_byte_count,
                "ascii_packets": metadata["ascii_packets"],
                "binary_packets": metadata["binary_packets"],
                "bad_binary_packets": metadata["bad_binary_packets"],
                "clean_stop": metadata["clean_stop"],
                "error": metadata["error"],
                "end_time_pi_clock": metadata["end_time_pi_clock"],
                "free_space_end_bytes": metadata.get("free_space_end_bytes"),
            })
        except OSError as exc:
            logging.error("Could not write logger status file: %s", exc)
        try:
            run_metadata_row = dict(metadata_snapshot)
            run_metadata_row.update({
                "session_file": run_id,
                "run_id": run_id,
                "run_number": identity["run_number"],
                "date": identity["date"],
            })
            write_run_metadata_csv(log_dir, run_metadata_row)
        except OSError as exc:
            logging.error("Could not write run metadata CSV: %s", exc)

    logging.info("Session closed. Raw bytes: %d", byte_count)
    update_latest("idle", False, session_name)


def install_gpio_callbacks(log_pin: int, power_pin: int, shutdown_command: str):
    try:
        from gpiozero import Button
    except ImportError:
        logging.warning("gpiozero is not installed; buttons are disabled")
        return None

    last_log_press = {"t": 0.0}

    def toggle_logging():
        now = time.monotonic()
        if now - last_log_press["t"] < BUTTON_DEBOUNCE_S:
            return
        last_log_press["t"] = now
        if logging_requested.is_set():
            logging.info("LOG button: stop requested")
            logging_requested.clear()
            active_session_stop.set()
        else:
            logging.info("LOG button: start requested")
            setup_stream_requested.clear()
            active_session_stop.clear()
            logging_requested.set()

    def shutdown_pi():
        logging.info("POWER button held: shutdown requested")
        shutdown_requested.set()
        logging_requested.clear()
        setup_stream_requested.clear()
        active_session_stop.set()
        stop_requested.set()

    log_button = Button(log_pin, pull_up=True, bounce_time=BUTTON_DEBOUNCE_S)
    power_button = Button(power_pin, pull_up=True, hold_time=POWER_HOLD_S)
    log_button.when_pressed = toggle_logging
    power_button.when_held = shutdown_pi
    logging.info("Buttons enabled: log GPIO %d, power GPIO %d", log_pin, power_pin)
    return log_button, power_button


DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VN300 Live</title>
<style>
body{font-family:Arial,sans-serif;margin:0;background:#101214;color:#f2f4f5}
header{display:flex;gap:16px;align-items:center;padding:14px 18px;background:#1b1f23;border-bottom:1px solid #333;flex-wrap:wrap}
h1{font-size:20px;margin:0}.pill{padding:5px 10px;border-radius:4px;background:#444}.on{background:#116b43}.off{background:#6b2b11}
main{padding:18px;display:grid;gap:14px;grid-template-columns:repeat(12,1fr)}section{grid-column:1/-1}
.grid{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(160px,1fr))}
.tile,.panel{border:1px solid #333;border-radius:6px;padding:14px;background:#181c20}.label{color:#9ba3aa;font-size:12px}.value{font-size:26px;margin-top:5px}.value.small{font-size:16px;line-height:1.25}
.panel h2{font-size:16px;margin:0 0 12px}.form{display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));align-items:end}
label{display:grid;gap:5px;color:#9ba3aa;font-size:12px}input,select,button,textarea{font:inherit;border-radius:4px;border:1px solid #3a4148;background:#101418;color:#f2f4f5;padding:8px}
textarea{min-height:70px;resize:vertical}.span2{grid-column:span 2}.next-run{color:#9ba3aa;font-size:14px}
button{background:#2563eb;border-color:#2563eb;cursor:pointer}.secondary{background:#30363d;border-color:#454c54}
.save-button{display:inline-flex;align-items:center;justify-content:center;gap:8px}.save-button svg{width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
.save-status{align-self:center;color:#9ba3aa;font-size:13px;min-height:20px}.save-status.saving{color:#d29922}.save-status.saved{color:#56d364}.save-status.error{color:#ff7b72}.save-status.dirty{color:#d29922}
canvas{border:1px solid #333;border-radius:6px;background:#15191d;width:100%;height:260px}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{border-bottom:1px solid #30363d;padding:8px;text-align:left}th{color:#9ba3aa;font-weight:400}
.finish{display:none}.autocross .finish{display:grid}.wide{grid-column:1/-1}.ok{color:#56d364}.bad{color:#ff7b72}
</style>
</head>
<body>
<header><h1>VN300 Live</h1><span id="status" class="pill off">idle</span><span id="session"></span><span id="timingStatus"></span></header>
<main>
<section class="grid">
<div class="tile"><div class="label">Speed</div><div id="speed" class="value">-- mph</div></div>
<div class="tile"><div class="label">Longitudinal</div><div id="longG" class="value">-- g</div></div>
<div class="tile"><div class="label">Lateral</div><div id="latG" class="value">-- g</div></div>
<div class="tile"><div class="label">Lap/Run</div><div id="lapCount" class="value">0</div></div>
<div class="tile"><div class="label">Current</div><div id="currentTime" class="value">--</div></div>
<div class="tile"><div class="label">Best</div><div id="bestTime" class="value">--</div></div>
<div class="tile"><div class="label">Live Delta</div><div id="deltaTime" class="value">--</div></div>
<div class="tile"><div class="label">GPS</div><div id="gps" class="value">--</div></div>
<div class="tile"><div class="label">Stream</div><div id="stream" class="value small">--</div></div>
<div class="tile"><div class="label">Log</div><div id="logHealth" class="value small">--</div></div>
<div class="tile"><div class="label">Pi CPU</div><div id="cpuTemp" class="value small">--</div></div>
<div class="tile"><div class="label">CAN</div><div id="canStatus" class="value small">disabled</div></div>
</section>
<section class="panel">
<h2>Run Metadata</h2>
<div class="form">
<label>Next Run<div id="nextRun" class="next-run">--</div></label>
<label>Driver<input id="driver" autocomplete="off"></label>
<label>Test Type<input id="test_type" autocomplete="off" placeholder="autocross, skidpad, brake"></label>
<label>Course<input id="course" autocomplete="off"></label>
<label>Location<input id="test_location" autocomplete="off"></label>
<label>Car Setup<input id="car_config" autocomplete="off"></label>
<label>Tire<input id="tire_compound" autocomplete="off"></label>
<label>Cold FL psi<input id="cold_fl_psi" type="number" step="0.1"></label>
<label>Cold FR psi<input id="cold_fr_psi" type="number" step="0.1"></label>
<label>Cold RL psi<input id="cold_rl_psi" type="number" step="0.1"></label>
<label>Cold RR psi<input id="cold_rr_psi" type="number" step="0.1"></label>
<label>Brake Bias<input id="brake_bias" autocomplete="off"></label>
<label>Aero<input id="aero_config" autocomplete="off"></label>
<label>Valid<select id="valid_run"><option value="yes">Yes</option><option value="no">No</option><option value="review">Review</option></select></label>
<label class="span2">Notes<textarea id="notes"></textarea></label>
<button id="saveRunMetadata" class="save-button" title="Save run metadata for the next log">
<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 3h12l2 2v16H5z"></path><path d="M8 3v6h8V3"></path><path d="M8 21v-7h8v7"></path></svg>
<span>Save Run Info</span>
</button>
<div id="runMetadataSaveStatus" class="save-status" aria-live="polite"></div>
</div>
</section>
<section class="panel" id="setupPanel">
<h2>Timing Setup</h2>
<div class="form">
<label>Track<select id="mode"><option value="lap">Lap</option><option value="autocross">Autocross</option></select></label>
<label>Start Lat 1<input id="start_lat1" type="number" step="0.00000001"></label>
<label>Start Lon 1<input id="start_lon1" type="number" step="0.00000001"></label>
<label>Start Lat 2<input id="start_lat2" type="number" step="0.00000001"></label>
<label>Start Lon 2<input id="start_lon2" type="number" step="0.00000001"></label>
<label class="finish">Finish Lat 1<input id="finish_lat1" type="number" step="0.00000001"></label>
<label class="finish">Finish Lon 1<input id="finish_lon1" type="number" step="0.00000001"></label>
<label class="finish">Finish Lat 2<input id="finish_lat2" type="number" step="0.00000001"></label>
<label class="finish">Finish Lon 2<input id="finish_lon2" type="number" step="0.00000001"></label>
<label>Min Speed mph<input id="min_speed_mph" type="number" step="0.5" value="5"></label>
<label>Min Gap s<input id="min_gap_s" type="number" step="0.5" value="8"></label>
<button id="saveConfig" class="save-button" title="Save timing setup">
<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 3h12l2 2v16H5z"></path><path d="M8 3v6h8V3"></path><path d="M8 21v-7h8v7"></path></svg>
<span>Save Timing</span>
</button>
<button id="setupStream" title="Show live data without recording a run or creating log files">Start Setup Stream</button>
<button id="resetTiming" class="secondary">Reset</button>
<div id="timingConfigSaveStatus" class="save-status" aria-live="polite"></div>
</div>
</section>
<section class="panel">
<h2>Live Data</h2>
<div class="grid">
<div class="tile"><div class="label">Yaw</div><div id="yaw" class="value">-- deg</div></div>
<div class="tile"><div class="label">Latitude</div><div id="lat" class="value">--</div></div>
<div class="tile"><div class="label">Longitude</div><div id="lon" class="value">--</div></div>
<div class="tile"><div class="label">Position Uncertainty</div><div id="pos" class="value">-- m</div></div>
<div class="tile"><div class="label">Checksum</div><div id="checksum" class="value">--</div></div>
</div>
</section>
<section class="panel"><h2>Track</h2><canvas id="trackPlot" width="900" height="360"></canvas></section>
<section><canvas id="plot" width="900" height="260"></canvas></section>
<section class="panel"><h2>Completed</h2><table><thead><tr><th>#</th><th>Type</th><th>Time</th><th>Delta</th><th>Warning</th></tr></thead><tbody id="laps"></tbody></table></section>
</main>
<script>
const history=[]; const maxN=240; const c=document.getElementById('plot'); const x=c.getContext('2d');
const trackCanvas=document.getElementById('trackPlot'); const trackCtx=trackCanvas.getContext('2d');
const ids=['start_lat1','start_lon1','start_lat2','start_lon2','finish_lat1','finish_lon1','finish_lat2','finish_lon2','min_speed_mph','min_gap_s'];
const metadataIds=['driver','test_type','course','test_location','car_config','tire_compound','cold_fl_psi','cold_fr_psi','cold_rl_psi','cold_rr_psi','brake_bias','aero_config','valid_run','notes'];
let setupStreamActive=false;
function fmt(v,n=2){return Number.isFinite(v)?v.toFixed(n):'--'}
function timeFmt(v){if(!Number.isFinite(v))return'--'; const m=Math.floor(v/60),s=v-m*60; return m?`${m}:${s.toFixed(3).padStart(6,'0')}`:s.toFixed(3)}
function deltaFmt(v){if(v===null||v===undefined||!Number.isFinite(v))return'--'; return (v>=0?'+':'')+v.toFixed(3)}
function setModeClass(){document.getElementById('setupPanel').className='panel '+document.getElementById('mode').value}
document.getElementById('mode').addEventListener('change',setModeClass); setModeClass();
function draw(){x.clearRect(0,0,c.width,c.height);x.strokeStyle='#2a3036';for(let i=0;i<6;i++){let y=i*c.height/5;x.beginPath();x.moveTo(0,y);x.lineTo(c.width,y);x.stroke()}
 if(history.length<2)return; const max=Math.max(10,...history); x.strokeStyle='#58a6ff'; x.lineWidth=2; x.beginPath();
 history.forEach((v,i)=>{let px=i*(c.width/(maxN-1));let py=c.height-(v/max)*(c.height-16)-8;if(i===0)x.moveTo(px,py);else x.lineTo(px,py)}); x.stroke()}
function projectTrace(points,origin){if(!points.length||!origin)return[]; const lat0=origin.lat, lon0=origin.lon, r=6371000, c=Math.cos(lat0*Math.PI/180); return points.map(p=>({x:(p.lon-lon0)*Math.PI/180*r*c,y:(p.lat-lat0)*Math.PI/180*r,t:p.elapsed_s,lat:p.lat,lon:p.lon}))}
function pointAtElapsed(points, elapsed){if(!points.length||!Number.isFinite(elapsed))return null; if(elapsed<=points[0].t)return points[0]; for(let i=1;i<points.length;i++){if(points[i].t>=elapsed){const a=points[i-1],b=points[i],span=b.t-a.t||1,frac=(elapsed-a.t)/span; return {x:a.x+frac*(b.x-a.x),y:a.y+frac*(b.y-a.y),t:elapsed}}} return points[points.length-1]}
function drawTrack(t){trackCtx.clearRect(0,0,trackCanvas.width,trackCanvas.height); const currentRaw=(t.current_trace||[]).filter(p=>Number.isFinite(p.lat)&&Number.isFinite(p.lon)); const bestRaw=(t.best_trace||[]).filter(p=>Number.isFinite(p.lat)&&Number.isFinite(p.lon)); const origin=(currentRaw[0]||bestRaw[0]); const current=projectTrace(currentRaw,origin); const best=projectTrace(bestRaw,origin); const all=current.concat(best); trackCtx.fillStyle='#9ba3aa'; if(!all.length){trackCtx.fillText('No active timing trace',20,28);return}
 let minx=Math.min(...all.map(p=>p.x)),maxx=Math.max(...all.map(p=>p.x)),miny=Math.min(...all.map(p=>p.y)),maxy=Math.max(...all.map(p=>p.y)); if(minx===maxx){minx-=1;maxx+=1} if(miny===maxy){miny-=1;maxy+=1} const pad=24, sx=(trackCanvas.width-pad*2)/(maxx-minx), sy=(trackCanvas.height-pad*2)/(maxy-miny), scale=Math.min(sx,sy);
 function px(p){return trackCanvas.width/2+(p.x-(minx+maxx)/2)*scale} function py(p){return trackCanvas.height/2-(p.y-(miny+maxy)/2)*scale}
 function stroke(points,color,width){if(points.length<2)return; trackCtx.strokeStyle=color; trackCtx.lineWidth=width; trackCtx.beginPath(); points.forEach((p,i)=>{if(i===0)trackCtx.moveTo(px(p),py(p));else trackCtx.lineTo(px(p),py(p))}); trackCtx.stroke()}
 stroke(best,'#56d364',3); stroke(current,'#58a6ff',3);
 const currentDot=current.length?current[current.length-1]:null; const bestDot=pointAtElapsed(best,t.current_elapsed_s);
 if(bestDot){trackCtx.fillStyle='#56d364'; trackCtx.beginPath(); trackCtx.arc(px(bestDot),py(bestDot),6,0,Math.PI*2); trackCtx.fill()}
 if(currentDot){trackCtx.fillStyle='#58a6ff'; trackCtx.beginPath(); trackCtx.arc(px(currentDot),py(currentDot),6,0,Math.PI*2); trackCtx.fill()}
 trackCtx.fillStyle='#d8dee4'; trackCtx.fillText('current',20,24); trackCtx.fillStyle='#58a6ff'; trackCtx.fillRect(78,16,18,4); trackCtx.fillStyle='#d8dee4'; trackCtx.fillText('best / same elapsed',112,24); trackCtx.fillStyle='#56d364'; trackCtx.fillRect(228,16,18,4)}
function configPayload(){const p={mode:document.getElementById('mode').value}; ids.forEach(id=>p[id]=Number(document.getElementById(id).value)); return p}
function metadataPayload(){const p={}; metadataIds.forEach(id=>p[id]=document.getElementById(id).value); return p}
function setTimingSaveStatus(text,state=''){const el=document.getElementById('timingConfigSaveStatus'); el.textContent=text; el.className='save-status '+state}
function setMetadataSaveStatus(text,state=''){const el=document.getElementById('runMetadataSaveStatus'); el.textContent=text; el.className='save-status '+state}
['mode'].concat(ids).forEach(id=>{const el=document.getElementById(id); el.addEventListener('input',()=>setTimingSaveStatus('Unsaved timing changes','dirty')); el.addEventListener('change',()=>setTimingSaveStatus('Unsaved timing changes','dirty'))});
metadataIds.forEach(id=>document.getElementById(id).addEventListener('input',()=>setMetadataSaveStatus('Unsaved metadata changes','dirty')));
document.getElementById('saveConfig').onclick=async()=>{
 const btn=document.getElementById('saveConfig'); btn.disabled=true; setTimingSaveStatus('Saving timing setup...','saving');
 try{const r=await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(configPayload())}); const d=await r.json(); if(!r.ok){setTimingSaveStatus(d.error||'Timing setup save failed','error'); alert(d.error||'Config failed')} else {fillConfig((d.timing&&d.timing.config)||d.config); setTimingSaveStatus('Timing setup saved','saved')}}
 catch(e){setTimingSaveStatus('Timing setup save failed','error'); alert('Config failed')}
 finally{btn.disabled=false}
};
document.getElementById('resetTiming').onclick=async()=>{await fetch('/api/reset_timing',{method:'POST'}); setTimingSaveStatus('Timing laps reset','saved')};
document.getElementById('setupStream').onclick=async()=>{
 const btn=document.getElementById('setupStream'), enable=!setupStreamActive; btn.disabled=true; btn.textContent=enable?'Starting...':'Stopping...';
 try{const r=await fetch('/api/setup_stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:enable})}); const d=await r.json(); if(!r.ok)alert(d.error||'Setup stream failed'); else setupStreamActive=!!d.setup_streaming}
 catch(e){alert('Setup stream failed')}
 finally{btn.textContent=setupStreamActive?'Stop Setup Stream':'Start Setup Stream'; btn.disabled=false}
};
document.getElementById('saveRunMetadata').onclick=async()=>{
 const btn=document.getElementById('saveRunMetadata'); btn.disabled=true; setMetadataSaveStatus('Saving metadata...','saving');
 try{const r=await fetch('/api/run_metadata',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(metadataPayload())}); const d=await r.json(); if(!r.ok){setMetadataSaveStatus(d.error||'Metadata save failed','error'); alert(d.error||'Metadata failed')} else {fillRunMetadata(d); setMetadataSaveStatus(`Saved for ${d.next_run_id||'next run'}`,'saved')}}
 catch(e){setMetadataSaveStatus('Metadata save failed','error'); alert('Metadata failed')}
 finally{btn.disabled=false}
};
function fillConfig(cfg){if(!cfg||!cfg.mode)return; document.getElementById('mode').value=cfg.mode; setModeClass(); const s=cfg.start_line||{},f=cfg.finish_line||{};
 [['start_lat1',s.lat1],['start_lon1',s.lon1],['start_lat2',s.lat2],['start_lon2',s.lon2],['finish_lat1',f.lat1],['finish_lon1',f.lon1],['finish_lat2',f.lat2],['finish_lon2',f.lon2],['min_speed_mph',cfg.min_speed_mph],['min_gap_s',cfg.min_gap_s]].forEach(([id,v])=>{if(v!==undefined&&v!==null)document.getElementById(id).value=v})}
async function loadConfig(){try{const d=await (await fetch('/api/config')).json(); fillConfig(d.config)}catch(e){}}
function fillRunMetadata(d){const m=(d&&d.metadata)||{}; metadataIds.forEach(id=>{if(m[id]!==undefined&&m[id]!==null)document.getElementById(id).value=m[id]}); document.getElementById('nextRun').textContent=d.next_run_id||'--'}
async function loadRunMetadata(){try{const d=await (await fetch('/api/run_metadata')).json(); fillRunMetadata(d)}catch(e){}}
async function tick(){try{const r=await fetch('/api/latest',{cache:'no-store'}); const d=await r.json(); const f=d.fields||{},t=d.timing||{};
 const st=document.getElementById('status'); st.textContent=d.status; st.className='pill '+(d.logging?'on':'off');
 setupStreamActive=!!d.setup_streaming; const setupBtn=document.getElementById('setupStream'); setupBtn.textContent=setupStreamActive?'Stop Setup Stream':'Start Setup Stream'; setupBtn.disabled=!!d.logging;
 document.getElementById('session').textContent=d.session||''; document.getElementById('timingStatus').textContent=d.warning||t.status||'';
 document.getElementById('speed').textContent=fmt(f.Speed_mph,1)+' mph'; document.getElementById('longG').textContent=fmt(f.Longitudinal_G,2)+' g'; document.getElementById('latG').textContent=fmt(f.Lateral_G,2)+' g'; document.getElementById('lapCount').textContent=String(t.lap_count||0);
 document.getElementById('currentTime').textContent=timeFmt(t.current_elapsed_s); document.getElementById('bestTime').textContent=timeFmt(t.best_lap_s);
 const deltaEl=document.getElementById('deltaTime'); deltaEl.className='value';
 if(t.live_delta_error){deltaEl.textContent=t.live_delta_error; deltaEl.className='value small bad'} else {deltaEl.textContent=t.live_delta_available?deltaFmt(t.live_delta_s):'--'}
 document.getElementById('gps').textContent=(fmt(f.Latitude_deg,5)+', '+fmt(f.Longitude_deg,5));
 document.getElementById('stream').textContent=`raw ${d.raw_bytes||0} B / bin ${d.binary_packets||0} pkts / bad ${d.bad_binary_packets||0} / ASCII ${d.ascii_packets||0}`;
 document.getElementById('logHealth').textContent=`${d.log_destination||'unknown'} / ${d.log_health||'unknown'} / ${d.free_space_mb??'--'} MB`;
 document.getElementById('cpuTemp').textContent=Number.isFinite(d.cpu_temp_c)?`${fmt(d.cpu_temp_c,1)} C / ${fmt(d.cpu_temp_f,1)} F`:'--';
 const can=d.can||{}; document.getElementById('canStatus').textContent=`${can.status||'disabled'} / ${can.frames||0} frames / ${can.decoded_frames||0} decoded`;
 document.getElementById('yaw').textContent=fmt(f.Yaw_deg,1)+' deg'; document.getElementById('lat').textContent=fmt(f.Latitude_deg,7);
 document.getElementById('lon').textContent=fmt(f.Longitude_deg,7); document.getElementById('pos').textContent=fmt(f.PosUncertainty_m,2)+' m';
 document.getElementById('checksum').innerHTML=d.checksum_ok===true?'<span class="ok">true</span>':String(d.checksum_ok??'--');
 document.getElementById('laps').innerHTML=(t.completed||[]).slice().reverse().map(l=>`<tr><td>${l.number}</td><td>${l.type}</td><td>${timeFmt(l.duration_s)}</td><td>${deltaFmt(l.delta_to_best_s)}</td><td class="${l.warning?'bad':''}">${l.warning||''}</td></tr>`).join('');
 if(d.run_metadata&&d.run_metadata.next_run_id) document.getElementById('nextRun').textContent=d.run_metadata.next_run_id;
 drawTrack(t);
 if(Number.isFinite(f.Speed_mph)){history.push(f.Speed_mph); while(history.length>maxN) history.shift(); draw();}
}catch(e){document.getElementById('status').textContent='offline'}}
loadConfig(); loadRunMetadata(); setInterval(tick,250); tick();
</script>
</body>
</html>
"""


class DashboardHandler(BaseHTTPRequestHandler):
    def send_json(self, payload: dict, status: int = 200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8") if length else "{}"
        return json.loads(body)

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index.html"):
            body = DASHBOARD_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path.startswith("/api/latest"):
            with state_lock:
                age_s = None
                if latest_packet.get("updated_monotonic") is not None:
                    age_s = time.monotonic() - latest_packet["updated_monotonic"]
                payload = {k: v for k, v in latest_packet.items() if k != "updated_monotonic"}
                if isinstance(payload.get("can"), dict):
                    payload["can"] = {k: v for k, v in payload["can"].items() if k != "_last_monotonic"}
                payload["age_s"] = age_s
            payload["setup_streaming"] = setup_stream_requested.is_set()
            payload["timing"] = public_timing_snapshot()
            payload["run_metadata"] = public_run_metadata_snapshot()
            cpu_temp_c = cpu_temperature_c()
            payload["cpu_temp_c"] = cpu_temp_c
            payload["cpu_temp_f"] = None if cpu_temp_c is None else cpu_temp_c * 9.0 / 5.0 + 32.0
            self.send_json(payload)
            return

        if self.path.startswith("/api/run_metadata"):
            self.send_json(public_run_metadata_snapshot())
            return

        if self.path.startswith("/api/config"):
            self.send_json({"config": public_timing_snapshot()["config"], "timing": public_timing_snapshot()})
            return

        self.send_error(404)

    def do_POST(self):
        if self.path.startswith("/api/setup_stream"):
            try:
                enabled = self.read_json_body().get("enabled")
            except json.JSONDecodeError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
                return
            if not isinstance(enabled, bool):
                self.send_json({"ok": False, "error": "enabled must be true or false"}, status=400)
                return
            with state_lock:
                logging_active = bool(latest_packet.get("logging"))
            if enabled and (logging_requested.is_set() or logging_active):
                self.send_json(
                    {"ok": False, "error": "Stop the active logging run before starting track setup data."},
                    status=409,
                )
                return
            if enabled:
                setup_stream_requested.set()
                logging.info("Dashboard requested non-recording track setup data")
            else:
                setup_stream_requested.clear()
                logging.info("Dashboard stopped non-recording track setup data")
            self.send_json({"ok": True, "setup_streaming": setup_stream_requested.is_set()})
            return

        if self.path.startswith("/api/config"):
            try:
                configure_timing(self.read_json_body())
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
                return
            self.send_json({"ok": True, "timing": public_timing_snapshot()})
            return

        if self.path.startswith("/api/run_metadata"):
            try:
                update_run_metadata(self.read_json_body())
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
                return
            payload = public_run_metadata_snapshot()
            payload["ok"] = True
            self.send_json(payload)
            return

        if self.path.startswith("/api/reset_timing"):
            with timing_lock:
                reset_timing_state("waiting for start" if timing_state["configured"] else "not configured")
            self.send_json({"ok": True, "timing": public_timing_snapshot()})
            return

        self.send_error(404)

    def log_message(self, format, *args):
        return


def start_dashboard(host: str, port: int):
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logging.info("Dashboard listening on http://%s:%d", host, port)
    return server


def wait_for_acquisition_request():
    while not stop_requested.is_set():
        if logging_requested.is_set() or setup_stream_requested.is_set():
            return True
        stop_requested.wait(0.25)
    return False


def perform_shutdown(shutdown_command: str):
    logging.info("Running shutdown command: %s", shutdown_command)
    subprocess.Popen(shlex.split(shutdown_command))


def main():
    global active_log_dir, active_base_log_dir, active_log_destination_type
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default=None)
    parser.add_argument("--baud", type=int, default=921600)
    parser.add_argument("--auto-start", action="store_true", help="start logging immediately after boot")
    parser.add_argument("--no-buttons", action="store_true")
    parser.add_argument("--log-gpio", type=int, default=17)
    parser.add_argument("--power-gpio", type=int, default=27)
    parser.add_argument("--dashboard-host", default="0.0.0.0")
    parser.add_argument("--dashboard-port", type=int, default=8080)
    parser.add_argument("--parse-mode", choices=("auto", "binary", "ascii"), default="auto")
    parser.add_argument("--can-enable", action="store_true", help="passively log CAN frames during each VN300 logging session")
    parser.add_argument("--can-interface", default="socketcan", help="python-can interface type, usually socketcan on Raspberry Pi")
    parser.add_argument("--can-channel", default="can0", help="CAN channel/device name, usually can0")
    parser.add_argument("--can-bitrate", type=int, default=0, help="CAN bitrate such as 1000000; use 0 if the interface is already configured")
    parser.add_argument("--can-signal-map", type=Path, default=DEFAULT_CAN_SIGNAL_MAP, help="CSV signal map used to decode MoTeC/dash CAN frames")
    parser.add_argument("--shutdown-command", default="/usr/bin/sudo /sbin/shutdown -h now")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)

    base_log_dir = find_log_directory()
    active_base_log_dir = base_log_dir
    active_log_destination_type = log_destination_type(base_log_dir)
    active_log_dir = create_boot_log_directory(base_log_dir)
    logging.info("Using base log directory: %s", base_log_dir)
    logging.info("Using boot log directory: %s", active_log_dir)
    logging.info("Log destination type: %s", active_log_destination_type)
    set_log_health(
        "idle",
        log_dir=active_log_dir,
        base_log_dir=base_log_dir,
        destination=active_log_destination_type,
        free_space=free_space_bytes(active_log_dir),
        write_error="",
    )
    if active_log_destination_type != "flash drive":
        set_latest_warning(f"Logging to {active_log_destination_type}, not flash drive")
    start_dashboard(args.dashboard_host, args.dashboard_port)
    can_config = {
        "enabled": args.can_enable,
        "interface": args.can_interface,
        "channel": args.can_channel,
        "bitrate": args.can_bitrate or None,
        "signal_map": args.can_signal_map,
    }

    gpio_handles = None
    if not args.no_buttons:
        gpio_handles = install_gpio_callbacks(args.log_gpio, args.power_gpio, args.shutdown_command)

    if args.auto_start:
        logging_requested.set()

    update_latest("idle", False)
    while not stop_requested.is_set():
        if not wait_for_acquisition_request():
            break

        port = find_serial_port(args.port)
        if not port:
            logging.warning("Serial port not found. Retrying while log request is active...")
            update_latest("waiting for serial", False)
            time.sleep(RECONNECT_DELAY_S)
            continue

        active_session_stop.clear()
        ran_logging_session = logging_requested.is_set()
        try:
            if ran_logging_session:
                run_session(port, args.baud, active_log_dir, base_log_dir, args.parse_mode, can_config)
            elif setup_stream_requested.is_set():
                run_setup_stream(port, args.baud, args.parse_mode)
        except serial.SerialException as exc:
            logging.error("Serial error: %s", exc)
            update_latest("serial error", False)
        except OSError as exc:
            logging.error("I/O error: %s", exc)
            update_latest("io error", False)

        if ran_logging_session:
            logging_requested.clear()
        active_session_stop.clear()
        time.sleep(0.2)

    if gpio_handles:
        for handle in gpio_handles:
            handle.close()

    if shutdown_requested.is_set():
        perform_shutdown(args.shutdown_command)


if __name__ == "__main__":
    main()
