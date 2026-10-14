"""Steering serial framing, recording boundaries, and failure isolation."""
import csv
import json
import tempfile
import time
import types
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from test_vn300_button_logger import logger
import logger as steering


class SteeringTests(unittest.TestCase):
    def test_calibration_endpoints_and_unequal_side_ranges(self):
        calibration = steering.SteeringCalibration(20, 315, 688, -157.5, 157.5)
        for raw, expected in ((20, -157.5), (315, 0), (688, 157.5),
                              (167.5, -78.75), (501.5, 78.75)):
            self.assertAlmostEqual(calibration.angle(raw), expected)
        self.assertAlmostEqual(calibration.angle(314), -157.5 / 295)
        self.assertAlmostEqual(calibration.angle(316), 157.5 / 373)
        self.assertLess(calibration.angle(19), -157.5)
        self.assertGreater(calibration.angle(689), 157.5)

    def test_calibration_rejects_invalid_or_incomplete_profiles(self):
        good = dict(raw_left=20, raw_center=315, raw_right=688,
                    left_angle_deg=-157.5, right_angle_deg=157.5)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "calibration.json"
            path.write_text(json.dumps(good))
            self.assertEqual(steering.SteeringCalibration.from_file(path).angle(315), 0)
            for bad in ({**good, "raw_center": 20}, {**good, "raw_right": 300},
                        {**good, "left_angle_deg": 1}, {**good, "right_angle_deg": 0},
                        {**good, "raw_left": float("nan")}, {**good, "raw_left": True},
                        {**good, "raw_left": "20"}, {**good, "unknown": 1}, {}, []):
                path.write_text(json.dumps(bad))
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    steering.SteeringCalibration.from_file(path)

    def test_calibrated_csv_keeps_raw_and_marks_extrapolation(self):
        reader = steering.SteeringReader("/dev/test", calibration=steering.SteeringCalibration(20, 315, 688, -157.5, 157.5))
        with tempfile.TemporaryDirectory() as directory:
            reader.begin_recording(directory, "RUN001", 0)
            for raw in (20, 315, 688, 690):
                reader.accept_line(str(raw).encode())
            state = reader.end_recording()
            with (Path(directory) / "RUN001_STEERING.csv").open() as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual([int(r["Steering_Raw"]) for r in rows], [20, 315, 688, 690])
            self.assertEqual([float(r["Steering_Angle_deg"]) for r in rows[:3]], [-157.5, 0, 157.5])
            self.assertEqual([r["Steering_Outside_Calibration"] for r in rows], ["False", "False", "False", "True"])
            self.assertTrue(state["calibrated"])
            self.assertEqual(state["calibration"]["raw_center"], 315)
            self.assertTrue(state["outside_calibration"])

    def test_webpanel_calibration_saves_applies_and_survives_restart(self):
        reader = steering.SteeringReader("/dev/test")
        reader.accept_line(b"315")
        profile = dict(raw_left=20, raw_center=315, raw_right=688,
                       left_angle_deg=-157.5, right_angle_deg=157.5)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "steering_calibration.json"
            with mock.patch.object(logger, "steering_reader", reader), \
                 mock.patch.object(logger, "steering_calibration_path", path):
                logger.logging_requested.clear()
                server = logger.start_dashboard("127.0.0.1", 0)
                url = f"http://127.0.0.1:{server.server_port}"
                def post(data):
                    req = urllib.request.Request(
                        url + "/api/steering_calibration",
                        data=json.dumps(data).encode(),
                        headers={"Content-Type": "application/json"}, method="POST")
                    return urllib.request.urlopen(req)
                try:
                    html = urllib.request.urlopen(url).read().decode()
                    self.assertIn("Capture Center", html)
                    self.assertIn("Save Steering Calibration", html)
                    self.assertTrue(json.load(post(profile))["steering"]["calibrated"])
                    self.assertEqual(steering.SteeringCalibration.from_file(path).raw_center, 315)
                    self.assertEqual(reader.snapshot()["angle_deg"], 0)
                    reader.accept_line(b"688")
                    self.assertEqual(reader.snapshot()["angle_deg"], 157.5)

                    with self.assertRaises(urllib.error.HTTPError) as invalid:
                        post({**profile, "raw_center": 20})
                    self.assertEqual(invalid.exception.code, 400)
                    self.assertEqual(steering.SteeringCalibration.from_file(path).raw_center, 315)

                    reader.begin_recording(directory, "RUN001", 0)
                    with self.assertRaises(urllib.error.HTTPError) as active:
                        post({**profile, "raw_center": 320})
                    self.assertEqual(active.exception.code, 409)
                    reader.accept_line(b"320")
                    reader.end_recording()
                    with (Path(directory) / "RUN001_STEERING.csv").open() as stream:
                        rows = list(csv.DictReader(stream))
                    self.assertAlmostEqual(float(rows[0]["Steering_Angle_deg"]),
                                           profile["right_angle_deg"] * 5 / (688 - 315), places=5)
                    self.assertEqual(steering.SteeringCalibration.from_file(path).raw_center, 315)
                finally:
                    server.shutdown()
                    server.server_close()

    def test_raw_integer_validation_and_stale_state(self):
        reader = steering.SteeringReader("/dev/test")
        reader.accept_line(b"295\r\n", time.monotonic() - 3)
        for bad in (b"", b"nan", b"3.5", b"value=295", b"1,2", b"\xff", b"1" * 129):
            reader.accept_line(bad)
        state = reader.snapshot()
        self.assertEqual(state["raw"], 295)
        self.assertEqual(state["samples"], 1)
        self.assertEqual(state["parse_errors"], 7)
        self.assertEqual(state["status"], "stale")
        self.assertFalse(state["calibrated"])

    def test_recording_boundaries_and_shared_run_time(self):
        reader = steering.SteeringReader("/dev/test")
        with tempfile.TemporaryDirectory() as directory:
            reader.accept_line(b"290", 9.0)
            reader.begin_recording(directory, "RUN001", 10.0)
            reader.accept_line(b"291", 9.9)  # received before start, queued on lock
            reader.accept_line(b"292", 10.125)
            state = reader.end_recording()
            reader.accept_line(b"293", 11.0)
            reader.begin_recording(directory, "RUN002", 12.0)
            reader.accept_line(b"294", 12.5)
            reader.end_recording()
            for name, expected, elapsed in (("RUN001", "292", "0.125000"), ("RUN002", "294", "0.500000")):
                with (Path(directory) / f"{name}_STEERING.csv").open() as stream:
                    rows = list(csv.DictReader(stream))
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["Steering_Raw"], expected)
                self.assertEqual(rows[0]["Pi_Logger_Elapsed_Time_s"], elapsed)
                self.assertNotIn("Steering_Angle_deg", rows[0])
            self.assertEqual(state["recorded_samples"], 1)
            self.assertFalse(state["recording"])

    def test_write_error_keeps_live_serial_readings_available(self):
        reader = steering.SteeringReader("/dev/test")
        with tempfile.TemporaryDirectory() as directory:
            reader.begin_recording(directory, "RUN001", 0.0)
            reader.writer = mock.Mock()
            reader.writer.writerow.side_effect = OSError("disk full")
            reader.accept_line(b"123")
            reader.accept_line(b"124")
            state = reader.snapshot()
            self.assertEqual(state["raw"], 124)
            self.assertEqual(state["write_error"], "disk full")
            self.assertFalse(state["recording"])

    def test_fragmented_lines_and_reconnect(self):
        reader = steering.SteeringReader("/dev/test")
        chunks = iter([b"partial\n", b"29", b"5\r\n", b"bad\n", b"1" * 128,
                       b"2" * 128, b"3\n", b"296\n"])
        def read_until(*args, **kwargs):
            try:
                return next(chunks)
            except StopIteration:
                reader.stop_event.set()
                return b""
        stream = mock.MagicMock()
        stream.__enter__.return_value = stream
        stream.read_until.side_effect = read_until
        with mock.patch.object(steering.serial, "Serial", side_effect=[OSError("unplugged"), stream]) as factory, mock.patch.object(reader.stop_event, "wait", return_value=False):
            reader._read_loop()
        self.assertEqual(factory.call_count, 2)
        self.assertEqual(reader.state["samples"], 2)
        self.assertEqual(reader.state["raw"], 296)
        self.assertEqual(reader.state["parse_errors"], 2)
        self.assertEqual(reader.state["reconnects"], 1)

    def test_arduino_excluded_from_vn300_autodetection(self):
        with tempfile.TemporaryDirectory() as directory:
            device = Path(directory) / "ttyACM0"
            device.touch()
            stable = Path(directory) / "arduino-by-id"
            stable.symlink_to(device)
            port = types.SimpleNamespace(device=str(device), description="USB Serial", manufacturer="Arduino")
            with mock.patch.object(logger.list_ports, "comports", return_value=[port]):
                self.assertIsNone(logger.find_serial_port(None, str(stable)))
                self.assertIsNone(logger.find_serial_port(str(device), str(stable)))

    def test_session_closes_steering_recording_when_vn300_fails(self):
        # Exercise the actual run cleanup, including a serial failure before capture.
        reader = steering.SteeringReader("/dev/test")
        logger.stop_requested.clear()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with mock.patch.object(logger.serial, "Serial", side_effect=OSError("VN300 missing")):
                with self.assertRaises(OSError):
                    logger.run_session("/dev/test-vn300", 921600, path, path, steering=reader)
            self.assertFalse(reader.snapshot()["recording"])
            self.assertIsNone(reader.file)
            self.assertEqual(len(list(path.glob("*_STEERING.csv"))), 1)


if __name__ == "__main__":
    unittest.main()
