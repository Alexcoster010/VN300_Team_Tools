import argparse
import json
import subprocess
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from test_vn300_button_logger import logger

ROOT = Path(__file__).resolve().parents[1]


class PiPackageTests(unittest.TestCase):
    def test_installer_preflight_accepts_complete_package(self):
        result = subprocess.run(['sh', str(ROOT / 'deploy/install.sh'), '--check'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('passed preflight checks', result.stdout)
        version = (ROOT / 'config/VERSION').read_text().strip()
        self.assertRegex(version, r'^\d+\.\d+\.\d+$')
        self.assertEqual(logger.LOGGER_VERSION, version)

    def test_service_runs_new_entrypoint_with_existing_install_location(self):
        service = (ROOT / 'deploy/telemetry.service').read_text()
        self.assertIn('User=vectornav', service)
        self.assertIn('/home/vectornav/vn300_tools/app.py', service)
        self.assertIn('Restart=always', service)

    def test_installer_generates_unquoted_working_directory_and_preserves_flags(self):
        installer = (ROOT / 'deploy/install.sh').read_text()
        marker = 'python3 - "$SCRIPT_DIR/telemetry.service" "$GENERATED_SERVICE" "$APP_USER" "$APP_DIR" "$BACKUP_DIR/$SERVICE" <<\'PY\'\n'
        generator = installer.split(marker, 1)[1].split('\nPY\n', 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'generated.service'
            previous = Path(directory) / 'previous.service'
            previous.write_text('ExecStart=/usr/bin/python3 /old/vn300_button_logger.py --baud 115200 --can-enable\n')
            result = subprocess.run(['python3', '-c', generator,
                                     str(ROOT / 'deploy/telemetry.service'), str(destination),
                                     'vectornav', '/home/vectornav/vn300_tools', str(previous)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            generated = destination.read_text()
            self.assertIn('\nWorkingDirectory=/home/vectornav/vn300_tools\n', generated)
            self.assertIn('"app.py"', generated.replace('/home/vectornav/vn300_tools/', ''))
            self.assertIn('"--baud" "115200" "--can-enable"', generated)

    def test_settings_paths_and_command_line_overrides(self):
        parser = argparse.ArgumentParser()
        parser.add_argument('--port')
        parser.add_argument('--baud', type=int)
        parser.add_argument('--steering-calibration', type=Path)
        parser.add_argument('--can-enable', action='store_true')
        parser.add_argument('--parse-mode', choices=('auto', 'binary', 'ascii'))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            path.write_text(json.dumps({'baud': 115200, 'port': '/dev/serial/by-id/test',
                                        'steering_calibration': 'steering.json', 'can_enable': False}))
            parser.set_defaults(**logger.load_settings(path, parser))
            args = parser.parse_args(['--baud', '921600'])
            self.assertEqual(args.baud, 921600)
            self.assertEqual(args.port, '/dev/serial/by-id/test')
            self.assertEqual(args.steering_calibration, (Path(directory) / 'steering.json').resolve())
            for invalid in ({'can_enable': 'false'}, {'baud': True}, {'unknown': 1},
                            {'parse_mode': 'invalid'}, []):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    path.write_text(json.dumps(invalid))
                    logger.load_settings(path, parser)

    def test_external_dashboard_assets_and_api_are_served(self):
        server = logger.start_dashboard('127.0.0.1', 0)
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            for route, filename, content_type in (
                ('/', 'index.html', 'text/html'),
                ('/index.html?check=1', 'index.html', 'text/html'),
                ('/app.js', 'app.js', 'text/javascript'),
                ('/sensors.js', 'sensors.js', 'text/javascript'),
                ('/trace.js', 'trace.js', 'text/javascript'),
                ('/style.css', 'style.css', 'text/css'),
            ):
                with self.subTest(route=route), urllib.request.urlopen(base + route) as response:
                    self.assertEqual(response.read(), (ROOT / 'web' / filename).read_bytes())
                    self.assertIn(content_type, response.headers['Content-Type'])
            with urllib.request.urlopen(base + '/api/latest') as response:
                self.assertEqual(json.load(response)['logger_version'], logger.LOGGER_VERSION)
            for route in ('/../logger.py', '/config/settings.json', '/logger.py'):
                with self.subTest(route=route), self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(base + route)
                self.assertEqual(error.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    unittest.main()
