"""Paired Arduino analog input, nonlinear conversion, and API/CSV consistency."""
import csv
import json
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

from test_vn300_button_logger import logger
from logger import AirspeedCalibration
from logger import SteeringCalibration, SteeringReader


def calibration():
    return AirspeedCalibration.from_file(Path(__file__).resolve().parents[1] / "config/examples/airspeed_calibration.example.json")


def reader():
    return SteeringReader("/dev/test", calibration=SteeringCalibration(20, 315, 688, -157.5, 157.5),
                          input_format="a0-a1-volts", airspeed_calibration=calibration())


class AirspeedTests(unittest.TestCase):
    def test_curve_knots_interpolation_and_limits(self):
        curve = calibration()
        for voltage, speed in curve.voltage_to_speed:
            self.assertAlmostEqual(curve.convert(voltage)[0], speed)
        self.assertAlmostEqual(curve.convert(1.45)[0], 2.5)
        self.assertEqual(curve.convert(0.4), (0.0, "below_nominal_zero"))
        self.assertEqual(curve.convert(3.3), (None, "above_range"))
        for voltage in (float("nan"), float("inf"), -0.1):
            with self.assertRaises(ValueError):
                curve.convert(voltage)

    def test_reject_invalid_curves(self):
        for points in ([], [(0.5, 0)], [(0.5, 0), (0.5, 1)], [(1, 0), (0.5, 1)],
                       [(0.5, 0), (1, 0)], [(0.5, 1), (1, 2)],
                       [(0.5, 0), (float("nan"), 1)], [(0.5, 0), (True, 1)],
                       [(0.5, 0, 1), (1, 2)], [1, 2], None):
            with self.subTest(points=points), self.assertRaises(ValueError):
                AirspeedCalibration("FS7002-D", "test", points)

    def test_paired_volts_use_existing_steering_calibration(self):
        stream = reader()
        for a0, expected in (("0.098", -157.5), ("1.540", 0), ("3.363", 157.5)):
            stream.accept_line(f"{a0},1.45".encode())
            state = stream.snapshot()
            self.assertAlmostEqual(state["angle_deg"], expected)
            self.assertAlmostEqual(state["airspeed"]["speed_mps"], 2.5)
            self.assertAlmostEqual(state["airspeed"]["speed_mph"], 2.5 / 0.44704)
            self.assertEqual(state["airspeed"]["voltage_v"], 1.45)

    def test_bad_pair_never_updates_one_channel_only(self):
        stream = reader()
        stream.accept_line(b"1.540,0.543")
        original = stream.snapshot()
        for bad in (b"1.540", b"315", b"1,2,3", b"nan,1", b"1,inf", b"-1,1",
                    b"6,1", b"1,5.001", b"1,", b"\xff,1", b"a,b"):
            stream.accept_line(bad)
        current = stream.snapshot()
        self.assertEqual(current["samples"], 1)
        self.assertEqual(current["parse_errors"], 11)
        self.assertEqual(current["raw"], original["raw"])
        self.assertEqual(current["airspeed"]["voltage_v"], original["airspeed"]["voltage_v"])

    def test_csv_preserves_both_voltages_and_speeds_on_same_row(self):
        stream = reader()
        with tempfile.TemporaryDirectory() as directory:
            stream.begin_recording(directory, "RUN001", 10)
            stream.accept_line(b"1.540,1.450", 10.5)
            stream.accept_line(b"3.363,3.300", 10.6)
            state = stream.end_recording()
            with (Path(directory) / "RUN001_STEERING.csv").open() as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["Pi_Logger_Elapsed_Time_s"], "0.500000")
            self.assertEqual(float(rows[0]["Steering_Voltage_V"]), 1.54)
            self.assertEqual(int(rows[0]["Steering_Raw"]), 315)
            self.assertEqual(float(rows[0]["Steering_Angle_deg"]), 0)
            self.assertEqual(float(rows[0]["Airspeed_Voltage_V"]), 1.45)
            self.assertEqual(float(rows[0]["Airspeed_mps"]), 2.5)
            self.assertEqual(rows[1]["Airspeed_mps"], "")
            self.assertEqual(rows[1]["Airspeed_mph"], "")
            self.assertEqual(rows[1]["Airspeed_Range"], "above_range")
            self.assertEqual(state["recorded_samples"], 2)

    def test_air_channel_marks_stale_when_serial_stops(self):
        stream = reader()
        stream.accept_line(b"1.540,0.543", time.monotonic() - 3)
        state = stream.snapshot()
        self.assertEqual(state["status"], "stale")
        self.assertEqual(state["airspeed"]["status"], "stale")
        self.assertEqual(state["last_age_s"], state["airspeed"]["last_age_s"])

    def test_api_exposes_both_channels(self):
        stream = reader()
        stream.accept_line(b"1.540,1.450")
        old_reader = logger.steering_reader
        logger.steering_reader = stream
        server = None
        try:
            server = logger.start_dashboard("127.0.0.1", 0)
            data = json.load(urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/api/latest"))
            self.assertEqual(data["steering"]["angle_deg"], 0)
            self.assertAlmostEqual(data["airspeed"]["speed_mps"], 2.5)
            self.assertEqual(data["airspeed"]["sensor"], "FS7002-D")
            self.assertEqual(data["steering"]["last_age_s"], data["airspeed"]["last_age_s"])
        finally:
            logger.steering_reader = old_reader
            if server:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
