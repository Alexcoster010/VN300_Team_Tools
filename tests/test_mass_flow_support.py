"""SFM3505 Pi I2C, capture, and HTTP integration without sensor hardware."""
import csv
import json
import os
import sys
import tempfile
import types
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from test_vn300_button_logger import logger
import app
import mass_flow

FIXED_PACKET = bytes((0x12, 0x34, 0x37, 0x56, 0xAB, 0x2A, 0xCD, 0xEF, 0x84))
ZERO_PACKET = bytes((0x80, 0x00, 0xA2, 0x00, 0x80, 0xFB, 0x00, 0x00, 0x81))


def packet(air=0, oxygen=0):
    values = b''.join(round(8388608 + 25600 * flow).to_bytes(3, 'big') for flow in (air, oxygen))
    return b''.join(values[index:index + 2] + bytes((mass_flow.crc8(values[index:index + 2]),))
                    for index in (0, 2, 4))


class MassFlowTests(unittest.TestCase):
    def test_crc_reference_packet_layout_and_conversion(self):
        self.assertEqual(mass_flow.crc8(bytes((0xBE, 0xEF))), 0x92)
        decoded = mass_flow.decode_packet(FIXED_PACKET)
        self.assertEqual((decoded['air_raw'], decoded['o2_raw']), (0x123456, 0xABCDEF))
        self.assertEqual(mass_flow.decode_packet(ZERO_PACKET)['air_slm'], 0)
        for air, oxygen, flag in ((-150, 300, 'in_range'), (-1, 1, 'in_range'),
                                  (300.1, -150.1, 'outside_range')):
            with self.subTest(air=air, oxygen=oxygen):
                result = mass_flow.decode_packet(packet(air, oxygen))
                self.assertAlmostEqual(result['air_slm'], air)
                self.assertAlmostEqual(result['o2_slm'], oxygen)
                self.assertEqual(result['range'], flag)

    def test_corruption_and_short_reads_clear_all_values(self):
        reader = mass_flow.MassFlowReader()
        for index in range(9):
            reader.accept_packet(ZERO_PACKET, 10)
            bad = bytearray(FIXED_PACKET)
            bad[index] ^= 1
            self.assertFalse(reader.accept_packet(bad, 11))
            state = reader.snapshot()
            self.assertEqual(state['status'], 'crc_error')
            self.assertTrue(all(state[key] is None for key in ('air_raw', 'o2_raw', 'air_slm', 'o2_slm')))
        for size in (0, 8, 10):
            self.assertFalse(reader.accept_packet(bytes(size)))
            self.assertEqual(reader.snapshot()['status'], 'i2c_error')
        self.assertEqual(reader.snapshot()['sensor_errors'], 12)

    def test_warmup_stale_age_and_arduino_independence(self):
        reader = mass_flow.MassFlowReader()
        reader.report_status('warming_up')
        self.assertIsNone(reader.snapshot()['last_age_s'])
        self.assertEqual(reader.snapshot()['sensor_errors'], 0)
        reader.accept_packet(packet(10, 20), 10)
        arduino = logger.SteeringReader('/dev/test')
        arduino.accept_line(b'315')
        arduino.state['status'] = 'disconnected'
        with mock.patch.object(mass_flow.time, 'monotonic', return_value=11):
            self.assertEqual(reader.snapshot()['status'], 'online')
            self.assertEqual(reader.snapshot()['last_age_s'], 1)
        with mock.patch.object(mass_flow.time, 'monotonic', return_value=13):
            self.assertEqual(reader.snapshot()['status'], 'stale')
        self.assertNotIn('mass_flow', arduino.snapshot())
        reader.report_status('i2c_error', 'NACK', 14)
        self.assertIsNone(reader.snapshot()['air_slm'])
        reader.accept_packet(ZERO_PACKET)
        self.assertEqual(reader.snapshot()['i2c_error'], '')

    def test_csv_run_boundaries_fault_rows_and_counts(self):
        reader = mass_flow.MassFlowReader()
        reader.accept_packet(packet(2, 3), 9)
        with tempfile.TemporaryDirectory() as directory:
            reader.begin_recording(directory, 'RUN001', 10)
            reader.accept_packet(ZERO_PACKET, 9.5)  # queued before the run
            reader.accept_packet(packet(-150, 300), 10.5)
            reader.report_status('crc_error', 'bad CRC', 11)
            reader.accept_packet(packet(300.1, -150.1), 11.5)
            state = reader.end_recording()
            reader.accept_packet(ZERO_PACKET, 12)
            with (Path(directory) / 'RUN001_MASS_FLOW.csv').open() as source:
                rows = list(csv.DictReader(source))
            self.assertEqual(len(rows), 3)
            self.assertEqual(list(rows[0]), ['Pi_Logger_Elapsed_Time_s', 'System_Time_ISO', *mass_flow.FLOW_COLUMNS])
            self.assertEqual(float(rows[0]['Pi_Logger_Elapsed_Time_s']), 0.5)
            self.assertEqual(float(rows[0]['Mass_Flow_Air_slm']), -150)
            self.assertEqual(float(rows[0]['Mass_Flow_O2_slm']), 300)
            self.assertEqual(rows[1]['Mass_Flow_Status'], 'crc_error')
            self.assertTrue(all(rows[1][field] == '' for field in mass_flow.FLOW_COLUMNS[1:5]))
            self.assertEqual(rows[2]['Mass_Flow_Range'], 'outside_range')
            self.assertEqual(state['recorded_samples'], 2)
            reader.begin_recording(directory, 'RUN002', 13)
            self.assertEqual(reader.snapshot()['recorded_samples'], 0)
            reader.accept_packet(packet(7, 8), 13.5)
            self.assertEqual(reader.end_recording()['recorded_samples'], 1)

    def test_disk_errors_do_not_stop_live_sensor_updates(self):
        reader = mass_flow.MassFlowReader()
        with tempfile.TemporaryDirectory() as directory:
            reader.begin_recording(directory, 'RUN001', 0)
            reader.writer = mock.Mock()
            reader.writer.writerow.side_effect = OSError('disk full')
            reader.accept_packet(ZERO_PACKET)
            state = reader.snapshot()
            self.assertFalse(state['recording'])
            self.assertEqual(state['write_error'], 'disk full')
            self.assertEqual(state['recorded_samples'], 0)
            reader.accept_packet(packet(3, 4))
            self.assertEqual(reader.snapshot()['air_slm'], 3)
            reader.begin_recording(directory, 'RUN002', 0)
            self.assertEqual(reader.snapshot()['write_error'], '')
            reader.close()
        with mock.patch.object(Path, 'open', side_effect=OSError('permission denied')):
            reader.begin_recording('/tmp', 'RUN003', 0)
        self.assertIn('permission denied', reader.snapshot()['write_error'])

    def test_raw_i2c_commands_warmup_and_recovery(self):
        reader = mass_flow.MassFlowReader()
        commands, waits, buses = [], [], []
        corrupt = bytearray(ZERO_PACKET)
        corrupt[5] ^= 1
        replies = iter((OSError('NACK'), bytes(corrupt), FIXED_PACKET))
        clock = [0.0]
        done = [False]

        class Message:
            @staticmethod
            def write(address, data):
                return types.SimpleNamespace(address=address, data=bytes(data))

            @staticmethod
            def read(address, count):
                self.assertEqual((address, count), (0x2E, 9))
                return bytearray(count)

        class Bus:
            def __init__(self, number):
                self.number = number
                self.closed = False
                buses.append(self)

            def i2c_rdwr(self, message):
                if hasattr(message, 'address'):
                    commands.append((message.address, message.data))
                else:
                    reply = next(replies)
                    if isinstance(reply, Exception):
                        raise reply
                    message[:] = reply
                    if reply == FIXED_PACKET:
                        done[0] = True

            def close(self):
                self.closed = True

        def wait(seconds):
            waits.append(seconds)
            clock[0] += seconds
            return done[0]

        event = mock.Mock()
        event.is_set.side_effect = lambda: done[0]
        event.wait.side_effect = wait
        reader.stop_event = event
        module = types.SimpleNamespace(SMBus=Bus, i2c_msg=Message)
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(sys.modules, smbus2=module), \
             mock.patch.object(mass_flow.time, 'monotonic', side_effect=lambda: clock[0]):
            reader.begin_recording(directory, 'RUN001', 0)
            reader._read_loop()
            state = reader.end_recording()
            with (Path(directory) / 'RUN001_MASS_FLOW.csv').open() as source:
                rows = list(csv.DictReader(source))
        self.assertEqual(state['samples'], 1)
        self.assertEqual(state['sensor_errors'], 2)
        self.assertEqual(state['reconnects'], 2)
        self.assertEqual(state['status'], 'stopped')
        self.assertEqual([bus.number for bus in buses], [1, 1, 1])
        self.assertTrue(all(bus.closed for bus in buses))
        self.assertEqual(commands, [(0x2E, data) for _ in range(3)
                                    for data in (b'\x3f\xf9', b'\x36\x03', b'\x3f\xf9')])
        self.assertEqual(waits.count(0.001), 3)
        self.assertEqual(waits.count(0.050), 3)
        self.assertEqual(waits.count(1.0), 2)
        self.assertEqual([row['Mass_Flow_Status'] for row in rows],
                         ['warming_up', 'i2c_error', 'warming_up', 'crc_error', 'warming_up', 'ok', 'stopped'])
        self.assertEqual(rows[-2]['Mass_Flow_Air_Raw'], str(0x123456))

    def test_unavailable_bus_retries_and_missing_dependency_is_visible(self):
        reader = mass_flow.MassFlowReader()
        reader.stop_event = mock.Mock()
        reader.stop_event.is_set.side_effect = (False, False, True)
        with mock.patch.dict(sys.modules, smbus2=types.SimpleNamespace(
                SMBus=mock.Mock(side_effect=PermissionError('access denied')), i2c_msg=object)):
            reader._read_loop()
        self.assertEqual(reader.snapshot()['sensor_errors'], 1)
        self.assertEqual(reader.snapshot()['reconnects'], 1)
        reader = mass_flow.MassFlowReader()
        with mock.patch.dict(sys.modules, smbus2=None):
            reader._read_loop()
        self.assertEqual(reader.snapshot()['status'], 'disconnected')
        self.assertIn('Install smbus2', reader.snapshot()['i2c_error'])

    def test_session_closes_flow_recording_when_vn300_fails(self):
        reader = mass_flow.MassFlowReader()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(logger.serial, 'Serial', side_effect=OSError('VN300 missing')):
                with self.assertRaises(OSError):
                    logger.run_session('/dev/test-vn300', 921600, root, root, mass_flow=reader)
            self.assertFalse(reader.snapshot()['recording'])
            self.assertIsNone(reader.file)
            self.assertEqual(len(list(root.glob('*_MASS_FLOW.csv'))), 1)
            metadata = json.loads(next(root.glob('*_session_metadata.json')).read_text())
            self.assertEqual(metadata['mass_flow']['transport'], 'pi-i2c')
            self.assertEqual(metadata['mass_flow']['i2c_bus'], 1)
            self.assertFalse(metadata['mass_flow']['recording'])

    def test_settings_validation_and_saved_uno_migration(self):
        for bus, hz in ((-1, 50), (True, 50), (1.5, 50), (1, 0), (1, 1001), (1, float('nan'))):
            with self.subTest(bus=bus, hz=hz), self.assertRaises(ValueError):
                mass_flow.MassFlowReader(bus, hz)
        for arguments in (['--arduino-format=a0-a1-sfm3505'], ['--arduino-format', 'a0-a1-sfm3505']):
            args = logger.settings_parser().parse_args(logger.migrate_sensor_arguments(arguments))
            self.assertEqual(args.arduino_format, 'a0-a1-volts')
            self.assertTrue(args.mass_flow_enable)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            path.write_text(json.dumps({'arduino_format': 'a0-a1-sfm3505'}))
            settings = logger.load_settings(path, logger.settings_parser())
            self.assertEqual(settings, {'arduino_format': 'a0-a1-volts', 'mass_flow_enable': True})
            path.write_text(json.dumps({'arduino_format': 'a0-a1-sfm3505', 'mass_flow_enable': False}))
            self.assertFalse(logger.load_settings(path, logger.settings_parser())['mass_flow_enable'])


class MassFlowHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.patch = mock.patch.multiple(
            logger, LOCAL_FALLBACK=self.root / 'logs', active_base_log_dir=self.root / 'logs',
            active_log_dir=self.root / 'logs' / 'boot', web_settings_path=self.root / 'config' / 'settings.json',
            web_effective_settings={}, steering_reader=None, mass_flow_reader=None)
        self.patch.start()
        self.env = mock.patch.dict(os.environ, SRT_DATA_DIR=str(self.root / 'runtime'))
        self.env.start()
        logger.active_log_dir.mkdir(parents=True)
        logger.logging_requested.clear()
        logger.recording_busy.clear()
        self.server = app.start_dashboard('127.0.0.1', 0)
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        logger.logging_requested.clear()
        logger.recording_busy.clear()
        self.server.shutdown()
        self.server.server_close()
        self.env.stop()
        self.patch.stop()
        self.temp.cleanup()

    def request(self, route, data=None):
        request = urllib.request.Request(self.base + route, data=None if data is None else json.dumps(data).encode(),
                                         headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)

    def test_api_flow_without_arduino_and_with_usb_failure(self):
        self.assertFalse(self.request('/api/latest')['mass_flow']['enabled'])
        logger.mass_flow_reader = mass_flow.MassFlowReader()
        logger.mass_flow_reader.accept_packet(packet(10, 20))
        data = self.request('/api/latest')
        self.assertFalse(data['steering']['enabled'])
        self.assertEqual(data['mass_flow']['air_slm'], 10)
        self.assertEqual(data['mass_flow']['o2_slm'], 20)
        self.assertEqual(data['mass_flow']['status'], 'online')
        logger.steering_reader = logger.SteeringReader('/dev/test')
        logger.steering_reader.state['status'] = 'disconnected'
        self.assertEqual(self.request('/api/latest')['mass_flow']['status'], 'online')
        logger.mass_flow_reader.report_status('i2c_error', 'NACK')
        data = self.request('/api/latest')
        self.assertEqual(data['mass_flow']['status'], 'i2c_error')
        self.assertIsNone(data['mass_flow']['air_slm'])

    def test_settings_flow_has_no_serial_or_calibration_requirement(self):
        settings = self.request('/api/settings')
        fields = {field['name']: field for field in settings['fields']}
        self.assertIn('mass_flow_enable', fields)
        self.assertEqual(fields['arduino_format']['choices'], ['steering-raw', 'a0-a1-volts'])
        body = {'mass_flow_enable': True, 'mass_flow_i2c_bus': 1, 'mass_flow_sample_hz': 50}
        self.assertTrue(self.request('/api/settings', body)['ok'])
        for invalid in ({'mass_flow_i2c_bus': -1}, {'mass_flow_i2c_bus': 1.5}, {'mass_flow_sample_hz': 0},
                        {'mass_flow_sample_hz': 1001}, {'mass_flow_enable': 'true'}):
            with self.subTest(invalid=invalid), self.assertRaises(urllib.error.HTTPError) as error:
                self.request('/api/settings', invalid)
            self.assertEqual(error.exception.code, 400)
        self.assertEqual(logger.load_settings(logger.web_settings_path, logger.settings_parser()), body)

    def test_guide_and_run_catalog_include_pi_flow(self):
        documents = self.request('/api/history')['documents']
        guide = next(document for document in documents if document['path'] == 'docs/SFM3505_SETUP.md')
        self.assertFalse(guide['historical'])
        content = self.request('/api/history?id=' + guide['id'])
        self.assertIn('Raspberry Pi I2C', content['text'])
        for suffix in ('BINARY', 'MASS_FLOW'):
            (logger.active_log_dir / f'RUN001_{suffix}.csv').write_text('Pi_Logger_Elapsed_Time_s\n0\n')
        catalog = self.request('/api/sessions')['sessions']
        run = next(session for session in catalog if session['name'] == 'RUN001')
        self.assertEqual(len(run['files']), 2)


if __name__ == '__main__':
    unittest.main()
