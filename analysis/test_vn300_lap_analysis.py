import math
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from vn300_lap_analysis import (
    credible_sector_rows,
    latest_dashboard_config,
    load_metadata_files,
    load_vnins,
    metadata_for_path,
    run_metadata_paths,
    unwrap_angle_rad,
)


class AngleUnwrapTests(unittest.TestCase):
    def test_large_corrupt_angle_is_normalized_in_one_step(self):
        previous = math.radians(12.0)
        result = unwrap_angle_rad(previous, 1.0e20)

        self.assertTrue(math.isfinite(result))
        self.assertLessEqual(abs(result - previous), math.pi)

    def test_normal_wrap_preserves_shortest_delta(self):
        previous = math.radians(179.0)
        result = unwrap_angle_rad(previous, math.radians(-179.0))

        self.assertAlmostEqual(math.degrees(result - previous), 2.0, places=9)


class SectorCredibilityTests(unittest.TestCase):
    def test_rejects_corrupt_tiny_sector(self):
        rows = [{
            "duration_s": 33.0,
            "distance_m": 350.0,
            "max_speed_mph": 40.0,
            "sector_1_s": 0.1,
            "sector_2_s": 16.0,
            "sector_3_s": 16.9,
        }]

        accepted, rejected = credible_sector_rows(rows)

        self.assertEqual(accepted, [])
        self.assertEqual(rejected[0]["reason"], "a sector is less than 5% of lap duration")

    def test_accepts_balanced_equal_distance_sectors(self):
        rows = [{
            "duration_s": 33.0,
            "distance_m": 350.0,
            "max_speed_mph": 40.0,
            "sector_1_s": 9.5,
            "sector_2_s": 12.0,
            "sector_3_s": 11.5,
        }]

        accepted, rejected = credible_sector_rows(rows)

        self.assertEqual(accepted, rows)
        self.assertEqual(rejected, [])


class DashboardConfigTests(unittest.TestCase):
    HEADER = (
        "Configured_At_Local,Mode,Start_Lat_1,Start_Lon_1,Start_Lat_2,Start_Lon_2,"
        "Finish_Lat_1,Finish_Lon_1,Finish_Lat_2,Finish_Lon_2,Min_Speed_mph,Min_Gap_s\n"
    )

    def test_recorded_timestamp_wins_over_copied_file_mtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / "old" / "VN300_dashboard_timing_config.csv"
            current = root / "current" / "VN300_dashboard_timing_config.csv"
            old.parent.mkdir()
            current.parent.mkdir()
            old.write_text(
                self.HEADER + "2026-07-14T19:56:15,lap,0,0,0,0,,,,,5,8\n",
                encoding="utf-8",
            )
            current.write_text(
                self.HEADER
                + "2026-07-15T02:40:19,lap,35.2066462,-97.4383937,35.2066553,-97.4382367,,,,,5,8\n",
                encoding="utf-8",
            )
            os.utime(old, (200.0, 200.0))
            os.utime(current, (100.0, 100.0))

            config = latest_dashboard_config([root])

            self.assertEqual(config["path"], current)
            self.assertEqual(
                config["start_line"],
                (35.2066462, -97.4383937, 35.2066553, -97.4382367),
            )

    def test_zero_length_line_is_not_used_for_timing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "VN300_dashboard_timing_config.csv"
            path.write_text(
                self.HEADER + "2026-07-14T19:56:15,lap,0,0,0,0,,,,,5,8\n",
                encoding="utf-8",
            )

            config = latest_dashboard_config([root])

            self.assertIsNone(config["start_line"])


class TelemetrySanitationTests(unittest.TestCase):
    def test_impossible_samples_do_not_destroy_valid_trace(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "VN300_2026-07-15_RUN001_BINARY.csv"
            path.write_text(
                "Pi_Elapsed_Time_s,Latitude_deg,Longitude_deg,Vel_N_mps,Vel_E_mps,Vel_D_mps,"
                "PosUncertainty_m,Yaw_deg,Longitudinal_G,Lateral_G\n"
                "0.00,35.0,-97.0,10,0,0,1,0,0.2,0.5\n"
                "0.01,5.0,120.0,1e38,0,0,1,1e30,1e30,-1e30\n"
                "0.02,35.000001,-97.0,10,0,0,1,1,0.3,0.6\n",
                encoding="utf-8",
            )

            samples = load_vnins(path)

            self.assertEqual(len(samples), 2)
            self.assertLess(samples[-1].dist_m, 1.0)
            self.assertLessEqual(max(sample.speed_mph for sample in samples), 25.0)


class MetadataDiscoveryTests(unittest.TestCase):
    HEADER = "session_file,run_id,run_number,driver,date\n"

    def test_all_selected_boot_metadata_files_are_merged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "boot_a"
            second = root / "boot_b"
            first.mkdir()
            second.mkdir()
            (first / "VN300_run_metadata.csv").write_text(
                self.HEADER + "VN300_RUN001,VN300_RUN001,1,Driver A,2026-07-14\n",
                encoding="utf-8",
            )
            (second / "VN300_run_metadata.csv").write_text(
                self.HEADER + "VN300_RUN002,VN300_RUN002,2,Driver B,2026-07-15\n",
                encoding="utf-8",
            )

            metadata, _ = load_metadata_files(run_metadata_paths([root]))

            self.assertEqual(
                metadata_for_path(metadata, first / "VN300_RUN001_BINARY.csv")["meta_driver"],
                "Driver A",
            )
            self.assertEqual(
                metadata_for_path(metadata, second / "VN300_RUN002_BINARY.csv")["meta_driver"],
                "Driver B",
            )


if __name__ == "__main__":
    unittest.main()
