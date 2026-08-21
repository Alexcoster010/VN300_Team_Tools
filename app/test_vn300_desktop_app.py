import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import vn300_desktop_app as desktop


class SnapshotHandler(BaseHTTPRequestHandler):
    received_payload = None

    def do_GET(self):
        if self.path != "/api/latest":
            self.send_error(404)
            return
        body = json.dumps({
            "logger_version": "0.5.0",
            "status": "idle",
            "logging": False,
            "fields": {"Speed_mph": 12.5},
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/api/run_metadata":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        type(self).received_payload = json.loads(self.rfile.read(length).decode("utf-8"))
        body = json.dumps({
            "ok": True,
            "metadata": type(self).received_payload,
            "next_run_id": "VN300_2026-08-20_RUN002",
        }).encode("utf-8")
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
            state["pi_ssh_user"] = "vectornav"
            state["analysis"]["auto_sectors"] = 4
            state["custom_analysis"]["x_channel"] = "Distance_m"
            state["custom_presets"]["Braking"] = {"y_channels": ["Brake_Pressure_Front"]}
            desktop.save_state(state, path)
            loaded = desktop.load_state(path)
            self.assertEqual(loaded["pi_endpoint"], "http://10.0.0.7:8080/")
            self.assertEqual(loaded["pi_ssh_user"], "vectornav")
            self.assertEqual(loaded["analysis"]["auto_sectors"], 4)
            self.assertEqual(loaded["custom_analysis"]["x_channel"], "Distance_m")
            self.assertEqual(loaded["custom_presets"]["Braking"]["y_channels"], ["Brake_Pressure_Front"])

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
        self.assertEqual(payload["logger_version"], "0.5.0")
        self.assertEqual(payload["fields"]["Speed_mph"], 12.5)

    def test_pi_api_request_posts_json_without_proxy(self):
        SnapshotHandler.received_payload = None
        server = ThreadingHTTPServer(("127.0.0.1", 0), SnapshotHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            payload = desktop.pi_api_request(
                f"http://127.0.0.1:{server.server_port}/",
                "api/run_metadata",
                "POST",
                {"driver": "Alex C", "valid_run": "yes"},
            )
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(SnapshotHandler.received_payload, {"driver": "Alex C", "valid_run": "yes"})
        self.assertEqual(payload["next_run_id"], "VN300_2026-08-20_RUN002")

    def test_lap_time_format(self):
        self.assertEqual(desktop.format_lap_time(31.2478), "0:31.248")
        self.assertEqual(desktop.format_lap_time(61.0), "1:01.000")
        self.assertEqual(desktop.format_lap_time(None), "--:--.---")


if __name__ == "__main__":
    unittest.main()
