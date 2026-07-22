import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import vn300_desktop_app as desktop


class SnapshotHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/api/latest":
            self.send_error(404)
            return
        body = json.dumps({"status": "idle", "logging": False, "fields": {"Speed_mph": 12.5}}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


class DesktopAppTests(unittest.TestCase):
    def test_normalize_pi_endpoint_adds_scheme_and_default_port(self):
        self.assertEqual(desktop.normalize_pi_endpoint("192.168.1.25"), "http://192.168.1.25:8080/")
        self.assertEqual(desktop.normalize_pi_endpoint("raspberrypi.local:9000"), "http://raspberrypi.local:9000/")
        self.assertEqual(desktop.normalize_pi_endpoint("https://10.0.0.4"), "https://10.0.0.4:8080/")

    def test_normalize_pi_endpoint_rejects_invalid_addresses(self):
        with self.assertRaises(ValueError):
            desktop.normalize_pi_endpoint("")
        with self.assertRaises(ValueError):
            desktop.normalize_pi_endpoint("file:///tmp/pi")

    def test_state_round_trip_saves_pi_endpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "desktop_state.json"
            state = desktop.load_state(path)
            state["pi_endpoint"] = "http://10.0.0.7:8080/"
            state["analysis"]["auto_sectors"] = 4
            desktop.save_state(state, path)
            loaded = desktop.load_state(path)
            self.assertEqual(loaded["pi_endpoint"], "http://10.0.0.7:8080/")
            self.assertEqual(loaded["analysis"]["auto_sectors"], 4)

    def test_fetch_pi_snapshot_reads_dashboard_api(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), SnapshotHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            payload = desktop.fetch_pi_snapshot(f"http://127.0.0.1:{server.server_port}/")
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(payload["status"], "idle")
        self.assertEqual(payload["fields"]["Speed_mph"], 12.5)

    def test_lap_time_format(self):
        self.assertEqual(desktop.format_lap_time(31.2478), "0:31.248")
        self.assertEqual(desktop.format_lap_time(61.0), "1:01.000")
        self.assertEqual(desktop.format_lap_time(None), "--:--.---")


if __name__ == "__main__":
    unittest.main()
