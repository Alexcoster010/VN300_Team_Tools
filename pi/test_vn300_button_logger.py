import json
import sys
import types
import unittest
import urllib.error
import urllib.request
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


if __name__ == "__main__":
    unittest.main()
