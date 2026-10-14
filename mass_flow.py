"""Direct Raspberry Pi I2C acquisition and run capture for Sensirion SFM3505."""

import csv
import datetime as dt
import logging
import math
import os
import threading
import time
from pathlib import Path


SFM3505_ADDRESS = 0x2E
SFM3505_START = 0x3603
SFM3505_STOP = 0x3FF9
SFM3505_OFFSET = 8388608
SFM3505_SCALE = 25600
FLOW_COLUMNS = ["Mass_Flow_Status", "Mass_Flow_Air_Raw", "Mass_Flow_O2_Raw",
                "Mass_Flow_Air_slm", "Mass_Flow_O2_slm", "Mass_Flow_Range"]


def crc8(data):
    value = 0xFF
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = ((value << 1) ^ (0x31 if value & 0x80 else 0)) & 0xFF
    return value


def decode_packet(packet):
    """Check all three word CRCs before decoding the two offset-binary values."""
    if len(packet) != 9:
        raise OSError("Expected nine SFM3505 I2C bytes")
    if any(crc8(packet[index:index + 2]) != packet[index + 2] for index in (0, 3, 6)):
        raise ValueError("SFM3505 CRC mismatch")
    air_raw = (packet[0] << 16) | (packet[1] << 8) | packet[3]
    o2_raw = (packet[4] << 16) | (packet[6] << 8) | packet[7]
    air_slm, o2_slm = ((raw - SFM3505_OFFSET) / SFM3505_SCALE for raw in (air_raw, o2_raw))
    return {"sample_status": "ok", "air_raw": air_raw, "o2_raw": o2_raw,
            "air_slm": air_slm, "o2_slm": o2_slm,
            "range": "in_range" if all(-150 <= flow <= 300 for flow in (air_slm, o2_slm)) else "outside_range"}


def validate_settings(bus, sample_hz):
    if type(bus) is not int or bus < 0:
        raise ValueError("Mass flow I2C bus must be a nonnegative integer")
    if type(sample_hz) not in (int, float) or not math.isfinite(sample_hz) or not 1 <= sample_hz <= 1000:
        raise ValueError("Mass flow sample rate must be between 1 and 1000 Hz")


class MassFlowReader:
    def __init__(self, bus=1, sample_hz=50):
        validate_settings(bus, sample_hz)
        self.bus_number = bus
        self.sample_hz = sample_hz
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        self.file = self.writer = None
        self.session_start = None
        self.last_flush = 0.0
        self.state = {
            "enabled": True, "sensor": "SFM3505", "transport": "pi-i2c",
            "i2c_bus": bus, "device": f"/dev/i2c-{bus}", "i2c_address": SFM3505_ADDRESS,
            "sample_hz": sample_hz, "status": "connecting", "sample_status": "waiting for data",
            "air_raw": None, "o2_raw": None, "air_slm": None, "o2_slm": None,
            "range": "waiting for data", "range_min_slm": -150, "range_max_slm": 300,
            "reference_temperature_c": 20, "reference_pressure_hpa": 1013.25,
            "offset": SFM3505_OFFSET, "scale_factor": SFM3505_SCALE,
            "samples": 0, "sensor_errors": 0, "reconnects": 0, "recorded_samples": 0,
            "recording": False, "write_error": "", "i2c_error": "", "last_monotonic": None,
        }

    def snapshot(self):
        with self.lock:
            result = dict(self.state)
        last = result.pop("last_monotonic")
        result["last_age_s"] = None if last is None else max(0.0, time.monotonic() - last)
        if result["status"] == "online" and result["last_age_s"] > 2.0:
            result["status"] = "stale"
        return result

    def start(self):
        self.thread = threading.Thread(target=self._read_loop, name="mass-flow-i2c", daemon=True)
        self.thread.start()

    def begin_recording(self, log_dir, file_prefix, session_start):
        with self.lock:
            self.end_recording()
            self.state.update(recorded_samples=0, write_error="")
            self.session_start = session_start
            path = Path(log_dir) / f"{file_prefix}_MASS_FLOW.csv"
            try:
                self.file = path.open("w", newline="", encoding="utf-8")
                self.writer = csv.writer(self.file)
                self.writer.writerow(["Pi_Logger_Elapsed_Time_s", "System_Time_ISO", *FLOW_COLUMNS])
                self.file.flush()
                self.last_flush = time.monotonic()
                self.state["recording"] = True
                logging.info("Mass flow CSV output: %s", path)
            except OSError as exc:
                self._write_failed(exc)

    def _write_failed(self, exc):
        self.state.update(write_error=str(exc), recording=False)
        logging.error("Mass flow CSV write failed: %s", exc)
        self.writer = None
        if self.file:
            try:
                self.file.close()
            except OSError:
                pass
        self.file = None

    def end_recording(self):
        with self.lock:
            if self.file:
                try:
                    self.file.flush()
                    os.fsync(self.file.fileno())
                    self.file.close()
                except OSError as exc:
                    self._write_failed(exc)
            self.file = self.writer = None
            self.state["recording"] = False
            return self.snapshot()

    def accept_packet(self, packet, received_at=None):
        try:
            reading = decode_packet(packet)
        except (OSError, ValueError) as exc:
            self.report_status("crc_error" if isinstance(exc, ValueError) else "i2c_error", str(exc), received_at)
            return False
        self._publish(reading, "online", "", received_at)
        return True

    def report_status(self, status, error="", received_at=None):
        self._publish({"sample_status": status, "air_raw": None, "o2_raw": None,
                       "air_slm": None, "o2_slm": None, "range": status}, status, error, received_at)

    def _publish(self, reading, status, error, received_at):
        received_at = time.monotonic() if received_at is None else received_at
        with self.lock:
            self.state.update(reading, status=status, i2c_error=error)
            if status == "online":
                self.state["last_monotonic"] = received_at
                self.state["samples"] += 1
            elif status in ("i2c_error", "crc_error", "disconnected"):
                self.state["sensor_errors"] += 1
            if self.writer and received_at >= self.session_start:
                try:
                    self.writer.writerow([
                        f"{received_at - self.session_start:.6f}",
                        dt.datetime.now().astimezone().isoformat(timespec="milliseconds"),
                        reading["sample_status"], reading["air_raw"], reading["o2_raw"],
                        "" if reading["air_slm"] is None else f'{reading["air_slm"]:.6f}',
                        "" if reading["o2_slm"] is None else f'{reading["o2_slm"]:.6f}', reading["range"],
                    ])
                    if status == "online":
                        self.state["recorded_samples"] += 1
                    if received_at - self.last_flush >= 1.0:
                        self.file.flush()
                        self.last_flush = received_at
                except OSError as exc:
                    self._write_failed(exc)

    def _read_loop(self):
        # Raw I2C messages: SMBus register/block helpers add bytes this sensor
        # does not expect. Import lazily so web-only and other sensors still work.
        try:
            from smbus2 import SMBus, i2c_msg
        except ImportError as exc:
            self.report_status("disconnected", f"Install smbus2 for Pi mass flow: {exc}")
            return

        def command(bus, value):
            bus.i2c_rdwr(i2c_msg.write(SFM3505_ADDRESS, [value >> 8, value & 0xFF]))

        while not self.stop_event.is_set():
            bus = None
            try:
                bus = SMBus(self.bus_number)
                # A service restart may leave the sensor measuring. Stop only
                # this address, then allow it to enter idle before starting.
                command(bus, SFM3505_STOP)
                if self.stop_event.wait(0.001):
                    break
                command(bus, SFM3505_START)
                self.report_status("warming_up")
                if self.stop_event.wait(0.050):
                    break
                while not self.stop_event.is_set():
                    started = time.monotonic()
                    message = i2c_msg.read(SFM3505_ADDRESS, 9)
                    bus.i2c_rdwr(message)
                    if not self.accept_packet(bytes(message)):
                        break
                    if self.stop_event.wait(max(0, 1 / self.sample_hz - (time.monotonic() - started))):
                        break
            except OSError as exc:
                self.report_status("i2c_error" if bus is not None else "disconnected", str(exc))
            finally:
                if bus is not None:
                    try:
                        command(bus, SFM3505_STOP)
                    except OSError:
                        pass
                    bus.close()
            if not self.stop_event.is_set():
                with self.lock:
                    self.state["reconnects"] += 1
                self.stop_event.wait(1.0)
        self.report_status("stopped")

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2.0)
        self.end_recording()
