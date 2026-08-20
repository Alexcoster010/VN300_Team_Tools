import tempfile
import unittest
from pathlib import Path
from unittest import mock

import vn300_team_app as app


class TeamAppTests(unittest.TestCase):
    def test_normalize_pi_url(self):
        self.assertEqual(app.normalize_pi_url("192.168.1.25"), "http://192.168.1.25:8080/")
        self.assertEqual(app.normalize_pi_url("raspberrypi.local:8080"), "http://raspberrypi.local:8080/")
        self.assertEqual(app.normalize_pi_url("http://raspberrypi.local"), "http://raspberrypi.local:8080/")
        self.assertEqual(app.normalize_pi_url("https://10.0.0.4:8080/live"), "https://10.0.0.4:8080/live/")
        with self.assertRaises(ValueError):
            app.normalize_pi_url("file:///tmp/dashboard")

    def test_path_containment_rejects_sibling(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "output"
            root.mkdir()
            self.assertTrue(app.path_is_within(root / "report.html", root))
            self.assertFalse(app.path_is_within(root / ".." / "secret.csv", root))

    def test_build_analysis_command_maps_ui_options(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            output = root / "output"
            data.mkdir()
            output.mkdir()
            command, settings = app.build_analysis_command({
                "input_path": str(data),
                "mode": "lap",
                "driver_order": "Alex C,Jimmy",
                "driver_order_offset": 1,
                "auto_sectors": 4,
                "sector_report_min_seconds": 18.5,
                "gg_enabled": False,
                "include_ascii": True,
            }, output)
            self.assertIn("--no-prompts", command)
            self.assertIn("--mode", command)
            self.assertIn("lap", command)
            self.assertIn("--driver-order", command)
            self.assertIn("--no-gg-lap-prediction", command)
            self.assertIn("--include-ascii", command)
            self.assertEqual(settings["auto_sectors"], 4)

    def test_frozen_build_launches_bundled_analyzer_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            output = root / "output"
            data.mkdir()
            output.mkdir()
            analyzer = root / "VN300Analyzer.exe"
            with mock.patch.object(app, "IS_FROZEN", True), mock.patch.object(app, "ANALYZER_PATH", analyzer):
                command, _ = app.build_analysis_command({"input_path": str(data)}, output)
            self.assertEqual(command[0], str(analyzer))
            self.assertEqual(command[1], str(data.resolve()))

    def test_result_files_prioritizes_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "summary.csv").write_text("value\n1\n", encoding="utf-8")
            (output / "report.html").write_text("<h1>Report</h1>", encoding="utf-8")
            (output / "custom.csv").write_text("value\n2\n", encoding="utf-8")
            results = app.result_files(output)
            self.assertEqual(results[0]["name"], "report.html")
            self.assertEqual({item["name"] for item in results}, {"report.html", "summary.csv", "custom.csv"})

    def test_state_store_persists_settings_and_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            store = app.StateStore(path)
            store.update_settings({"pi_url": "http://10.0.0.2:8080/"})
            store.add_history({"id": "run1", "status": "completed"})
            reloaded = app.StateStore(path).snapshot()
            self.assertEqual(reloaded["settings"]["pi_url"], "http://10.0.0.2:8080/")
            self.assertEqual(reloaded["history"][0]["id"], "run1")


if __name__ == "__main__":
    unittest.main()
