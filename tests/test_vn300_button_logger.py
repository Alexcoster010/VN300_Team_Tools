import csv
import contextlib
import json
import random
import shutil
import struct
import sys
import threading
import time
import types
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from unittest import mock

try:
    import serial  # noqa: F401
except ImportError:
    serial_module = types.ModuleType("serial")
    serial_module.Serial = object
    serial_module.SerialException = OSError
    serial_module.EIGHTBITS = 8
    serial_module.PARITY_NONE = "N"
    serial_module.STOPBITS_ONE = 1
    serial_tools_module = types.ModuleType("serial.tools")
    serial_list_ports_module = types.ModuleType("serial.tools.list_ports")
    serial_list_ports_module.comports = lambda: []
    serial_tools_module.list_ports = serial_list_ports_module
    sys.modules["serial"] = serial_module
    sys.modules["serial.tools"] = serial_tools_module
    sys.modules["serial.tools.list_ports"] = serial_list_ports_module

import logger


class LoggingRestartTests(unittest.TestCase):
    def setUp(self):
        logger.stop_requested.clear()
        logger.logging_requested.clear()
        logger.setup_stream_requested.clear()
        logger.active_session_stop.clear()

    def tearDown(self):
        self.setUp()

    def test_restart_during_flush_preserves_stop_and_next_request(self):
        logger.toggle_logging_request()
        first = logger.begin_acquisition()
        logger.toggle_logging_request()  # Stop the old run.
        logger.toggle_logging_request()  # Queue a new run while it flushes.
        self.assertTrue(logger.active_session_stop.is_set())
        logger.finish_acquisition(*first)
        self.assertTrue(logger.logging_requested.is_set())
        second = logger.begin_acquisition()
        self.assertTrue(second[0])
        self.assertFalse(logger.active_session_stop.is_set())
        logger.finish_acquisition(*second)
        self.assertFalse(logger.logging_requested.is_set())

    def test_pending_restart_can_be_cancelled(self):
        logger.toggle_logging_request()
        first = logger.begin_acquisition()
        for _ in range(3):  # Stop, restart, cancel restart.
            logger.toggle_logging_request()
        logger.finish_acquisition(*first)
        self.assertIsNone(logger.begin_acquisition())

    def test_stop_during_port_discovery_does_not_start_run(self):
        logger.toggle_logging_request()
        logger.toggle_logging_request()
        self.assertIsNone(logger.begin_acquisition())

    def test_button_cannot_restart_after_process_shutdown(self):
        logger.stop_requested.set()
        logger.toggle_logging_request()
        self.assertFalse(logger.logging_requested.is_set())
        self.assertIsNone(logger.begin_acquisition())

    def test_main_accepts_button_restart_after_acquisition_error(self):
        for error in (OSError("USB disconnected"), ValueError("bad packet")):
            with self.subTest(error=error):
                self.setUp()
                runs = []

                def request_run():
                    self.assertFalse(logger.logging_requested.is_set())
                    logger.toggle_logging_request()
                    return True

                def run(*args):
                    runs.append(args)
                    if len(runs) == 1:
                        raise error
                    logger.stop_requested.set()

                with contextlib.ExitStack() as stack:
                    stack.enter_context(mock.patch.object(sys, "argv", ["logger", "--no-buttons"]))
                    for name in ("start_dashboard", "set_log_health", "set_latest_warning", "update_latest"):
                        stack.enter_context(mock.patch.object(logger, name))
                    stack.enter_context(mock.patch.object(logger.signal, "signal"))
                    stack.enter_context(mock.patch.object(logger.time, "sleep"))
                    stack.enter_context(mock.patch.object(logger, "steering_reader", None))
                    stack.enter_context(mock.patch.object(logger, "find_log_directory", return_value=Path("/tmp")))
                    stack.enter_context(mock.patch.object(logger, "create_boot_log_directory", return_value=Path("/tmp")))
                    stack.enter_context(mock.patch.object(logger, "free_space_bytes", return_value=10**10))
                    stack.enter_context(mock.patch.object(logger, "find_serial_port", return_value="TEST"))
                    stack.enter_context(mock.patch.object(logger, "wait_for_acquisition_request", side_effect=request_run))
                    stack.enter_context(mock.patch.object(logger, "run_session", side_effect=run))
                    logger.main()
                self.assertEqual(len(runs), 2)


class LogSwitchTests(unittest.TestCase):
    def setUp(self):
        for event in (logger.stop_requested, logger.shutdown_requested,
                      logger.logging_requested, logger.setup_stream_requested,
                      logger.active_session_stop, logger.recording_busy):
            event.clear()
        self.log_switch = types.SimpleNamespace(is_pressed=False)
        self.power_button = types.SimpleNamespace()
        self.button_factory = mock.Mock(side_effect=[self.log_switch, self.power_button])
        gpiozero = types.ModuleType("gpiozero")
        gpiozero.Button = self.button_factory
        self.gpio_patch = mock.patch.dict(sys.modules, {"gpiozero": gpiozero})
        self.gpio_patch.start()
        self.addCleanup(self.gpio_patch.stop)

    def tearDown(self):
        for event in (logger.stop_requested, logger.shutdown_requested,
                      logger.logging_requested, logger.setup_stream_requested,
                      logger.active_session_stop, logger.recording_busy):
            event.clear()

    def install(self, on=False):
        self.log_switch.is_pressed = on
        return logger.install_gpio_callbacks(17, 27, "unused")

    def switch(self, on):
        self.log_switch.is_pressed = on
        callback = self.log_switch.when_pressed if on else self.log_switch.when_released
        callback(self.log_switch)

    def test_boot_on_requests_capture_and_pauses_analysis(self):
        with mock.patch.object(logger, "capture_guard") as guard:
            self.install(on=True)
        self.assertTrue(logger.logging_requested.is_set())
        guard.assert_called_once_with()
        self.button_factory.assert_has_calls([
            mock.call(17, pull_up=True, bounce_time=logger.LOG_SWITCH_DEBOUNCE_S),
            mock.call(27, pull_up=True, hold_time=logger.POWER_HOLD_S),
        ])

    def test_boot_off_clears_pending_start(self):
        logger.toggle_logging_request(True)
        self.install()
        self.assertFalse(logger.logging_requested.is_set())
        self.assertTrue(logger.active_session_stop.is_set())

    def test_both_edges_set_state_and_repeated_edges_are_idempotent(self):
        self.install()
        self.switch(True)
        generation = logger.logging_request_generation
        self.switch(True)
        self.assertTrue(logger.logging_requested.is_set())
        self.assertEqual(logger.logging_request_generation, generation)
        self.switch(False)
        generation = logger.logging_request_generation
        self.switch(False)
        self.assertFalse(logger.logging_requested.is_set())
        self.assertEqual(logger.logging_request_generation, generation)
        self.assertTrue(logger.active_session_stop.is_set())

    def test_off_on_during_flush_queues_next_run(self):
        self.install(on=True)
        first = logger.begin_acquisition()
        self.switch(False)
        self.switch(True)
        self.assertTrue(logger.active_session_stop.is_set())
        logger.finish_acquisition(*first)
        self.assertTrue(logger.logging_requested.is_set())
        self.assertTrue(logger.begin_acquisition()[0])
        self.assertFalse(logger.active_session_stop.is_set())

    def test_off_cancels_restart_during_flush(self):
        self.install(on=True)
        first = logger.begin_acquisition()
        self.switch(False)
        self.switch(True)
        self.switch(False)
        logger.finish_acquisition(*first)
        self.assertIsNone(logger.begin_acquisition())

    def test_power_hold_stops_capture_and_prevents_switch_restart(self):
        self.install(on=True)
        logger.setup_stream_requested.set()
        self.power_button.when_held()
        self.switch(False)
        self.switch(True)
        self.assertTrue(logger.shutdown_requested.is_set())
        self.assertTrue(logger.stop_requested.is_set())
        self.assertTrue(logger.active_session_stop.is_set())
        self.assertFalse(logger.logging_requested.is_set())
        self.assertFalse(logger.setup_stream_requested.is_set())

    def test_main_boot_switch_takes_precedence_over_auto_start(self):
        for on, no_buttons, auto_start in (
            (False, False, True), (True, False, False),
            (True, False, True), (False, True, True), (False, True, False),
        ):
            with self.subTest(on=on, no_buttons=no_buttons, auto_start=auto_start):
                self.tearDown()
                self.log_switch.is_pressed = on
                self.log_switch.close = mock.Mock()
                self.power_button.close = mock.Mock()
                self.button_factory.side_effect = [self.log_switch, self.power_button]
                argv = ["logger"]
                if no_buttons:
                    argv.append("--no-buttons")
                if auto_start:
                    argv.append("--auto-start")

                def check_request():
                    self.assertEqual(logger.logging_requested.is_set(),
                                     auto_start if no_buttons else on)
                    logger.stop_requested.set()
                    return False

                with contextlib.ExitStack() as stack:
                    stack.enter_context(mock.patch.object(sys, "argv", argv))
                    for name in ("start_dashboard", "set_log_health", "set_latest_warning", "update_latest"):
                        stack.enter_context(mock.patch.object(logger, name))
                    stack.enter_context(mock.patch.object(logger.signal, "signal"))
                    stack.enter_context(mock.patch.object(logger, "steering_reader", None))
                    stack.enter_context(mock.patch.object(logger, "find_log_directory", return_value=Path("/tmp")))
                    stack.enter_context(mock.patch.object(logger, "create_boot_log_directory", return_value=Path("/tmp")))
                    stack.enter_context(mock.patch.object(logger, "free_space_bytes", return_value=10**10))
                    stack.enter_context(mock.patch.object(logger, "wait_for_acquisition_request", side_effect=check_request))
                    logger.main()
                if not no_buttons:
                    self.log_switch.close.assert_called_once_with()
                    self.power_button.close.assert_called_once_with()


class FakeSerial:
    def __init__(self, *args, **kwargs):
        self.read_count = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self, _size):
        self.read_count += 1
        if self.read_count == 1:
            return b"$VNYPR,1,2,3*00\r\n"
        logger.setup_stream_requested.clear()
        return b""


class SetupStreamTests(unittest.TestCase):
    def setUp(self):
        logger.stop_requested.clear()
        logger.logging_requested.clear()
        logger.setup_stream_requested.clear()
        logger.active_session_stop.clear()
        logger.reset_latest_for_setup_stream()
        logger.setup_stream_requested.clear()

    def tearDown(self):
        logger.stop_requested.clear()
        logger.logging_requested.clear()
        logger.setup_stream_requested.clear()
        logger.active_session_stop.clear()
        with logger.state_lock:
            logger.latest_packet["logging"] = False
            logger.latest_packet["setup_streaming"] = False

    def test_web_logging_start_stop_restart_and_validation(self):
        server = logger.start_dashboard("127.0.0.1", 0)
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            url = base + "/api/logging"
            logger.setup_stream_requested.set()
            self.assertTrue(self.post_json(url, {"enabled": True})["logging_requested"])
            self.assertFalse(logger.setup_stream_requested.is_set())
            first = logger.begin_acquisition()
            # Repeating an explicit start must not toggle or create a new request.
            self.post_json(url, {"enabled": True})
            self.assertEqual(first[1], logger.logging_request_generation)
            self.assertFalse(self.post_json(url, {"enabled": False})["logging_requested"])
            self.post_json(url, {"enabled": True})
            self.assertTrue(logger.active_session_stop.is_set())
            logger.finish_acquisition(*first)
            self.assertTrue(logger.logging_requested.is_set())
            with urllib.request.urlopen(base + "/api/latest") as response:
                self.assertTrue(json.load(response)["logging_requested"])
            for invalid in ({}, {"enabled": "true"}, [], None):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    self.post_json(url, invalid)
                self.assertEqual(caught.exception.code, 400)
            logger.stop_requested.set()
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.post_json(url, {"enabled": True})
            self.assertEqual(caught.exception.code, 409)
        finally:
            server.shutdown()
            server.server_close()

    def test_setup_stream_publishes_fields_without_timing_or_run_files(self):
        parsed = {
            "message_type": "VNYPR",
            "values": [1.0, 2.0, 3.0],
            "checksum_ok": True,
        }
        logger.setup_stream_requested.set()
        with mock.patch.object(logger.serial, "Serial", FakeSerial), mock.patch.object(
            logger, "parse_ascii_line", return_value=parsed
        ), mock.patch.object(logger, "update_timing") as update_timing, mock.patch.object(
            logger, "build_run_identity"
        ) as build_run_identity, mock.patch.object(
            logger, "write_session_metadata"
        ) as write_session_metadata, mock.patch.object(
            logger, "write_run_metadata_csv"
        ) as write_run_metadata_csv:
            logger.run_setup_stream("COM_TEST", 921600, "ascii")

        with logger.state_lock:
            fields = dict(logger.latest_packet["fields"])
            session = logger.latest_packet["session"]
            logging_active = logger.latest_packet["logging"]
        self.assertEqual(fields["Yaw_deg"], 1.0)
        self.assertIsNone(session)
        self.assertFalse(logging_active)
        update_timing.assert_not_called()
        build_run_identity.assert_not_called()
        write_session_metadata.assert_not_called()
        write_run_metadata_csv.assert_not_called()

    def test_setup_stream_api_toggles_and_rejects_active_logging(self):
        server = logger.start_dashboard("127.0.0.1", 0)
        try:
            base_url = f"http://127.0.0.1:{server.server_port}"
            payload = self.post_json(f"{base_url}/api/setup_stream", {"enabled": True})
            self.assertTrue(payload["setup_streaming"])
            self.assertTrue(logger.setup_stream_requested.is_set())

            self.post_json(f"{base_url}/api/setup_stream", {"enabled": False})
            self.assertFalse(logger.setup_stream_requested.is_set())

            logger.logging_requested.set()
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.post_json(f"{base_url}/api/setup_stream", {"enabled": True})
            self.assertEqual(caught.exception.code, 409)
            self.assertFalse(logger.setup_stream_requested.is_set())
        finally:
            server.shutdown()
            server.server_close()

    @staticmethod
    def post_json(url, payload):
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=2.0) as response:
            return json.loads(response.read().decode("utf-8"))


def make_binary_packet(startup_ns=1_000_000_000):
    payload = bytearray(logger.VN300_BINARY_PAYLOAD_LEN)
    struct.pack_into("<Q", payload, 0, startup_ns)
    without_crc = logger.VN300_BINARY_HEADER + payload
    crc = logger.vectornav_crc16(without_crc[1:])
    return without_crc + crc.to_bytes(2, "big")


class BinaryPacketIntegrityTests(unittest.TestCase):
    def test_crc_matches_independent_bitwise_reference(self):
        def reference(data):
            crc = 0
            for byte in data:
                crc ^= byte << 8
                for _ in range(8):
                    crc = ((crc << 1) ^ (0x1021 if crc & 0x8000 else 0)) & 0xFFFF
            return crc

        rng = random.Random(300)
        for length in (0, 1, 19, 256, 505, 506, 4096):
            data = bytes(rng.randrange(256) for _ in range(length))
            with self.subTest(length=length):
                self.assertEqual(logger.vectornav_crc16(data), reference(data))

    def test_vectornav_crc_matches_standard_check_value(self):
        self.assertEqual(logger.vectornav_crc16(b"123456789"), 0x31C3)

    def test_valid_packet_parses_and_corrupt_packet_is_rejected(self):
        packet = make_binary_packet(2_500_000_000)
        self.assertEqual(len(packet), logger.VN300_BINARY_PACKET_LEN)
        self.assertTrue(logger.binary_packet_crc_ok(packet))
        fields = logger.parse_vn300_binary_packet(packet)
        self.assertEqual(fields["Binary_TimeStartup_ns"], 2_500_000_000)
        self.assertTrue(fields["Checksum_OK"])

        corrupt = bytearray(packet)
        corrupt[100] ^= 0x01
        self.assertFalse(logger.binary_packet_crc_ok(bytes(corrupt)))
        self.assertIsNone(logger.parse_vn300_binary_packet(bytes(corrupt)))

    def test_extractor_resynchronizes_after_crc_failure(self):
        valid = make_binary_packet()
        corrupt = bytearray(valid)
        corrupt[200] ^= 0x80
        buffer = bytearray(b"serial-noise" + corrupt + valid)

        packets, crc_errors, skipped_bytes = logger.extract_vn300_binary_packets(buffer)

        self.assertEqual(packets, [valid])
        self.assertEqual(crc_errors, 1)
        self.assertGreaterEqual(skipped_bytes, len(b"serial-noise") + len(corrupt))
        self.assertEqual(buffer, bytearray())


class TraceSpeedTests(unittest.TestCase):
    def test_captured_speed_survives_best_trace_and_next_run(self):
        with mock.patch.dict(logger.timing_state):
            logger.reset_timing_state()
            first = {"t": 10.0, "lat": 35.0, "lon": -97.0, "speed_mph": 12.3}
            logger.start_timed_segment(10.0, first, "lap running")
            logger.append_current_trace({**first, "t": 10.1, "speed_mph": 15.0})
            logger.append_current_trace({**first, "t": 11.0, "lon": -96.9999, "speed_mph": 29.2})
            logger.complete_timed_segment(12.0, "lap")
            logger.start_timed_segment(12.0, {**first, "t": 12.0, "speed_mph": 88.0}, "lap running")
            snapshot = logger.public_timing_snapshot()
            self.assertEqual([p["speed_mph"] for p in snapshot["best_trace"]], [12.3, 29.2])
            self.assertEqual(snapshot["current_trace"][0]["speed_mph"], 88.0)
            self.assertEqual(snapshot["best_trace"][1]["elapsed_s"], 1.0)

    def test_missing_speed_is_unavailable_instead_of_zero(self):
        with mock.patch.dict(logger.timing_state):
            logger.reset_timing_state()
            logger.start_timed_segment(0.0, {"t": 0.0, "lat": 35.0, "lon": -97.0}, "lap running")
            self.assertIsNone(logger.public_timing_snapshot()["current_trace"][0]["speed_mph"])


class ReferenceLookupTests(unittest.TestCase):
    def test_interpolation_and_stationary_points(self):
        trace = [
            {"distance_m": distance, "elapsed_s": elapsed}
            for distance, elapsed in ((0, 0), (10, 1), (10, 2), (20, 3), (40, 5))
        ]
        with mock.patch.dict(logger.timing_state, best_trace=trace, best_lap_s=6):
            for distance, expected in ((-1, 0), (0, 0), (5, 0.5), (10, 1),
                                       (15, 2.5), (20, 3), (30, 4), (40, 5), (41, 6)):
                with self.subTest(distance=distance):
                    self.assertEqual(logger.best_time_at_distance(distance), expected)

    def test_long_reference_matches_linear_interpolation(self):
        rng = random.Random(300)
        distance = 0
        trace = []
        for index in range(2500):
            distance += rng.choice((0, 1, 2, 3))
            trace.append({"distance_m": distance, "elapsed_s": index * 0.2})
        with mock.patch.dict(logger.timing_state, best_trace=trace, best_lap_s=500):
            for _ in range(200):
                target = rng.uniform(trace[0]["distance_m"] + 0.01, distance)
                for prev, cur in zip(trace, trace[1:]):
                    if prev["distance_m"] <= target <= cur["distance_m"]:
                        fraction = (target - prev["distance_m"]) / (cur["distance_m"] - prev["distance_m"])
                        expected = prev["elapsed_s"] + fraction * (cur["elapsed_s"] - prev["elapsed_s"])
                        break
                self.assertAlmostEqual(logger.best_time_at_distance(target), expected)

    def test_missing_reference_and_distance_beyond_unfinished_reference(self):
        for trace in ([], [{"distance_m": 0, "elapsed_s": 0}]):
            with mock.patch.dict(logger.timing_state, best_trace=trace):
                self.assertIsNone(logger.best_time_at_distance(0))
        trace = [{"distance_m": 0, "elapsed_s": 0}, {"distance_m": 10, "elapsed_s": 1}]
        with mock.patch.dict(logger.timing_state, best_trace=trace, best_lap_s=None):
            self.assertIsNone(logger.best_time_at_distance(11))


class SerialCaptureTests(unittest.TestCase):
    class BurstSerial:
        def __init__(self, chunks, stop_event):
            self.chunks = list(chunks)
            self.stop_event = stop_event

        def read(self, _size):
            if self.chunks:
                return self.chunks.pop(0)
            self.stop_event.set()
            return b""

    def test_reader_preserves_burst_while_consumer_is_delayed(self):
        stop_event = threading.Event()
        expected = [b"first", b"second", b"third", b"fourth"]
        capture = logger.SerialChunkCapture(
            self.BurstSerial(expected, stop_event),
            stop_event.is_set,
            max_queue_chunks=2,
        ).start()
        time.sleep(0.12)

        received = []
        while capture.is_alive() or not capture.empty():
            try:
                _captured_at, chunk = capture.get(timeout=0.2)
            except logger.queue.Empty:
                continue
            received.append(chunk)
        capture.join(timeout=1.0)

        self.assertEqual(received, expected)
        self.assertIsNone(capture.error)
        self.assertGreater(capture.queue_full_events, 0)


class BinaryHealthTests(unittest.TestCase):
    def test_sensor_time_gap_is_counted(self):
        logger.reset_latest_for_session("health-test")
        for sample_time in (10.0, 10.01, 10.11):
            logger.update_latest_fields(
                "logging",
                True,
                "health-test",
                {"Pi_Elapsed_Time_s": sample_time, "Checksum_OK": True},
                timing_active=False,
            )

        with logger.state_lock:
            snapshot = dict(logger.latest_packet)
        self.assertEqual(snapshot["binary_packets"], 3)
        self.assertEqual(snapshot["binary_time_gaps"], 1)
        self.assertEqual(snapshot["binary_missing_samples_estimate"], 9)
        self.assertAlmostEqual(snapshot["largest_binary_gap_s"], 0.10, places=6)
        self.assertAlmostEqual(snapshot["binary_effective_hz"], 2 / 0.11, places=6)


class SessionPipelineTests(unittest.TestCase):
    class PacketSerial:
        def __init__(self, chunks):
            self.chunks = list(chunks)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def read(self, _size):
            if self.chunks:
                return self.chunks.pop(0)
            logger.active_session_stop.set()
            return b""

    def setUp(self):
        logger.stop_requested.clear()
        logger.logging_requested.clear()
        logger.active_session_stop.clear()
        logger.shutdown_requested.clear()

    def tearDown(self):
        logger.stop_requested.clear()
        logger.logging_requested.clear()
        logger.active_session_stop.clear()
        logger.shutdown_requested.clear()

    def test_session_writes_complete_raw_stream_and_validated_csv(self):
        packets = [
            make_binary_packet(1_000_000_000),
            make_binary_packet(1_010_000_000),
            make_binary_packet(1_020_000_000),
        ]
        stream = b"".join(packets)
        chunks = [stream[:333], stream[333:1200], stream[1200:]]
        fake_serial = self.PacketSerial(chunks)

        log_dir = Path(__file__).resolve().parents[1] / "archive/work" / f"session_pipeline_test_{uuid.uuid4().hex}"
        log_dir.mkdir(parents=True)
        try:
            with mock.patch.object(
                logger.serial, "Serial", return_value=fake_serial
            ), mock.patch.object(logger, "free_space_bytes", return_value=1024 * 1024 * 1024):
                logger.run_session(
                    "COM_TEST",
                    921600,
                    log_dir,
                    log_dir,
                    parse_mode="binary",
                    can_config={"enabled": False},
                )

            raw_path = next(log_dir.glob("*_COM_TEST.bin"))
            binary_path = next(log_dir.glob("*_BINARY.csv"))
            metadata_path = next(log_dir.glob("*_session_metadata.json"))
            raw_bytes = raw_path.read_bytes()
            rows = list(csv.DictReader(binary_path.read_text(encoding="utf-8").splitlines()))
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        finally:
            shutil.rmtree(log_dir, ignore_errors=True)

        self.assertEqual(raw_bytes, stream)
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row["Checksum_OK"] == "True" for row in rows))
        self.assertEqual(metadata["binary_packets"], 3)
        self.assertEqual(metadata["binary_crc_errors"], 0)
        self.assertEqual(metadata["binary_time_gaps"], 0)
        self.assertEqual(metadata["capture_queue_full_events"], 0)


if __name__ == "__main__":
    unittest.main()
