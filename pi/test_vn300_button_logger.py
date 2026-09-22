import csv
import io
import json
import shutil
import struct
import sys
import threading
import time
import tempfile
import zipfile
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

from pi import vn300_button_logger as logger


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


class LivePipelineLatencyTests(unittest.TestCase):
    class PacedPacketSerial:
        def __init__(self, packets, stop_event):
            self.packets = iter(packets)
            self.stop_event = stop_event

        def read(self, _size):
            try:
                time.sleep(0.001)
                return next(self.packets)
            except StopIteration:
                self.stop_event.set()
                return b""

    def test_dashboard_uses_recent_reader_sample_while_durable_consumer_falls_behind(self):
        """A slow CSV/raw path cannot make the published sample FIFO-aged."""
        stop_event = threading.Event()
        logger.reset_latest_for_session("latency-test")
        start = time.monotonic()
        parser = logger.LiveChunkParser("latency-test", start, "binary")
        packets = [make_binary_packet(1_000_000_000 + index * 10_000_000) for index in range(80)]
        # The real binary decoder is covered separately. Make it cheap here so
        # durable throughput, rather than CPU parsing, is the limiting resource.
        with mock.patch.object(logger, "extract_vn300_binary_packets", return_value=([b"packet"], 0, 0)), mock.patch.object(
            logger, "parse_vn300_binary_packet", return_value={"Pi_Elapsed_Time_s": 1.0, "Checksum_OK": True}
        ):
            capture = logger.SerialChunkCapture(
                self.PacedPacketSerial(packets, stop_event),
                stop_event.is_set,
                max_queue_chunks=8,
                on_chunk=parser.feed,
            ).start()
            # Deliberately make durable consumption 10x slower than ingress.
            while capture.is_alive() or not capture.empty():
                try:
                    capture.get(timeout=0.02)
                except logger.queue.Empty:
                    continue
                time.sleep(0.010)
                with logger.state_lock:
                    age = time.monotonic() - logger.latest_packet["live_capture_monotonic"]
                self.assertLess(age, 0.16)
            capture.join(timeout=1.0)
        self.assertEqual(capture.max_queue_depth, 8)
        self.assertIsNone(capture.error)



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

        log_dir = Path.cwd() / "work" / f"session_pipeline_test_{uuid.uuid4().hex}"
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


class CaptureIoTests(unittest.TestCase):
    def test_active_capture_checkpoint_never_fsyncs_outputs(self):
        raw = mock.Mock()
        ascii_outputs = mock.Mock()
        binary_outputs = mock.Mock()

        with mock.patch.object(logger.os, "fsync") as fsync:
            logger.buffered_capture_checkpoint(raw, ascii_outputs, binary_outputs)

        raw.flush.assert_called_once_with()
        ascii_outputs.flush.assert_called_once_with(sync=False)
        binary_outputs.flush.assert_called_once_with(sync=False)
        fsync.assert_not_called()


class DownloadLogsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.current = root / "current"
        self.fallback = root / "fallback"
        self.current.mkdir()
        self.fallback.mkdir()
        self.old_base = logger.active_base_log_dir
        self.old_fallback = logger.LOCAL_FALLBACK
        logger.active_base_log_dir = self.current
        logger.LOCAL_FALLBACK = self.fallback
        self.addCleanup(self.restore_roots)
        self.server = logger.start_dashboard("127.0.0.1", 0)
        self.addCleanup(self.stop_server)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def restore_roots(self):
        logger.active_base_log_dir = self.old_base
        logger.LOCAL_FALLBACK = self.old_fallback

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()

    def test_archive_contents_headers_and_dashboard_link(self):
        (self.current / "VN300_BOOT_1").mkdir()
        (self.current / "VN300_BOOT_1" / "run.csv").write_bytes(b"one")
        (self.fallback / "old.csv").write_bytes(b"two")
        with urllib.request.urlopen(self.url + "/", timeout=2) as response:
            self.assertIn(b"Download All Logs", response.read())
        with urllib.request.urlopen(self.url + "/api/download_logs", timeout=2) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers["Content-Type"], "application/zip")
            self.assertIn("attachment; filename=", response.headers["Content-Disposition"])
            body = response.read()
            self.assertEqual(int(response.headers["Content-Length"]), len(body))
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            self.assertEqual(archive.namelist(), ["current/VN300_BOOT_1/run.csv", "pi-local/old.csv"])
            self.assertEqual(archive.read("current/VN300_BOOT_1/run.csv"), b"one")
            self.assertEqual(archive.read("pi-local/old.csv"), b"two")

    def test_skips_symlinks_and_deduplicates_roots(self):
        outside = Path(self.temp.name) / "secret.txt"
        outside.write_text("secret")
        (self.current / "safe.txt").write_text("safe")
        (self.current / "link.txt").symlink_to(outside)
        (self.current / "linked-dir").symlink_to(self.fallback, target_is_directory=True)
        logger.LOCAL_FALLBACK = self.current
        with urllib.request.urlopen(self.url + "/api/download_logs", timeout=2) as response:
            with zipfile.ZipFile(io.BytesIO(response.read())) as archive:
                self.assertEqual(archive.namelist(), ["current/safe.txt"])

    def test_no_logs_and_archive_error(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(self.url + "/api/download_logs", timeout=2)
        self.assertEqual(caught.exception.code, 404)
        self.assertIn("No logs", caught.exception.read().decode())
        caught.exception.close()
        (self.current / "one.txt").write_text("one")
        with mock.patch.object(logger, "write_log_archive", side_effect=OSError("disk full")):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(self.url + "/api/download_logs", timeout=2)
        self.assertEqual(caught.exception.code, 500)
        caught.exception.close()

    def test_polling_while_archive_is_built(self):
        (self.current / "one.txt").write_text("one")
        started = threading.Event()
        release = threading.Event()
        original = logger.write_log_archive
        def slow_archive(destination):
            started.set()
            self.assertTrue(release.wait(2))
            return original(destination)
        result = []
        def download():
            with urllib.request.urlopen(self.url + "/api/download_logs", timeout=3) as response:
                result.append(response.status)
        with mock.patch.object(logger, "write_log_archive", side_effect=slow_archive):
            worker = threading.Thread(target=download)
            worker.start()
            try:
                self.assertTrue(started.wait(2))
                with urllib.request.urlopen(self.url + "/api/latest", timeout=1) as response:
                    self.assertEqual(response.status, 200)
            finally:
                release.set()
                worker.join(3)
        self.assertEqual(result, [200])


if __name__ == "__main__":
    unittest.main()
