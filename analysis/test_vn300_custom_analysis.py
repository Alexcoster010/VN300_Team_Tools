import csv
import json
import tempfile
import unittest
from pathlib import Path

from vn300_custom_analysis import (
    FormulaEvaluator,
    WorkspaceError,
    inspect_csv_source,
    load_csv_source,
    run_custom_analysis,
    scan_workspace_sources,
)


class CustomAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.root = Path(self.temporary.name)
        self.source = self.root / "run_01.csv"
        with self.source.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=("time_s", "speed_mph", "lateral_g", "driver"))
            writer.writeheader()
            for index in range(10):
                writer.writerow({
                    "time_s": index * 0.1,
                    "speed_mph": 20 + index,
                    "lateral_g": (index - 5) / 10,
                    "driver": "Alex",
                })

    def tearDown(self):
        self.temporary.cleanup()

    def test_inspection_finds_numeric_channels_and_ignores_text(self):
        source = inspect_csv_source(self.source)
        names = {channel["name"] for channel in source["channels"]}
        self.assertEqual(source["format"], "wide")
        self.assertIn("speed_mph", names)
        self.assertNotIn("driver", names)
        self.assertEqual(scan_workspace_sources(self.root)["sources"][0]["name"], "run_01.csv")

    def test_formula_evaluator_supports_channels_filters_and_vector_functions(self):
        columns = {"speed": [1.0, 2.0, 4.0], "time": [0.0, 1.0, 2.0]}
        evaluator = FormulaEvaluator(columns)
        self.assertEqual(evaluator.evaluate("[speed] * 2"), [2.0, 4.0, 8.0])
        self.assertEqual(evaluator.evaluate("[speed] >= 2 and [time] < 2"), [False, True, False])
        self.assertEqual(evaluator.evaluate("derivative([speed], [time])"), [None, 1.0, 2.0])
        self.assertEqual(evaluator.evaluate("smooth([speed], 2)"), [1.0, 1.5, 3.0])

    def test_formula_evaluator_rejects_python_access(self):
        with self.assertRaises(WorkspaceError):
            FormulaEvaluator({"speed": [1.0]}).evaluate("__import__('os').system('dir')")

    def test_custom_analysis_writes_report_data_and_configuration(self):
        output = self.root / "output"
        result = run_custom_analysis(
            {
                "title": "Speed Review",
                "source_paths": [str(self.source)],
                "x_channel": "time_s",
                "y_channels": ["speed_kph", "lateral_g"],
                "formulas": [{"name": "speed_kph", "unit": "km/h", "expression": "[speed_mph] * 1.609344"}],
                "filter": "[speed_mph] >= 25",
                "smoothing_points": 1,
                "max_plot_points": 5000,
                "plot_style": "line",
            },
            output,
        )
        self.assertEqual(result["trace_count"], 2)
        self.assertEqual(result["point_count"], 10)
        self.assertTrue((output / "custom_workspace.html").is_file())
        self.assertTrue((output / "custom_workspace_data.csv").is_file())
        config = json.loads((output / "custom_workspace_config.json").read_text(encoding="utf-8"))
        self.assertEqual(config["filter"], "[speed_mph] >= 25")

    def test_long_form_can_channels_are_discovered(self):
        path = self.root / "MOTEC_CHANNELS.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(("Pi_Logger_Elapsed_Time_s", "Channel", "Value", "Unit"))
            writer.writerow((0.1, "Throttle_Position", 45, "%"))
            writer.writerow((0.1, "Brake_Pressure_Front", 250, "psi"))
        source = inspect_csv_source(path)
        self.assertEqual(source["format"], "long")
        self.assertIn("Throttle_Position", {channel["name"] for channel in source["channels"]})

    def test_raw_vnins_components_create_standard_analysis_channels(self):
        path = self.root / "run_VNINS.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=("Pi_Elapsed_Time_s", "Vel_N_mps", "Vel_E_mps", "Yaw_deg", "Latitude_deg", "Longitude_deg"),
            )
            writer.writeheader()
            writer.writerow({"Pi_Elapsed_Time_s": 0, "Vel_N_mps": 3, "Vel_E_mps": 4, "Yaw_deg": 0, "Latitude_deg": 35, "Longitude_deg": -97})
            writer.writerow({"Pi_Elapsed_Time_s": 1, "Vel_N_mps": 6, "Vel_E_mps": 8, "Yaw_deg": 10, "Latitude_deg": 35.0001, "Longitude_deg": -97})
        inspected = {channel["name"] for channel in inspect_csv_source(path)["channels"]}
        self.assertTrue({"Vehicle_Speed_mph", "Distance_m", "Longitudinal_G", "Lateral_G"}.issubset(inspected))
        table = load_csv_source(path)
        self.assertAlmostEqual(table.columns["Vehicle_Speed_mps"][0], 5.0)
        self.assertGreater(table.columns["Distance_m"][1], 10.0)
        self.assertIsNotNone(table.columns["Lateral_G"][1])

    def test_formula_can_be_used_with_sources_that_have_different_channels(self):
        second = self.root / "run_02.csv"
        second.write_text("time_s,temperature_f\n0,80\n1,81\n", encoding="utf-8")
        result = run_custom_analysis(
            {
                "source_paths": [str(self.source), str(second)],
                "x_channel": "time_s",
                "y_channels": ["speed_kph"],
                "formulas": [{"name": "speed_kph", "unit": "km/h", "expression": "[speed_mph] * 1.609344"}],
            },
            self.root / "heterogeneous",
        )
        self.assertEqual(result["trace_count"], 1)
        self.assertTrue(any("no valid points" in warning for warning in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
