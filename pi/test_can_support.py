"""Hardware-independent CAN profile, DBC and receive-loop regression checks."""
import csv
import json
import shutil
import uuid
from contextlib import contextmanager
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from pi.test_vn300_button_logger import logger


@contextmanager
def temporary_directory():
    root = Path(__file__).resolve().parents[1] / "build" / "test_can"
    root.mkdir(parents=True, exist_ok=True)
    path = root / uuid.uuid4().hex
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path)

try:
    import can
    import cantools
except ImportError:
    can = cantools = None


class CanProfileTests(unittest.TestCase):
    def test_profile_paths_and_explicit_enable(self):
        with temporary_directory() as directory:
            path = Path(directory) / "profile.json"
            path.write_text(json.dumps({"interface": "slcan", "channel": "/dev/serial/by-id/adapter",
                                        "dbc": "vehicle.dbc", "signal_map": None,
                                        "bus_options": {"tty_baudrate": 115200}}))
            config = logger.resolve_can_config({"enabled": False, "profile": path})
            self.assertFalse(config["enabled"])
            self.assertEqual(config["dbc"], Path(directory).resolve() / "vehicle.dbc")
            self.assertEqual(config["interface"], "slcan")

    def test_reject_bad_profile(self):
        with temporary_directory() as directory:
            path = Path(directory) / "bad.json"
            for profile in ([], {"enabled": True}, {"bitrate": -1}, {"channel": False},
                            {"bus_options": {"interface": "virtual"}}, {"dbc": 42}):
                with self.subTest(profile=profile):
                    path.write_text(json.dumps(profile))
                    with self.assertRaises(ValueError):
                        logger.resolve_can_config({"profile": path})


@unittest.skipIf(can is None or cantools is None, "CAN dependencies not installed")
class CanCaptureTests(unittest.TestCase):
    def setUp(self):
        logger.stop_requested.clear()
        self.database = cantools.database.load_string('''VERSION ""
NS_ :
BS_:
BU_: ECU
BO_ 291 Drive: 8 ECU
 SG_ Speed : 0|16@1+ (0.1,0) [0|6553.5] "km/h" Vector__XXX
''', database_format="dbc")

    def test_dbc_scaling_and_frame_types(self):
        msg = can.Message(arbitration_id=291, data=self.database.encode_message(291, {"Speed": 123.4}), is_extended_id=False)
        rows = logger.decode_can_database(self.database, msg)
        self.assertEqual(rows[0][1], 1234)
        self.assertAlmostEqual(rows[0][2], 123.4)
        for key, value in (("is_remote_frame", True), ("is_error_frame", True),
                           ("is_extended_id", True), ("arbitration_id", 999)):
            with self.subTest(key=key):
                other = can.Message(arbitration_id=291, data=msg.data, is_extended_id=False)
                setattr(other, key, value)
                self.assertEqual(logger.decode_can_database(self.database, other), [])

    def test_capture_continues_after_bad_dbc_payload(self):
        event = threading.Event()
        good = can.Message(arbitration_id=291, data=self.database.encode_message(291, {"Speed": 12.3}), is_extended_id=False)
        frames = iter([can.Message(arbitration_id=291, data=b"", is_extended_id=False), good])
        bus = mock.Mock()
        def receive(timeout):
            try:
                return next(frames)
            except StopIteration:
                event.set()
                return None
        bus.recv.side_effect = receive
        with temporary_directory() as directory:
            with mock.patch.object(logger, "can", can), mock.patch.object(can, "Bus", return_value=bus) as factory, mock.patch.object(logger, "load_can_database", return_value=self.database):
                logger.run_can_logger({"enabled": True, "interface": "virtual", "channel": "test", "dbc": "vehicle.dbc"}, Path(directory), "test", time.monotonic(), event)
                factory.assert_called_once_with(interface="virtual", channel="test", ignore_config=True)
            with (Path(directory) / "test_MOTEC_RAW_CAN.csv").open() as stream:
                self.assertEqual(len(list(csv.DictReader(stream))), 2)
            with (Path(directory) / "test_MOTEC_CHANNELS.csv").open() as stream:
                self.assertAlmostEqual(float(list(csv.DictReader(stream))[0]["Value"]), 12.3)
            self.assertEqual(logger.latest_packet["can"]["decode_errors"], 1)
            bus.shutdown.assert_called_once()

    def test_virtual_bus_capture_with_dbc_file(self):
        event = threading.Event()
        channel = uuid.uuid4().hex
        with temporary_directory() as directory:
            dbc_path = directory / "vehicle.dbc"
            cantools.database.dump_file(self.database, str(dbc_path))
            config = {"enabled": True, "interface": "virtual", "channel": channel, "dbc": dbc_path}
            real_factory = can.Bus
            ready = threading.Event()
            def open_bus(**kwargs):
                bus = real_factory(**kwargs)
                ready.set()
                return bus
            thread = threading.Thread(target=logger.run_can_logger,
                                      args=(config, directory, "virtual", time.monotonic(), event))
            with mock.patch.object(logger, "can", can), mock.patch.object(can, "Bus", side_effect=open_bus):
                thread.start()
                try:
                    self.assertTrue(ready.wait(3), "receiver did not open")
                    with real_factory(interface="virtual", channel=channel, ignore_config=True) as sender:
                        sender.send(can.Message(arbitration_id=291, is_extended_id=False,
                                               data=self.database.encode_message(291, {"Speed": 45.6})))
                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline:
                        with logger.state_lock:
                            state = dict(logger.latest_packet["can"])
                        if state.get("channel") == channel and state.get("decoded_frames") == 1:
                            break
                        time.sleep(0.01)
                    else:
                        self.fail("virtual frame was not decoded")
                finally:
                    event.set()
                    thread.join(3)
            self.assertFalse(thread.is_alive())
            with (directory / "virtual_MOTEC_CHANNELS.csv").open() as stream:
                self.assertAlmostEqual(float(list(csv.DictReader(stream))[0]["Value"]), 45.6)

    def test_multiplexed_dbc_uses_only_active_signals(self):
        database = cantools.database.load_string('''VERSION ""
NS_ :
BS_:
BU_: ECU
BO_ 512 Mux: 8 ECU
 SG_ Mode M : 0|8@1+ (1,0) [0|255] "" Vector__XXX
 SG_ A m1 : 8|16@1+ (0.5,-10) [0|100] "unit" Vector__XXX
 SG_ B m2 : 8|16@1+ (1,0) [0|100] "unit" Vector__XXX
''', database_format="dbc")
        msg = can.Message(arbitration_id=512, is_extended_id=False,
                          data=database.encode_message(512, {"Mode": 1, "A": 5}))
        rows = logger.decode_can_database(database, msg)
        self.assertEqual([row[0]["channel"] for row in rows], ["Mode", "A"])
        self.assertEqual(rows[1][1:], (30, 5))


if __name__ == "__main__":
    unittest.main()
