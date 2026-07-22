import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from vn300_gg_lap_predictor import (
    G_MPS2,
    EnvelopePolygon,
    build_car_envelope,
    select_consistent_lap_records,
    solve_minimum_lap,
)


def circular_envelope(radius: float = 1.0) -> list[dict]:
    rows = []
    for center in range(5, 360, 10):
        angle = math.radians(center)
        rows.append({
            "angle_center_deg": center,
            "lateral_g": radius * math.cos(angle),
            "longitudinal_g": radius * math.sin(angle),
        })
    return rows


class CarEnvelopeTests(unittest.TestCase):
    def test_car_envelope_selects_best_driver_per_direction(self):
        records = []
        for driver, radius in (("A", 1.0), ("B", 1.2)):
            samples = []
            for center in range(5, 360, 10):
                angle = math.radians(center)
                for _ in range(12):
                    samples.append(SimpleNamespace(
                        lateral_g=radius * math.cos(angle),
                        longitudinal_g=radius * math.sin(angle),
                        speed_mph=25.0,
                        pos_uncertainty_m=1.0,
                    ))
            records.append({"driver": driver, "samples": samples})

        car, drivers = build_car_envelope(records, 0.98, 10, 12, 5.0, 60.0, 4.0)

        self.assertEqual(len(car), 36)
        self.assertEqual(len(drivers), 72)
        self.assertEqual({row["source_driver"] for row in car}, {"B"})
        self.assertTrue(all(abs(row["car_p_combined_g"] - 1.2) < 1e-12 for row in car))

    def test_constant_radius_course_matches_lateral_limit_solution(self):
        radius_m = 20.0
        course_distance_m = 2.0 * math.pi * radius_m
        points = 500
        ds_m = course_distance_m / points
        reference = {
            "curvature_1pm": [1.0 / radius_m] * points,
            "ds_m": ds_m,
            "distance_m": course_distance_m,
        }
        envelope = circular_envelope()
        solution = solve_minimum_lap(reference, envelope, [], 100.0)
        _, left_limit_g = EnvelopePolygon(envelope).pure_lateral_limits()
        expected_speed_mps = math.sqrt(left_limit_g * G_MPS2 * radius_m)
        expected_time_s = course_distance_m / expected_speed_mps

        self.assertAlmostEqual(solution["lap_time_s"], expected_time_s, places=8)
        self.assertLessEqual(solution["iterations"], 3)

    def test_course_distance_cluster_rejects_short_false_split(self):
        def samples(distance_m, duration_s=34.0, count=101):
            speed_mps = distance_m / duration_s
            return [
                SimpleNamespace(
                    t=index * duration_s / (count - 1),
                    speed_mps=speed_mps,
                    speed_mph=speed_mps * 2.2369362921,
                )
                for index in range(count)
            ]

        records = [
            {"name": "lap_1", "samples": samples(348.0)},
            {"name": "lap_2", "samples": samples(352.0)},
            {"name": "lap_3", "samples": samples(345.0)},
            {"name": "false_split", "samples": samples(205.0, 26.0)},
        ]

        accepted, center = select_consistent_lap_records(records, 60.0)

        self.assertEqual({row["name"] for row in accepted}, {"lap_1", "lap_2", "lap_3"})
        self.assertAlmostEqual(center, 348.0, places=6)


if __name__ == "__main__":
    unittest.main()
