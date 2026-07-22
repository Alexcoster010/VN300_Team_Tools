import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from vn300_lap_analysis import credible_sector_rows, unwrap_angle_rad


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


if __name__ == "__main__":
    unittest.main()
