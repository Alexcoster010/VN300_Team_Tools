"""Hardware-independent CAN profile, DBC and receive-loop regression checks."""
import csv
import json
import shutil
import uuid
from contextlib import contextmanager
import threading
import time
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest import mock

from test_vn300_button_logger import logger


@contextmanager
def temporary_directory():
    root = Path(__file__).resolve().parents[1] / "archive" / "build" / "test_can"
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


class CanReferenceTests(unittest.TestCase):
    def setUp(self):
        self.mapping = logger.load_can_signal_map(logger.DEFAULT_CAN_SIGNAL_MAP)
        logger.reset_can_live()

    def frame(self, can_id, data, **flags):
        return SimpleNamespace(arbitration_id=can_id, data=bytes(data), dlc=len(data), is_extended_id=False, **flags)

    def decode(self, msg):
        return {spec['channel']: logger.decode_can_signal(msg.data, spec)
                for spec in logger.matching_can_specs(self.mapping, msg)}

    def test_complete_reference_and_full_channel_names(self):
        self.assertEqual(len(self.mapping), 50)
        self.assertEqual(sum(map(len, self.mapping.values())), 273)
        for specs in self.mapping.values():
            self.assertEqual(len({s['channel'] for s in specs}), len(specs))
            self.assertTrue(all(s['start_bit'] + s['length_bits'] <= (32 if s['can_id'] == 0x777 else 64) for s in specs))
        # Both long channels previously collided after filename truncation.
        rows = self.decode(self.frame(0x116, bytes.fromhex('03E8 07D0 0064 FFFF')))
        self.assertAlmostEqual(rows['Throttle_Servo_Bank_1_Position_Sensor_Main_Voltage'][1], 1)
        self.assertAlmostEqual(rows['Throttle_Servo_Bank_1_Position_Sensor_Tracking_Voltage'][1], 2)
        self.assertEqual(rows['Throttle_Servo_Bank_1_Diagnostic'][0], -1)

    def test_signed_nibbles_flags_and_temperature_offset(self):
        rows = self.decode(self.frame(0x64D, bytes.fromhex('1200000000000F00')))
        self.assertEqual(rows['Engine_State'][0], 1)
        self.assertEqual(rows['Fuel_Pump_State'][0], 2)
        self.assertEqual(rows['Gear'][0], -1)
        rows = self.decode(self.frame(0x650, bytes.fromhex('0000000000000081')))
        self.assertEqual(rows['Driver_Switch_1'][0], 1)
        self.assertEqual(rows['Driver_Switch_2'][0], 0)
        self.assertEqual(rows['Driver_Switch_8'][0], 1)
        rows = self.decode(self.frame(0x649, bytes.fromhex('648C28002D780001')))
        self.assertEqual(rows['Coolant_Temperature'][1], 60)
        self.assertAlmostEqual(rows['ECU_Battery_Voltage'][1], 12)

    def test_all_vcs_compounds_and_short_payload(self):
        expected = [4, 4, 4, 4, 7]
        for mux, count in enumerate(expected):
            rows = self.decode(self.frame(0x1F, [mux, 0, 0, 100, 0, 200, 0, 10]))
            self.assertEqual(len(rows), count)
            self.assertEqual(rows['VCS_Compound'][0], mux)
            if mux == 1:
                self.assertIn('VCS_Throttle_Position', rows)
                self.assertNotIn('VCS_Vehicle_Speed', rows)
        rows = self.decode(self.frame(0x1F, [255, 0, 0, 100, 0, 200, 0, 10]))
        self.assertEqual(list(rows), ['VCS_Compound'])
        with self.assertRaises(ValueError):
            self.decode(self.frame(0x1F, []))
        self.assertAlmostEqual(self.decode(self.frame(0x777, bytes.fromhex('0000D610')))['Fuel_Used'][1], 54.8)

    def test_wrong_frame_types_are_raw_only(self):
        for flag in ('is_remote_frame', 'is_error_frame', 'is_fd'):
            self.assertEqual(self.decode(self.frame(0x640, bytes(8), **{flag: True})), {})
        msg = self.frame(0x640, bytes(8))
        msg.is_extended_id = True
        self.assertEqual(self.decode(msg), {})
        self.assertEqual(self.decode(self.frame(0x021, bytes(8))), {})

    def test_legacy_maps_and_header_only_upgrade(self):
        with temporary_directory() as directory:
            path = directory / 'map.csv'
            path.write_text('channel,can_id,start_bit,length_bits,byte_order,signed,scale,offset,unit\nLong_Existing_Custom_Channel_Name,0x12345,0,16,little,false,0.5,1,unit\n')
            mapping = logger.load_can_signal_map(path)
            msg = self.frame(0x12345, bytes.fromhex('0400'), is_fd=True)
            msg.is_extended_id = True
            specs = logger.matching_can_specs(mapping, msg)
            self.assertEqual(len(specs), 1)
            self.assertEqual(logger.decode_can_signal(msg.data, specs[0]), (4, 3))
            path.write_text('channel,can_id\n')
            self.assertEqual(logger.load_can_signal_map(path), {})
            with mock.patch.object(logger, 'DEFAULT_CAN_SIGNAL_MAP', path):
                self.assertEqual(len(logger.load_can_signal_map(path)), 50)

    def test_bandwidth_uses_all_received_frames_and_expires(self):
        logger.update_can_status(capturing=True, bitrate=1000000)
        msg = self.frame(0x123, bytes(8))
        self.assertEqual(logger.estimate_can_frame_bits(msg), 131)
        extended = self.frame(0x123, bytes(8))
        extended.is_extended_id = True
        self.assertEqual(logger.estimate_can_frame_bits(extended), 155)
        self.assertEqual(logger.estimate_can_frame_bits(self.frame(0x123, bytes(8), is_remote_frame=True)), 54)
        with mock.patch.object(logger.time, 'monotonic', return_value=10):
            logger.reset_can_live()
            for _ in range(100):
                logger.update_can_live(msg, [])
        with mock.patch.object(logger.time, 'monotonic', return_value=11):
            bandwidth = logger.public_can_snapshot()['bandwidth']
            self.assertAlmostEqual(bandwidth['percent'], 1.31)
            self.assertEqual(bandwidth['frames_per_s'], 100)
        with mock.patch.object(logger.time, 'monotonic', return_value=16):
            self.assertEqual(logger.public_can_snapshot()['bandwidth']['percent'], 0)
        logger.update_can_status(bitrate=0)
        self.assertIsNone(logger.public_can_snapshot()['bandwidth']['percent'])
        logger.update_can_status(bitrate=500000)
        logger.update_can_live(self.frame(0x123, bytes(8), is_fd=True), [])
        self.assertIsNone(logger.public_can_snapshot()['bandwidth']['percent'])
        logger.update_can_status(capturing=False)
        self.assertEqual(logger.public_can_snapshot()['bandwidth']['reason'], 'Capture inactive')

    def test_bitrate_from_existing_socketcan_interface(self):
        result = SimpleNamespace(stdout=json.dumps([{'linkinfo': {'info_data': {'bittiming': {'bitrate': 500000}}}}]))
        with mock.patch.object(logger.subprocess, 'run', return_value=result):
            self.assertEqual(logger.can_effective_bitrate({'interface': 'socketcan', 'channel': 'can0'}), 500000)
        self.assertEqual(logger.can_effective_bitrate({'interface': 'virtual'}), 0)


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
            snapshot = logger.public_can_snapshot()
            self.assertEqual(snapshot['frames'][0]['count'], 2)
            self.assertAlmostEqual(snapshot['signals'][0]['value'], 12.3)
            self.assertEqual(snapshot['signals'][0]['unit'], 'km/h')
            self.assertFalse(snapshot['status']['capturing'])
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
