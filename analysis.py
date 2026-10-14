#!/usr/bin/env python3
"""Saved-session analysis and vehicle models.

Run python3 analysis.py --help to list commands. Each command retains the
arguments and output formats of its original standalone script.
"""
from __future__ import annotations

import bisect
import math
import statistics
import argparse
import csv
import html
import json
from dataclasses import dataclass
from pathlib import Path
import re
from collections import defaultdict


# Vn300 Gg Lap Predictor

G_MPS2 = 9.80665
MPH_PER_MPS = 2.2369362921


def predictor_percentile(values: list[float], fraction: float) -> float | None:
    clean = sorted(value for value in values if math.isfinite(value))
    if not clean:
        return None
    position = (len(clean) - 1) * fraction
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return clean[low]
    return clean[low] + (clean[high] - clean[low]) * (position - low)


def _valid_gg_sample(sample, speed_min_mph: float, speed_max_mph: float, gps_limit_m: float) -> bool:
    lateral = sample.lateral_g
    longitudinal = sample.longitudinal_g
    return (
        lateral is not None
        and longitudinal is not None
        and math.isfinite(lateral)
        and math.isfinite(longitudinal)
        and speed_min_mph <= sample.speed_mph <= speed_max_mph
        and 0.0 <= sample.pos_uncertainty_m <= gps_limit_m
        and abs(lateral) <= 2.8
        and abs(longitudinal) <= 2.8
        and math.hypot(lateral, longitudinal) <= 3.0
    )


def usable_sample_fraction(
    samples: list,
    speed_min_mph: float,
    speed_max_mph: float,
    gps_limit_m: float,
) -> float:
    if not samples:
        return 0.0
    usable = sum(
        1 for sample in samples
        if _valid_gg_sample(sample, speed_min_mph, speed_max_mph, gps_limit_m)
    )
    return usable / len(samples)


def build_car_envelope(
    segment_records: list[dict],
    envelope_percentile: float,
    angle_bin_deg: int,
    min_bin_points: int,
    speed_min_mph: float,
    speed_max_mph: float,
    gps_limit_m: float,
) -> tuple[list[dict], list[dict]]:
    bin_count = 360 // angle_bin_deg
    by_driver: dict[str, list[list[float]]] = {}
    for record in segment_records:
        driver = record.get("driver") or "Unknown"
        bins = by_driver.setdefault(driver, [[] for _ in range(bin_count)])
        for sample in record["samples"]:
            if not _valid_gg_sample(sample, speed_min_mph, speed_max_mph, gps_limit_m):
                continue
            angle = (math.degrees(math.atan2(sample.longitudinal_g, sample.lateral_g)) + 360.0) % 360.0
            bins[int(angle // angle_bin_deg) % bin_count].append(
                math.hypot(sample.lateral_g, sample.longitudinal_g)
            )

    driver_envelopes: dict[str, list[dict | None]] = {}
    driver_rows = []
    for driver, bins in by_driver.items():
        rows: list[dict | None] = []
        for bin_number, radii in enumerate(bins):
            center = bin_number * angle_bin_deg + angle_bin_deg / 2.0
            radius = predictor_percentile(radii, envelope_percentile) if len(radii) >= min_bin_points else None
            row = None
            if radius is not None:
                angle_rad = math.radians(center)
                row = {
                    "driver": driver,
                    "angle_center_deg": center,
                    "samples": len(radii),
                    "p_combined_g": radius,
                    "lateral_g": radius * math.cos(angle_rad),
                    "longitudinal_g": radius * math.sin(angle_rad),
                }
                driver_rows.append(row)
            rows.append(row)
        driver_envelopes[driver] = rows

    car_rows = []
    for bin_number in range(bin_count):
        candidates = [rows[bin_number] for rows in driver_envelopes.values() if rows[bin_number] is not None]
        if not candidates:
            continue
        best = max(candidates, key=lambda row: row["p_combined_g"])
        car_rows.append({
            "angle_center_deg": best["angle_center_deg"],
            "drivers_available": len(candidates),
            "car_p_combined_g": best["p_combined_g"],
            "lateral_g": best["lateral_g"],
            "longitudinal_g": best["longitudinal_g"],
            "source_driver": best["driver"],
            "source_samples": best["samples"],
        })
    return car_rows, driver_rows


def build_propulsion_envelope(
    segment_records: list[dict],
    speed_min_mph: float,
    speed_max_mph: float,
    gps_limit_m: float,
    envelope_percentile: float,
) -> list[dict]:
    bins: dict[int, list[float]] = {}
    for record in segment_records:
        for sample in record["samples"]:
            if not _valid_gg_sample(sample, speed_min_mph, speed_max_mph, gps_limit_m):
                continue
            if abs(sample.lateral_g) > 0.30 or sample.longitudinal_g <= 0.0:
                continue
            bin_number = int(sample.speed_mph // 5.0)
            bins.setdefault(bin_number, []).append(sample.longitudinal_g)

    rows = []
    for bin_number, values in sorted(bins.items()):
        if len(values) < 20:
            continue
        rows.append({
            "speed_center_mph": bin_number * 5.0 + 2.5,
            "samples": len(values),
            "p_acceleration_g": predictor_percentile(values, envelope_percentile),
        })
    return rows


def _linear_interpolate(xs: list[float], ys: list[float], x: float) -> float:
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    index = bisect.bisect_right(xs, x)
    x0, x1 = xs[index - 1], xs[index]
    fraction = (x - x0) / max(x1 - x0, 1e-12)
    return ys[index - 1] + fraction * (ys[index] - ys[index - 1])


def _resample(source_distance: list[float], values: list[float], grid: list[float]) -> list[float]:
    return [_linear_interpolate(source_distance, values, value) for value in grid]


def _gaussian_smooth_cyclic(values: list[float], sigma_points: float) -> list[float]:
    if not values or sigma_points <= 0.0:
        return list(values)
    radius = max(1, int(math.ceil(3.0 * sigma_points)))
    weights = [math.exp(-0.5 * (offset / sigma_points) ** 2) for offset in range(-radius, radius + 1)]
    total = sum(weights)
    count = len(values)
    return [
        sum(weight * values[(index + offset) % count] for offset, weight in zip(range(-radius, radius + 1), weights)) / total
        for index in range(count)
    ]


def _clean_speed_source(segment: list, speed_max_mph: float) -> list[float]:
    valid_speed_indices = [
        index for index, sample in enumerate(segment)
        if math.isfinite(sample.speed_mps) and 0.0 <= sample.speed_mph <= speed_max_mph
    ]
    if len(valid_speed_indices) < len(segment) * 0.98:
        raise ValueError("lap has more than 2% invalid speed samples")
    valid_speeds = [segment[index].speed_mps for index in valid_speed_indices]
    return [
        _linear_interpolate(valid_speed_indices, valid_speeds, index)
        for index in range(len(segment))
    ]


def speed_integrated_distance_m(segment: list, speed_max_mph: float) -> float:
    if len(segment) < 2:
        raise ValueError("lap has too few samples")
    duration = segment[-1].t - segment[0].t
    if not math.isfinite(duration) or duration <= 0.0:
        raise ValueError("lap duration is invalid")
    fallback_dt = duration / (len(segment) - 1)
    speeds = _clean_speed_source(segment, speed_max_mph)
    distance = 0.0
    for index, (previous, current) in enumerate(zip(segment, segment[1:])):
        dt = current.t - previous.t
        if not (0.0 < dt < 0.25):
            dt = fallback_dt
        distance += max(0.0, 0.5 * (speeds[index] + speeds[index + 1]) * dt)
    return distance


def select_consistent_lap_records(
    records: list[dict],
    speed_max_mph: float,
    tolerance_fraction: float = 0.15,
) -> tuple[list[dict], float]:
    candidates = []
    for record in records:
        try:
            distance = speed_integrated_distance_m(record["samples"], speed_max_mph)
        except ValueError:
            continue
        candidates.append({**record, "speed_integrated_distance_m": distance})
    if not candidates:
        raise ValueError("no laps have credible speed-integrated distance")

    def cluster(seed: dict) -> list[dict]:
        tolerance = max(10.0, seed["speed_integrated_distance_m"] * tolerance_fraction)
        return [
            record for record in candidates
            if abs(record["speed_integrated_distance_m"] - seed["speed_integrated_distance_m"]) <= tolerance
        ]

    clusters = [cluster(seed) for seed in candidates]
    best_cluster = max(
        clusters,
        key=lambda rows: (
            len(rows),
            statistics.median(row["speed_integrated_distance_m"] for row in rows),
        ),
    )
    center = statistics.median(row["speed_integrated_distance_m"] for row in best_cluster)
    tolerance = max(10.0, center * tolerance_fraction)
    accepted = [
        record for record in candidates
        if abs(record["speed_integrated_distance_m"] - center) <= tolerance
    ]
    return accepted, center


def prepare_reference_lap(
    segment: list,
    grid_step_m: float,
    smoothing_m: float,
    speed_max_mph: float,
) -> dict:
    if len(segment) < 100:
        raise ValueError("reference lap has too few samples")
    duration = segment[-1].t - segment[0].t
    fallback_dt = duration / max(len(segment) - 1, 1)
    speed_source = _clean_speed_source(segment, speed_max_mph)
    source_distance = [0.0]
    for index, (previous, current) in enumerate(zip(segment, segment[1:])):
        dt = current.t - previous.t
        if not (0.0 < dt < 0.25):
            dt = fallback_dt
        increment = 0.5 * (speed_source[index] + speed_source[index + 1]) * dt
        source_distance.append(source_distance[-1] + max(0.0, increment))
    distance_m = source_distance[-1]
    if distance_m < 100.0:
        raise ValueError("speed-integrated reference distance is not credible")

    points = max(200, int(round(distance_m / grid_step_m)))
    ds_m = distance_m / points
    grid = [index * ds_m for index in range(points)]
    speed = _resample(source_distance, speed_source, grid)
    curvature_source = [
        max(-0.35, min(0.35, sample.curvature_1pm if math.isfinite(sample.curvature_1pm) else 0.0))
        for sample in segment
    ]
    curvature = _resample(source_distance, curvature_source, grid)

    paired = [
        (sample.curvature_1pm, sample.lateral_g)
        for sample in segment
        if sample.lateral_g is not None
        and math.isfinite(sample.lateral_g)
        and math.isfinite(sample.curvature_1pm)
        and abs(sample.lateral_g) > 0.20
        and abs(sample.curvature_1pm) > 0.001
    ]
    sign_score = sum(curve * lateral for curve, lateral in paired)
    sign_multiplier = 1.0 if sign_score >= 0.0 else -1.0
    curvature = [value * sign_multiplier for value in curvature]
    curvature = _gaussian_smooth_cyclic(curvature, max(0.5, smoothing_m / ds_m))
    curvature = [max(-0.35, min(0.35, value)) for value in curvature]
    raw_heading_change = sum(curvature) * ds_m
    if abs(raw_heading_change) > math.pi:
        winding_count = max(1, round(abs(raw_heading_change) / (2.0 * math.pi)))
        target = math.copysign(2.0 * math.pi * winding_count, raw_heading_change)
        scale = target / raw_heading_change
        curvature = [value * scale for value in curvature]

    return {
        "duration_s": duration,
        "distance_m": distance_m,
        "ds_m": ds_m,
        "distance_grid_m": grid,
        "actual_speed_mps": speed,
        "curvature_1pm": curvature,
        "raw_heading_change_deg": math.degrees(raw_heading_change),
        "heading_change_deg": math.degrees(sum(curvature) * ds_m),
        "lateral_sign_multiplier": sign_multiplier,
    }


class EnvelopePolygon:
    def __init__(self, envelope_rows: list[dict]):
        ordered = sorted(envelope_rows, key=lambda row: row["angle_center_deg"])
        self.points = [(row["lateral_g"], row["longitudinal_g"]) for row in ordered]
        if len(self.points) < 12:
            raise ValueError("car G-G envelope has too few populated directions")

    def vertical_bounds(self, lateral_g: float) -> tuple[float, float]:
        intersections = []
        for first, second in zip(self.points, self.points[1:] + self.points[:1]):
            low, high = sorted((first[0], second[0]))
            if lateral_g < low - 1e-12 or lateral_g > high + 1e-12:
                continue
            delta = second[0] - first[0]
            if abs(delta) < 1e-12:
                intersections.extend((first[1], second[1]))
            else:
                fraction = (lateral_g - first[0]) / delta
                intersections.append(first[1] + fraction * (second[1] - first[1]))
        if not intersections:
            return 0.0, 0.0
        return min(intersections), max(intersections)

    def pure_lateral_limits(self) -> tuple[float, float]:
        intersections = []
        for first, second in zip(self.points, self.points[1:] + self.points[:1]):
            low, high = sorted((first[1], second[1]))
            if 0.0 < low - 1e-12 or 0.0 > high + 1e-12:
                continue
            delta = second[1] - first[1]
            if abs(delta) < 1e-12:
                intersections.extend((first[0], second[0]))
            else:
                fraction = -first[1] / delta
                intersections.append(first[0] + fraction * (second[0] - first[0]))
        if len(intersections) < 2:
            raise ValueError("car G-G envelope does not cross the pure-lateral axis")
        return min(intersections), max(intersections)


def solve_minimum_lap(
    reference: dict,
    envelope_rows: list[dict],
    propulsion_rows: list[dict],
    maximum_speed_mph: float,
) -> dict:
    polygon = EnvelopePolygon(envelope_rows)
    right_limit_g, left_limit_g = polygon.pure_lateral_limits()
    curvature = reference["curvature_1pm"]
    ds_m = reference["ds_m"]
    maximum_speed_mps = maximum_speed_mph / MPH_PER_MPS
    lateral_speed_cap = []
    for value in curvature:
        if value > 1e-9:
            lateral_speed_cap.append(math.sqrt(left_limit_g * G_MPS2 / value))
        elif value < -1e-9:
            lateral_speed_cap.append(math.sqrt(abs(right_limit_g) * G_MPS2 / abs(value)))
        else:
            lateral_speed_cap.append(maximum_speed_mps)
    speed = [min(value, maximum_speed_mps) for value in lateral_speed_cap]

    propulsion_speeds = [row["speed_center_mph"] for row in propulsion_rows]
    propulsion_caps = [row["p_acceleration_g"] for row in propulsion_rows]
    _, envelope_accel_cap = polygon.vertical_bounds(0.0)

    def propulsion_cap(speed_mps: float) -> float:
        if not propulsion_rows:
            return max(0.0, envelope_accel_cap)
        speed_mph = speed_mps * MPH_PER_MPS
        if speed_mph <= propulsion_speeds[-1]:
            return max(0.0, _linear_interpolate(propulsion_speeds, propulsion_caps, speed_mph))
        last_speed_mps = propulsion_speeds[-1] / MPH_PER_MPS
        return max(0.0, propulsion_caps[-1] * last_speed_mps / max(speed_mps, last_speed_mps))

    iterations = 0
    count = len(speed)
    for iterations in range(1, 1001):
        previous = list(speed)
        for index in range(count):
            source = (index - 1) % count
            lateral_g = curvature[source] * speed[source] ** 2 / G_MPS2
            _, tire_accel_g = polygon.vertical_bounds(lateral_g)
            acceleration = max(0.0, min(tire_accel_g, propulsion_cap(speed[source]))) * G_MPS2
            reachable = math.sqrt(max(0.0, speed[source] ** 2 + 2.0 * acceleration * ds_m))
            speed[index] = min(speed[index], reachable, lateral_speed_cap[index], maximum_speed_mps)

        for index in range(count - 1, -1, -1):
            target = (index + 1) % count
            if speed[index] <= speed[target]:
                continue
            low = speed[target]
            high = min(speed[index], lateral_speed_cap[index], maximum_speed_mps)
            for _ in range(35):
                candidate = 0.5 * (low + high)
                required_g = (candidate ** 2 - speed[target] ** 2) / (2.0 * ds_m * G_MPS2)
                lateral_g = curvature[index] * candidate ** 2 / G_MPS2
                tire_brake_g, _ = polygon.vertical_bounds(lateral_g)
                if required_g <= max(0.0, -tire_brake_g):
                    low = candidate
                else:
                    high = candidate
            speed[index] = min(speed[index], low)

        if max(abs(value - old) for value, old in zip(speed, previous)) < 1e-7:
            break

    next_speed = speed[1:] + speed[:1]
    dt = [2.0 * ds_m / max(value + following, 0.2) for value, following in zip(speed, next_speed)]
    longitudinal_g = [
        (following ** 2 - value ** 2) / (2.0 * ds_m * G_MPS2)
        for value, following in zip(speed, next_speed)
    ]
    lateral_g = [value * velocity ** 2 / G_MPS2 for value, velocity in zip(curvature, speed)]
    brake_limit_g, acceleration_limit_g = polygon.vertical_bounds(0.0)
    return {
        "lap_time_s": sum(dt),
        "speed_mps": speed,
        "longitudinal_g": longitudinal_g,
        "lateral_g": lateral_g,
        "iterations": iterations,
        "max_speed_mph": max(speed) * MPH_PER_MPS,
        "mean_speed_mph": reference["distance_m"] / sum(dt) * MPH_PER_MPS,
        "left_lateral_limit_g": left_limit_g,
        "right_lateral_limit_g": right_limit_g,
        "acceleration_limit_g": acceleration_limit_g,
        "braking_limit_g": brake_limit_g,
    }


def predict_car_gg_lap(
    segment_records: list[dict],
    reference_record: dict,
    envelope_percentile: float = 0.98,
    angle_bin_deg: int = 10,
    min_bin_points: int = 12,
    speed_min_mph: float = 5.0,
    speed_max_mph: float = 60.0,
    gps_limit_m: float = 4.0,
    grid_step_m: float = 0.5,
    smoothing_m: float = 2.5,
    maximum_speed_mph: float = 80.0,
) -> dict:
    car_envelope, driver_envelopes = build_car_envelope(
        segment_records,
        envelope_percentile,
        angle_bin_deg,
        min_bin_points,
        speed_min_mph,
        speed_max_mph,
        gps_limit_m,
    )
    expected_bins = 360 // angle_bin_deg
    if len(car_envelope) < max(12, int(expected_bins * 0.75)):
        raise ValueError(f"car G-G envelope populated only {len(car_envelope)} of {expected_bins} directions")
    propulsion = build_propulsion_envelope(
        segment_records,
        speed_min_mph,
        speed_max_mph,
        gps_limit_m,
        envelope_percentile,
    )
    reference = prepare_reference_lap(reference_record["samples"], grid_step_m, smoothing_m, speed_max_mph)
    solution = solve_minimum_lap(reference, car_envelope, propulsion, maximum_speed_mph)
    trace = []
    for index, distance_m in enumerate(reference["distance_grid_m"]):
        trace.append({
            "distance_m": distance_m,
            "curvature_1pm": reference["curvature_1pm"][index],
            "actual_speed_mph": reference["actual_speed_mps"][index] * MPH_PER_MPS,
            "predicted_speed_mph": solution["speed_mps"][index] * MPH_PER_MPS,
            "predicted_lateral_g": solution["lateral_g"][index],
            "predicted_longitudinal_g": solution["longitudinal_g"][index],
        })

    summary = {
        "model_name": "measured car P98 G-G same-line minimum lap",
        "predicted_lap_time_s": solution["lap_time_s"],
        "reference_recorded_lap_time_s": reference["duration_s"],
        "potential_vs_reference_s": reference["duration_s"] - solution["lap_time_s"],
        "reference_name": reference_record.get("display_name") or reference_record.get("name", ""),
        "reference_lap_number": reference_record.get("local_lap_number", ""),
        "reference_driver": reference_record.get("driver", "Unknown"),
        "reference_source": reference_record["samples"][0].source,
        "course_distance_m": reference["distance_m"],
        "envelope_percentile": envelope_percentile,
        "angle_bin_deg": angle_bin_deg,
        "populated_directions": len(car_envelope),
        "drivers_in_envelope": len(set(row["source_driver"] for row in car_envelope)),
        "left_lateral_limit_g": solution["left_lateral_limit_g"],
        "right_lateral_limit_g": solution["right_lateral_limit_g"],
        "acceleration_limit_g": solution["acceleration_limit_g"],
        "braking_limit_g": solution["braking_limit_g"],
        "predicted_max_speed_mph": solution["max_speed_mph"],
        "predicted_mean_speed_mph": solution["mean_speed_mph"],
        "maximum_speed_cap_mph": maximum_speed_mph,
        "iterations": solution["iterations"],
        "raw_heading_change_deg": reference["raw_heading_change_deg"],
        "closed_heading_change_deg": reference["heading_change_deg"],
        "lateral_sign_multiplier": reference["lateral_sign_multiplier"],
        "warning": "Mathematical lower bound: the car P98 G-G and propulsion envelopes stitch short peaks from different laps and assume they are continuously repeatable.",
    }
    return {
        "summary": summary,
        "car_envelope": car_envelope,
        "driver_envelopes": driver_envelopes,
        "propulsion_envelope": propulsion,
        "trace": trace,
    }


# Vn300 Lap Analysis
#!/usr/bin/env python3






EARTH_RADIUS_M = 6371000.0
MAX_LIVE_DELTA_POS_UNCERTAINTY_M = 4.0
G_MPS2 = 9.80665
ACTIVE_SPEED_MPH = 5.0
LAT_G_THRESHOLD = 0.8
BRAKE_G_THRESHOLD = 0.5


@dataclass
class Sample:
    source: str
    row_index: int
    t: float
    lat: float
    lon: float
    alt_m: float
    yaw_deg: float
    pitch_deg: float
    roll_deg: float
    speed_mps: float
    speed_mph: float
    pos_uncertainty_m: float
    longitudinal_g: float | None = None
    lateral_g: float | None = None
    vertical_g: float | None = None
    gps_time_s: float | None = None
    yaw_rate_dps: float = 0.0
    curvature_1pm: float = 0.0
    dist_m: float = 0.0
    x_m: float = 0.0
    y_m: float = 0.0


def parse_float(row: dict, name: str, default=0.0):
    try:
        value = row.get(name, default)
        if value in ("", None):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def first_existing_float(row: dict, names: list[str]):
    for name in names:
        value = parse_float(row, name, None)
        if value is not None:
            return value
    return None


def sample_step(items: list, max_points: int = 1600) -> int:
    return max(1, len(items) // max_points)


def parse_vn_time_seconds(value) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    hours = int(numeric // 10000)
    minutes = int((numeric - hours * 10000) // 100)
    seconds = numeric - hours * 10000 - minutes * 100
    if not (0 <= hours < 24 and 0 <= minutes < 60 and 0 <= seconds < 100):
        return None
    return hours * 3600 + minutes * 60 + seconds


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def project_xy(lat: float, lon: float, origin_lat: float, origin_lon: float) -> tuple[float, float]:
    x = math.radians(lon - origin_lon) * EARTH_RADIUS_M * math.cos(math.radians(origin_lat))
    y = math.radians(lat - origin_lat) * EARTH_RADIUS_M
    return x, y


def unwrap_angle_rad(previous: float, current: float) -> float:
    if not math.isfinite(previous) or not math.isfinite(current):
        return previous
    return previous + (current - previous + math.pi) % (2.0 * math.pi) - math.pi


def inspect_csv_fieldnames(path: Path) -> list[str]:
    try:
        with path.open(newline="") as f:
            return list(csv.DictReader(f).fieldnames or [])
    except OSError:
        return []


def load_vnins(path: Path) -> list[Sample]:
    rows: list[Sample] = []
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            if row.get("Checksum_OK") not in ("True", "true", "1", "", None):
                continue
            vn = parse_float(row, "Vel_N_mps")
            ve = parse_float(row, "Vel_E_mps")
            vd = parse_float(row, "Vel_D_mps")
            speed_mps = math.sqrt(vn * vn + ve * ve + vd * vd)
            longitudinal_g = first_existing_float(row, [
                "Longitudinal_G",
                "Common_Accel_X_g",
            ])
            lateral_g = first_existing_float(row, [
                "Lateral_G",
                "Common_Accel_Y_g",
            ])
            vertical_g = first_existing_float(row, [
                "Vertical_G",
                "Common_Accel_Z_g",
            ])
            if longitudinal_g is None:
                accel = first_existing_float(row, [
                    "Longitudinal_Accel_mps2",
                    "Accel_X_mps2",
                    "Common_Accel_X_mps2",
                    "Common_Imu_Accel_X_mps2",
                ])
                longitudinal_g = None if accel is None else accel / G_MPS2
            if lateral_g is None:
                accel = first_existing_float(row, [
                    "Lateral_Accel_mps2",
                    "Accel_Y_mps2",
                    "Common_Accel_Y_mps2",
                    "Common_Imu_Accel_Y_mps2",
                ])
                lateral_g = None if accel is None else accel / G_MPS2
            if vertical_g is None:
                accel = first_existing_float(row, [
                    "Vertical_Accel_mps2",
                    "Accel_Z_mps2",
                    "Common_Accel_Z_mps2",
                    "Common_Imu_Accel_Z_mps2",
                ])
                vertical_g = None if accel is None else accel / G_MPS2
            rows.append(Sample(
                source=path.name,
                row_index=i,
                t=parse_float(row, "Pi_Elapsed_Time_s"),
                lat=parse_float(row, "Latitude_deg"),
                lon=parse_float(row, "Longitude_deg"),
                alt_m=parse_float(row, "Altitude_m"),
                yaw_deg=parse_float(row, "Yaw_deg"),
                pitch_deg=parse_float(row, "Pitch_deg"),
                roll_deg=parse_float(row, "Roll_deg"),
                speed_mps=speed_mps,
                speed_mph=speed_mps * 2.2369362921,
                pos_uncertainty_m=parse_float(row, "PosUncertainty_m"),
                longitudinal_g=longitudinal_g,
                lateral_g=lateral_g,
                vertical_g=vertical_g,
                gps_time_s=parse_vn_time_seconds(row.get("Time_s")),
            ))

    if not rows:
        return rows

    repeated_or_backwards = sum(1 for prev, cur in zip(rows, rows[1:]) if cur.t <= prev.t)
    gps_times = [s.gps_time_s for s in rows]
    usable_gps_times = sum(1 for value in gps_times if value is not None)
    if usable_gps_times > len(rows) * 0.9 and repeated_or_backwards > len(rows) * 0.1:
        base_time = next(value for value in gps_times if value is not None)
        last_time = base_time
        day_offset = 0.0
        for sample, gps_time in zip(rows, gps_times):
            if gps_time is None:
                sample.t = last_time - base_time
                continue
            if gps_time + day_offset < last_time - 12 * 3600:
                day_offset += 24 * 3600
            sample.t = gps_time + day_offset - base_time
            last_time = gps_time + day_offset

    origin_lat = rows[0].lat
    origin_lon = rows[0].lon
    rows[0].x_m, rows[0].y_m = project_xy(rows[0].lat, rows[0].lon, origin_lat, origin_lon)
    for prev, cur in zip(rows, rows[1:]):
        cur.dist_m = prev.dist_m + haversine_m(prev.lat, prev.lon, cur.lat, cur.lon)
        cur.x_m, cur.y_m = project_xy(cur.lat, cur.lon, origin_lat, origin_lon)
        dt = cur.t - prev.t
        if dt > 0:
            prev_yaw = math.radians(prev.yaw_deg)
            cur_yaw = unwrap_angle_rad(prev_yaw, math.radians(cur.yaw_deg))
            yaw_rate_rps = (cur_yaw - prev_yaw) / dt
            cur.yaw_rate_dps = math.degrees(yaw_rate_rps)
            cur.curvature_1pm = yaw_rate_rps / max(cur.speed_mps, 0.1)
            if cur.longitudinal_g is None:
                derived_long_g = (cur.speed_mps - prev.speed_mps) / dt / G_MPS2
                cur.longitudinal_g = derived_long_g if abs(derived_long_g) <= 3.0 else None
            if cur.lateral_g is None:
                derived_lat_g = cur.speed_mps * yaw_rate_rps / G_MPS2
                cur.lateral_g = derived_lat_g if abs(derived_lat_g) <= 3.0 else None
    return rows


def segment_intersection_fraction(p1, p2, q1, q2):
    px, py = p1
    rx, ry = p2[0] - p1[0], p2[1] - p1[1]
    qx, qy = q1
    sx, sy = q2[0] - q1[0], q2[1] - q1[1]
    denom = rx * sy - ry * sx
    if abs(denom) < 1e-9:
        return None
    qpx, qpy = qx - px, qy - py
    t = (qpx * sy - qpy * sx) / denom
    u = (qpx * ry - qpy * rx) / denom
    if 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0:
        return t
    return None


def crossing_indices(samples: list[Sample], line: tuple[float, float, float, float], min_speed_mps: float, min_gap_s: float):
    if not samples:
        return []
    lat1, lon1, lat2, lon2 = line
    origin_lat = samples[0].lat
    origin_lon = samples[0].lon
    q1 = project_xy(lat1, lon1, origin_lat, origin_lon)
    q2 = project_xy(lat2, lon2, origin_lat, origin_lon)
    crossings = []
    last_t = -1e99
    for i in range(1, len(samples)):
        prev = samples[i - 1]
        cur = samples[i]
        if max(prev.speed_mps, cur.speed_mps) < min_speed_mps:
            continue
        frac = segment_intersection_fraction((prev.x_m, prev.y_m), (cur.x_m, cur.y_m), q1, q2)
        if frac is None:
            continue
        cross_t = prev.t + frac * (cur.t - prev.t)
        if cross_t - last_t >= min_gap_s:
            pos_uncertainty = max(prev.pos_uncertainty_m, cur.pos_uncertainty_m)
            warning = ""
            if pos_uncertainty > MAX_LIVE_DELTA_POS_UNCERTAINTY_M:
                warning = f"Invalid GPS: uncertainty {pos_uncertainty:.1f} m > {MAX_LIVE_DELTA_POS_UNCERTAINTY_M:.1f} m"
            crossings.append({"index": i, "time_s": cross_t, "warning": warning})
            last_t = cross_t
    return crossings


def combine_warnings(*values: str) -> str:
    warnings = [value for value in values if value]
    return "; ".join(dict.fromkeys(warnings))


def split_laps(samples: list[Sample], crossings: list[dict]) -> list[dict]:
    laps = []
    for start, end in zip(crossings, crossings[1:]):
        start_i = start["index"]
        end_i = end["index"]
        if end_i > start_i:
            laps.append({
                "samples": samples[start_i:end_i + 1],
                "warning": combine_warnings(start.get("warning", ""), end.get("warning", "")),
            })
    return laps


def split_autocross_runs(samples: list[Sample], start_crossings: list[dict], finish_crossings: list[dict]) -> list[dict]:
    runs = []
    finish_i = 0
    for start in start_crossings:
        while finish_i < len(finish_crossings) and finish_crossings[finish_i]["time_s"] <= start["time_s"]:
            finish_i += 1
        if finish_i >= len(finish_crossings):
            break
        start_i = start["index"]
        end_i = finish_crossings[finish_i]["index"]
        if end_i > start_i:
            runs.append({
                "samples": samples[start_i:end_i + 1],
                "warning": combine_warnings(start.get("warning", ""), finish_crossings[finish_i].get("warning", "")),
            })
        finish_i += 1
    return runs


def summarize_segment(name: str, segment: list[Sample]) -> dict:
    if not segment:
        return {"name": name, "samples": 0}
    duration = segment[-1].t - segment[0].t
    distance = segment[-1].dist_m - segment[0].dist_m
    max_speed = max(s.speed_mph for s in segment)
    avg_speed = (distance / duration) * 2.2369362921 if duration > 0 else 0.0
    max_pos_uncertainty = max(s.pos_uncertainty_m for s in segment)
    bad_pos_samples = sum(1 for s in segment if s.pos_uncertainty_m > MAX_LIVE_DELTA_POS_UNCERTAINTY_M)
    return {
        "name": name,
        "source": segment[0].source,
        "samples": len(segment),
        "start_s": segment[0].t,
        "end_s": segment[-1].t,
        "duration_s": duration,
        "distance_m": distance,
        "max_speed_mph": max_speed,
        "avg_speed_mph": avg_speed,
        "max_pos_uncertainty_m": max_pos_uncertainty,
        "gps_uncertainty_over_4m_samples": bad_pos_samples,
        "gps_uncertainty_over_4m_pct": (bad_pos_samples / len(segment)) * 100.0,
        "min_lat": min(s.lat for s in segment),
        "max_lat": max(s.lat for s in segment),
        "min_lon": min(s.lon for s in segment),
        "max_lon": max(s.lon for s in segment),
    }


def finite_values(values):
    return [value for value in values if value is not None and math.isfinite(value)]


def pct(count: int, total: int) -> float:
    return (count / total) * 100.0 if total else 0.0


def summarize_grip(segment: list[Sample]) -> dict:
    active = [s for s in segment if s.speed_mph >= ACTIVE_SPEED_MPH]
    base = active or segment
    long_g = finite_values([s.longitudinal_g for s in base])
    lat_g = finite_values([s.lateral_g for s in base])
    combined = [
        math.sqrt((s.longitudinal_g or 0.0) ** 2 + (s.lateral_g or 0.0) ** 2)
        for s in base
        if s.longitudinal_g is not None or s.lateral_g is not None
    ]
    yaw_rates = finite_values([s.yaw_rate_dps for s in base])
    curvatures = finite_values([abs(s.curvature_1pm) for s in base])
    return {
        "active_samples_over_5mph": len(active),
        "peak_lateral_g_left": max(lat_g) if lat_g else "",
        "peak_lateral_g_right": min(lat_g) if lat_g else "",
        "peak_lateral_g_abs": max((abs(v) for v in lat_g), default=""),
        "peak_braking_g": min(long_g) if long_g else "",
        "peak_accel_g": max(long_g) if long_g else "",
        "peak_combined_g": max(combined) if combined else "",
        "avg_combined_g_active": sum(combined) / len(combined) if combined else "",
        "pct_active_over_0p8_lat_g": pct(sum(1 for v in lat_g if abs(v) >= LAT_G_THRESHOLD), len(base)),
        "pct_active_over_0p5_brake_g": pct(sum(1 for v in long_g if v <= -BRAKE_G_THRESHOLD), len(base)),
        "peak_yaw_rate_dps_abs": max((abs(v) for v in yaw_rates), default=""),
        "peak_curvature_1pm_abs": max(curvatures) if curvatures else "",
    }


def data_quality_report(path: Path, samples: list[Sample], fieldnames: list[str]) -> dict:
    warnings = []
    failures = []
    required = ["Pi_Elapsed_Time_s", "Latitude_deg", "Longitude_deg", "Vel_N_mps", "Vel_E_mps", "Vel_D_mps"]
    missing = [name for name in required if name not in fieldnames]
    if missing:
        failures.append(f"missing required columns: {', '.join(missing)}")

    accel_columns = {
        "Longitudinal_G", "Lateral_G", "Vertical_G",
        "Longitudinal_Accel_mps2", "Lateral_Accel_mps2", "Vertical_Accel_mps2",
        "Accel_X_mps2", "Accel_Y_mps2", "Accel_Z_mps2",
        "Common_Accel_X_mps2", "Common_Accel_Y_mps2", "Common_Accel_Z_mps2",
    }
    if not accel_columns.intersection(fieldnames):
        warnings.append("no explicit acceleration/G columns; using speed/yaw-derived estimates where possible")

    if not samples:
        failures.append("no valid samples")
        return {
            "source": path.name,
            "status": "not recommended",
            "warnings": "; ".join(warnings),
            "failures": "; ".join(failures),
            "warning_count": len(warnings),
            "failure_count": len(failures),
        }

    duration = samples[-1].t - samples[0].t
    zero_latlon = sum(1 for s in samples if abs(s.lat) < 1e-12 or abs(s.lon) < 1e-12)
    high_uncertainty = sum(1 for s in samples if s.pos_uncertainty_m > MAX_LIVE_DELTA_POS_UNCERTAINTY_M)
    nonmonotonic = sum(1 for prev, cur in zip(samples, samples[1:]) if cur.t <= prev.t)
    impossible_speed = sum(1 for s in samples if s.speed_mph > 120.0)
    gps_jumps = 0
    for prev, cur in zip(samples, samples[1:]):
        dt = cur.t - prev.t
        if dt <= 0:
            continue
        jump_m = haversine_m(prev.lat, prev.lon, cur.lat, cur.lon)
        if jump_m > 50.0 and jump_m / dt > 90.0:
            gps_jumps += 1

    sample_rate_hz = (len(samples) - 1) / duration if duration > 0 else 0.0
    if duration < 3.0:
        warnings.append(f"short run duration: {duration:.1f} s")
    if sample_rate_hz < 5.0:
        warnings.append(f"low sample rate: {sample_rate_hz:.1f} Hz")
    if pct(high_uncertainty, len(samples)) > 10.0:
        warnings.append(f"high GPS uncertainty in {pct(high_uncertainty, len(samples)):.1f}% of samples")

    if pct(zero_latlon, len(samples)) > 1.0:
        failures.append(f"zero latitude/longitude in {pct(zero_latlon, len(samples)):.1f}% of samples")
    elif zero_latlon:
        warnings.append(f"zero latitude/longitude samples: {zero_latlon}")
    if nonmonotonic > len(samples) * 0.01:
        failures.append(f"non-monotonic timestamps: {nonmonotonic}")
    elif nonmonotonic:
        warnings.append(f"minor non-monotonic timestamps: {nonmonotonic}")
    if impossible_speed:
        failures.append(f"speed above 120 mph samples: {impossible_speed}")
    if gps_jumps > 3:
        failures.append(f"large GPS jumps: {gps_jumps}")
    elif gps_jumps:
        warnings.append(f"large GPS jumps: {gps_jumps}")

    status = "usable"
    if warnings:
        status = "usable with warnings"
    if failures:
        status = "not recommended"
    return {
        "source": path.name,
        "status": status,
        "duration_s": duration,
        "sample_rate_hz": sample_rate_hz,
        "zero_latlon_samples": zero_latlon,
        "high_gps_uncertainty_samples": high_uncertainty,
        "nonmonotonic_timestamp_samples": nonmonotonic,
        "impossible_speed_samples": impossible_speed,
        "gps_jump_count": gps_jumps,
        "warnings": "; ".join(warnings),
        "failures": "; ".join(failures),
        "warning_count": len(warnings),
        "failure_count": len(failures),
    }


def add_analysis_fields(summary: dict, segment: list[Sample]) -> dict:
    summary.update(summarize_grip(segment))
    return summary


def write_sector_summary(path: Path, sector_rows: list[dict]):
    write_summary(path, sector_rows)


def cornering_score(sample: Sample) -> float:
    lat_g = abs(sample.lateral_g) if sample.lateral_g is not None else 0.0
    curvature_score = abs(sample.curvature_1pm) * 25.0
    slow_penalty = max(0.0, (12.0 - sample.speed_mph) / 12.0) * 0.25
    return lat_g + curvature_score + slow_penalty


def choose_auto_sector_boundary(segment: list[Sample], target_t: float, search_window_s: float) -> int | None:
    if len(segment) < 6:
        return None
    first_t = segment[0].t
    last_t = segment[-1].t
    low = max(first_t + 1.0, target_t - search_window_s)
    high = min(last_t - 1.0, target_t + search_window_s)
    candidates = [
        (i, sample)
        for i, sample in enumerate(segment[1:-1], start=1)
        if low <= sample.t <= high
    ]
    if not candidates:
        return None

    def score(item):
        i, sample = item
        target_penalty = abs(sample.t - target_t) / max(search_window_s, 1.0) * 0.15
        edge_penalty = 0.0
        if i < 3 or i > len(segment) - 4:
            edge_penalty = 10.0
        return cornering_score(sample) + target_penalty + edge_penalty

    return min(candidates, key=score)[0]


def split_auto_time_sectors(segment: list[Sample], sector_count: int = 3) -> list[dict]:
    if sector_count < 2 or len(segment) < sector_count * 3:
        return []
    duration = segment_duration(segment)
    if duration <= sector_count:
        return []

    search_window_s = max(3.0, duration * 0.12)
    boundaries = [0]
    for split_number in range(1, sector_count):
        target_t = segment[0].t + duration * split_number / sector_count
        boundary = choose_auto_sector_boundary(segment, target_t, search_window_s)
        if boundary is None:
            boundary = min(range(1, len(segment) - 1), key=lambda i: abs(segment[i].t - target_t))
        if boundary <= boundaries[-1] + 2:
            boundary = min(len(segment) - 2, boundaries[-1] + max(3, len(segment) // sector_count // 3))
        boundaries.append(boundary)
    boundaries.append(len(segment) - 1)

    if any(end <= start for start, end in zip(boundaries, boundaries[1:])):
        return []

    sectors = []
    for sector_number, (start_i, end_i) in enumerate(zip(boundaries, boundaries[1:]), start=1):
        sector_samples = segment[start_i:end_i + 1]
        if len(sector_samples) < 2:
            continue
        boundary_sample = segment[start_i] if start_i else segment[0]
        sectors.append({
            "sequence_number": 1,
            "sector_number": sector_number,
            "sector_name": f"auto_sector_{sector_number:02d}",
            "start_gate": "segment_start" if sector_number == 1 else f"auto_gate_{sector_number - 1:02d}",
            "end_gate": "segment_finish" if sector_number == sector_count else f"auto_gate_{sector_number:02d}",
            "start_s": sector_samples[0].t,
            "end_s": sector_samples[-1].t,
            "samples": sector_samples,
            "warning": "",
            "auto_gate_lat": boundary_sample.lat,
            "auto_gate_lon": boundary_sample.lon,
            "auto_gate_cornering_score": cornering_score(boundary_sample),
        })
    return sectors


def append_sector_summaries(
    sector_summaries: list[dict],
    sectors: list[dict],
    base_name: str,
    segment_type: str,
    source_segment_number,
    quality: dict,
    path_metadata: dict,
):
    for sector in sectors:
        sector_name = f"{base_name}_seq_{sector['sequence_number']:02d}_sector_{sector['sector_number']:02d}"
        sector_summary = add_analysis_fields(summarize_segment(sector_name, sector["samples"]), sector["samples"])
        sector_summary["parent_segment_type"] = segment_type
        sector_summary["parent_segment_number"] = source_segment_number
        sector_summary["sequence_number"] = sector["sequence_number"]
        sector_summary["sector_number"] = sector["sector_number"]
        sector_summary["sector_name"] = sector["sector_name"]
        sector_summary["start_gate"] = sector["start_gate"]
        sector_summary["end_gate"] = sector["end_gate"]
        sector_summary["crossing_start_s"] = sector["start_s"]
        sector_summary["crossing_end_s"] = sector["end_s"]
        sector_summary["timing_warning"] = sector["warning"]
        sector_summary["auto_gate_lat"] = sector.get("auto_gate_lat", "")
        sector_summary["auto_gate_lon"] = sector.get("auto_gate_lon", "")
        sector_summary["auto_gate_cornering_score"] = sector.get("auto_gate_cornering_score", "")
        sector_summary["data_quality_status"] = quality.get("status", "")
        sector_summary.update(path_metadata)
        sector_summaries.append(sector_summary)


def reference_time_at_distance(reference: list[Sample], distance_m: float):
    if len(reference) < 2:
        return None
    d0 = reference[0].dist_m
    if distance_m <= 0:
        return 0.0
    for prev, cur in zip(reference, reference[1:]):
        prev_d = prev.dist_m - d0
        cur_d = cur.dist_m - d0
        if prev_d <= distance_m <= cur_d:
            span = cur_d - prev_d
            if span <= 0:
                return cur.t - reference[0].t
            frac = (distance_m - prev_d) / span
            return (prev.t - reference[0].t) + frac * (cur.t - prev.t)
    return reference[-1].t - reference[0].t


def live_deltas_to_reference(segment: list[Sample], reference):
    if not reference:
        return [{"delta_s": None, "error": "no best reference"} for _ in segment]
    d0 = segment[0].dist_m
    t0 = segment[0].t
    deltas = []
    for sample in segment:
        if sample.pos_uncertainty_m > MAX_LIVE_DELTA_POS_UNCERTAINTY_M:
            deltas.append({
                "delta_s": None,
                "error": f"GPS uncertainty {sample.pos_uncertainty_m:.1f} m > {MAX_LIVE_DELTA_POS_UNCERTAINTY_M:.1f} m",
            })
            continue
        lap_distance = sample.dist_m - d0
        elapsed = sample.t - t0
        reference_elapsed = reference_time_at_distance(reference, lap_distance)
        deltas.append({
            "delta_s": None if reference_elapsed is None else elapsed - reference_elapsed,
            "error": "" if reference_elapsed is not None else "no best reference",
        })
    return deltas


def fixed_distance_sector_times(segment: list[Sample], sector_count: int = 3) -> tuple[list[float], list[float]]:
    if sector_count < 2 or len(segment) < sector_count + 1:
        return [], []
    total_distance = segment[-1].dist_m - segment[0].dist_m
    duration = segment_duration(segment)
    if total_distance <= 0.0 or duration <= 0.0:
        return [], []

    elapsed_points = [0.0]
    gates_m = []
    for split_number in range(1, sector_count):
        distance_m = total_distance * split_number / sector_count
        elapsed = reference_time_at_distance(segment, distance_m)
        if elapsed is None:
            return [], []
        elapsed_points.append(elapsed)
        gates_m.append(distance_m)
    elapsed_points.append(duration)

    sector_times = [
        elapsed_points[i + 1] - elapsed_points[i]
        for i in range(sector_count)
    ]
    if any(value < 0.0 for value in sector_times):
        return [], []
    return sector_times, gates_m


def append_lap_sector_split(
    sector_split_rows: list[dict],
    name: str,
    segment: list[Sample],
    segment_type: str,
    global_segment_number: int,
    local_segment_number: int,
    timing_warning: str,
    path_metadata: dict,
    sector_count: int = 3,
):
    sector_times, gates_m = fixed_distance_sector_times(segment, sector_count)
    if len(sector_times) != sector_count:
        return
    duration = segment_duration(segment)
    row = {
        "name": name,
        "source": segment[0].source,
        "driver": path_metadata.get("meta_driver", ""),
        "segment_type": segment_type,
        "segment_number": global_segment_number,
        "lap_number": local_segment_number if segment_type == "lap" else "",
        "run_number": local_segment_number if segment_type == "run" else "",
        "duration_s": duration,
        "distance_m": segment[-1].dist_m - segment[0].dist_m,
        "max_speed_mph": max(s.speed_mph for s in segment),
        "timing_warning": timing_warning,
        "sector_method": "equal_distance_thirds",
    }
    for index, value in enumerate(sector_times, start=1):
        row[f"sector_{index}_s"] = value
    for index, value in enumerate(gates_m, start=1):
        row[f"sector_{index}_end_m"] = value
    row.update(path_metadata)
    sector_split_rows.append(row)


def format_seconds(value) -> str:
    if value in ("", None):
        return ""
    return f"{float(value):.3f}"


def credible_sector_rows(rows: list[dict], sector_count: int = 3) -> tuple[list[dict], list[dict]]:
    accepted = []
    rejected = []
    for row in rows:
        duration = float(row.get("duration_s", 0.0) or 0.0)
        distance = float(row.get("distance_m", 0.0) or 0.0)
        max_speed = float(row.get("max_speed_mph", 0.0) or 0.0)
        sectors = [float(row.get(f"sector_{number}_s", 0.0) or 0.0) for number in range(1, sector_count + 1)]
        reason = ""
        if not all(math.isfinite(value) for value in [duration, distance, max_speed, *sectors]):
            reason = "non-finite lap or sector value"
        elif duration <= 0.0 or distance <= 0.0 or not (0.0 < max_speed <= 120.0):
            reason = "invalid duration, distance, or maximum speed"
        elif abs(sum(sectors) - duration) > max(0.10, duration * 0.01):
            reason = "sector sum does not match lap duration"
        elif min(sectors) < duration * 0.05:
            reason = "a sector is less than 5% of lap duration"
        if reason:
            rejected.append({**row, "reason": reason})
        else:
            accepted.append(row)
    return accepted, rejected


def write_lap_sector_outputs(
    out_dir: Path,
    rows: list[dict],
    excluded_rows: list[dict],
    sector_count: int = 3,
    gg_prediction: dict | None = None,
):
    rows, credibility_rejections = credible_sector_rows(rows, sector_count)
    excluded_rows = [*excluded_rows, *credibility_rejections]
    if not rows:
        if excluded_rows:
            write_summary(out_dir / "lap_sector_excluded.csv", excluded_rows)
        return

    best_sector = {
        sector_number: min(rows, key=lambda row: row[f"sector_{sector_number}_s"])
        for sector_number in range(1, sector_count + 1)
    }
    overall_theoretical = sum(
        best_sector[sector_number][f"sector_{sector_number}_s"]
        for sector_number in range(1, sector_count + 1)
    )
    best_lap_time = min(row["duration_s"] for row in rows)

    lap_fields = [
        "driver", "name", "source", "segment_type", "segment_number", "lap_number", "run_number",
        "duration_s",
        *[f"sector_{sector_number}_s" for sector_number in range(1, sector_count + 1)],
        "distance_m", "max_speed_mph", "timing_warning", "sector_method",
    ]
    write_summary(out_dir / "lap_sector_splits.csv", [
        {field: row.get(field, "") for field in lap_fields}
        for row in rows
    ])

    if excluded_rows:
        write_summary(out_dir / "lap_sector_excluded.csv", excluded_rows)

    drivers = sorted(set(row.get("driver") or "Unknown" for row in rows))
    theoretical_rows = []
    for driver in drivers:
        driver_rows = [row for row in rows if (row.get("driver") or "Unknown") == driver]
        best_lap = min(driver_rows, key=lambda row: row["duration_s"])
        driver_best_sectors = {
            sector_number: min(driver_rows, key=lambda row: row[f"sector_{sector_number}_s"])
            for sector_number in range(1, sector_count + 1)
        }
        theoretical = sum(
            driver_best_sectors[sector_number][f"sector_{sector_number}_s"]
            for sector_number in range(1, sector_count + 1)
        )
        row = {
            "driver": driver,
            "best_lap_time_s": best_lap["duration_s"],
            "best_segment_number": best_lap["segment_number"],
            "best_lap_number": best_lap.get("lap_number", ""),
            "best_run_number": best_lap.get("run_number", ""),
            "theoretical_best_s": theoretical,
            "delta_theoretical_to_best_s": best_lap["duration_s"] - theoretical,
        }
        for sector_number in range(1, sector_count + 1):
            sector_row = driver_best_sectors[sector_number]
            row[f"best_sector_{sector_number}_s"] = sector_row[f"sector_{sector_number}_s"]
            row[f"best_sector_{sector_number}_segment_number"] = sector_row["segment_number"]
            row[f"best_sector_{sector_number}_lap_number"] = sector_row.get("lap_number", "")
            row[f"best_sector_{sector_number}_run_number"] = sector_row.get("run_number", "")
        theoretical_rows.append(row)
    theoretical_rows.sort(key=lambda row: row["theoretical_best_s"])
    write_summary(out_dir / "theoretical_best_by_driver.csv", theoretical_rows)

    overall_rows = []
    for sector_number in range(1, sector_count + 1):
        row = best_sector[sector_number]
        overall_rows.append({
            "sector": sector_number,
            "driver": row.get("driver", ""),
            "name": row.get("name", ""),
            "segment_type": row.get("segment_type", ""),
            "segment_number": row.get("segment_number", ""),
            "lap_number": row.get("lap_number", ""),
            "run_number": row.get("run_number", ""),
            "sector_time_s": row[f"sector_{sector_number}_s"],
            "overall_theoretical_best_s": overall_theoretical,
        })
    write_summary(out_dir / "overall_best_sectors.csv", overall_rows)

    def segment_label(row: dict) -> str:
        if row.get("segment_type") == "lap":
            return f"L{row.get('lap_number')}"
        if row.get("segment_type") == "run":
            return f"R{row.get('run_number')}"
        return str(row.get("segment_number", ""))

    def all_laps_table() -> str:
        headers = ["Driver", "Segment", "Lap Time"]
        headers.extend(f"Sector {sector_number}" for sector_number in range(1, sector_count + 1))
        headers.extend(["Delta To Best", "Warning", "Source"])
        head = "".join(f"<th>{html.escape(header)}</th>" for header in headers)
        body = []
        for row in rows:
            delta = row["duration_s"] - best_lap_time
            cells = [
                f"<td>{html.escape(row.get('driver') or 'Unknown')}</td>",
                f"<td>{html.escape(segment_label(row))}</td>",
                f"<td>{format_seconds(row['duration_s'])}</td>",
            ]
            for sector_number in range(1, sector_count + 1):
                is_best = row is best_sector[sector_number]
                class_name = " class=\"best-sector\"" if is_best else ""
                cells.append(f"<td{class_name}>{format_seconds(row[f'sector_{sector_number}_s'])}</td>")
            cells.extend([
                f"<td>{delta:+.3f}</td>",
                f"<td>{html.escape(row.get('timing_warning', ''))}</td>",
                f"<td>{html.escape(row.get('source', ''))}</td>",
            ])
            body.append(f"<tr>{''.join(cells)}</tr>")
        return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"

    def theoretical_table() -> str:
        headers = ["Driver", "Best Lap", "Theoretical Best", "Gap"]
        headers.extend(f"Best S{sector_number}" for sector_number in range(1, sector_count + 1))
        head = "".join(f"<th>{html.escape(header)}</th>" for header in headers)
        body = []
        for row in theoretical_rows:
            cells = [
                f"<td>{html.escape(row['driver'])}</td>",
                f"<td>{format_seconds(row['best_lap_time_s'])}</td>",
                f"<td>{format_seconds(row['theoretical_best_s'])}</td>",
                f"<td>{format_seconds(row['delta_theoretical_to_best_s'])}</td>",
            ]
            for sector_number in range(1, sector_count + 1):
                label = row.get(f"best_sector_{sector_number}_lap_number") or row.get(f"best_sector_{sector_number}_run_number")
                cells.append(f"<td>{format_seconds(row[f'best_sector_{sector_number}_s'])} ({html.escape(str(label))})</td>")
            body.append(f"<tr>{''.join(cells)}</tr>")
        return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"

    def best_sector_table() -> str:
        body = []
        for sector_number in range(1, sector_count + 1):
            row = best_sector[sector_number]
            body.append(
                "<tr>"
                f"<td>Sector {sector_number}</td>"
                f"<td class=\"best-sector\">{html.escape(row.get('driver') or 'Unknown')}</td>"
                f"<td>{html.escape(segment_label(row))}</td>"
                f"<td class=\"best-sector\">{format_seconds(row[f'sector_{sector_number}_s'])}</td>"
                f"<td>{html.escape(row.get('source', ''))}</td>"
                "</tr>"
            )
        return (
            "<table><thead><tr><th>Sector</th><th>Driver</th><th>Segment</th>"
            "<th>Time</th><th>Source</th></tr></thead><tbody>"
            + "".join(body)
            + "</tbody></table>"
        )

    cards = []
    cards.append(f"<div class=\"card\"><div class=\"note\">Observed Sector Composite</div><div class=\"big\">{overall_theoretical:.3f} s</div></div>")
    if gg_prediction:
        prediction = gg_prediction["summary"]
        prediction["observed_sector_composite_s"] = overall_theoretical
        prediction["gg_prediction_vs_sector_composite_s"] = prediction["predicted_lap_time_s"] - overall_theoretical
        cards.append(
            "<div class=\"card\">"
            "<div class=\"note\">Car G-G Mathematical Best</div>"
            f"<div class=\"big\">{prediction['predicted_lap_time_s']:.3f} s</div>"
            "</div>"
        )
    for sector_number in range(1, sector_count + 1):
        row = best_sector[sector_number]
        cards.append(
            "<div class=\"card\">"
            f"<div class=\"note\">Best Sector {sector_number}</div>"
            f"<div class=\"big\">{html.escape(row.get('driver') or 'Unknown')} {html.escape(segment_label(row))} - {format_seconds(row[f'sector_{sector_number}_s'])}</div>"
            "</div>"
        )

    body = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VN300 Lap Times And Sector Splits</title>
<style>
body{{margin:0;background:#f6f7f9;color:#17202a;font-family:Arial,Helvetica,sans-serif}}
header{{background:#17202a;color:white;padding:18px 22px}}
main{{padding:18px 22px;display:grid;gap:18px}}
section{{background:white;border:1px solid #d8e0e8;border-radius:6px;padding:14px;overflow:auto}}
h1{{margin:0 0 6px;font-size:24px}}h2{{margin:0 0 10px;font-size:18px}}
.note{{color:#617080;font-size:13px;line-height:1.4}}
table{{border-collapse:collapse;width:100%;font-size:13px}}
th,td{{border-bottom:1px solid #e1e7ee;padding:6px 8px;text-align:right;white-space:nowrap}}
th:first-child,td:first-child{{text-align:left}}
th{{background:#eef2f6;position:sticky;top:0}}
.best-sector{{background:#7e22ce!important;color:white!important;font-weight:700}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}}
.card{{border:1px solid #d8e0e8;border-radius:6px;padding:12px;background:#fbfcfe}}
.big{{font-size:24px;font-weight:700;margin-top:4px}}
</style>
</head>
<body>
<header><h1>VN300 Lap Times And Sector Splits</h1><div class="note">Sector composites show demonstrated execution. The car G-G prediction is a same-line mathematical lower bound from the measured directional envelope.</div></header>
<main>
<section class="cards">{''.join(cards)}</section>
{gg_prediction_section(gg_prediction, overall_theoretical)}
<section><h2>Overall Best Sectors</h2>{best_sector_table()}</section>
<section><h2>Sector Composite By Driver</h2>{theoretical_table()}</section>
<section><h2>All Lap Times And Sector Splits</h2>{all_laps_table()}</section>
</main>
</body>
</html>"""
    (out_dir / "lap_times_sector_splits.html").write_text(body, encoding="utf-8")


def gg_prediction_section(gg_prediction: dict | None, sector_composite_s: float | None = None) -> str:
    if not gg_prediction:
        return ""
    row = gg_prediction["summary"]
    sector_row = (
        f"<tr><td>Observed sector composite</td><td>{sector_composite_s:.3f} s</td></tr>"
        if sector_composite_s is not None else ""
    )
    return (
        "<section><h2>Car G-G Lap Prediction</h2>"
        "<p class=\"note\"><strong>Mathematical lower bound, not an achievable-lap forecast.</strong> "
        "The solver applies the best measured 98th-percentile car G-G and propulsion envelopes continuously around the fastest valid lap's recorded line. "
        "Those envelopes stitch short peaks from different laps and drivers, so transient peaks are treated as sustainable.</p>"
        "<table><thead><tr><th>Metric</th><th>Value</th></tr></thead><tbody>"
        f"<tr><td>Car G-G mathematical best</td><td>{row['predicted_lap_time_s']:.3f} s</td></tr>"
        f"{sector_row}"
        f"<tr><td>Fastest recorded reference</td><td>{row['reference_recorded_lap_time_s']:.3f} s</td></tr>"
        f"<tr><td>Reference</td><td>{html.escape(row['reference_driver'])} / {html.escape(row['reference_name'])}</td></tr>"
        f"<tr><td>Pure lateral, left / right</td><td>{row['left_lateral_limit_g']:.3f} / {row['right_lateral_limit_g']:.3f} g</td></tr>"
        f"<tr><td>Pure longitudinal, accel / brake</td><td>{row['acceleration_limit_g']:.3f} / {row['braking_limit_g']:.3f} g</td></tr>"
        f"<tr><td>Predicted max speed</td><td>{row['predicted_max_speed_mph']:.1f} mph</td></tr>"
        f"<tr><td>Envelope coverage</td><td>{row['populated_directions']} directions, {row['drivers_in_envelope']} source driver(s)</td></tr>"
        "</tbody></table></section>"
    )


def write_summary(path: Path, summaries: list[dict]):
    if not summaries:
        return
    fieldnames = []
    for summary in summaries:
        for key in summary.keys():
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(summaries)


def write_lap_csv(
    path: Path,
    lap: list[Sample],
    segment_type: str,
    segment_number: int,
    final_delta_s,
    live_deltas: list,
    timing_warning: str = "",
):
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "source", "segment_type", "segment_number", "row_index",
            "time_s", "segment_time_s", "distance_m", "segment_distance_m",
            "live_delta_to_best_s", "live_delta_error", "final_delta_to_best_s", "timing_warning",
            "lat", "lon", "speed_mph", "longitudinal_g", "lateral_g", "vertical_g",
            "yaw_deg", "yaw_rate_dps", "curvature_1pm", "pitch_deg", "roll_deg", "pos_uncertainty_m",
        ])
        t0 = lap[0].t
        d0 = lap[0].dist_m
        for s, live_delta in zip(lap, live_deltas):
            live_delta_s = live_delta.get("delta_s") if isinstance(live_delta, dict) else live_delta
            live_delta_error = live_delta.get("error", "") if isinstance(live_delta, dict) else ""
            writer.writerow([
                s.source, segment_type, segment_number, s.row_index,
                f"{s.t:.6f}", f"{s.t - t0:.6f}", f"{s.dist_m:.3f}", f"{s.dist_m - d0:.3f}",
                "" if live_delta_s is None else f"{live_delta_s:.6f}",
                live_delta_error,
                "" if final_delta_s is None else f"{final_delta_s:.6f}",
                timing_warning,
                f"{s.lat:.8f}", f"{s.lon:.8f}",
                f"{s.speed_mph:.3f}",
                "" if s.longitudinal_g is None else f"{s.longitudinal_g:.4f}",
                "" if s.lateral_g is None else f"{s.lateral_g:.4f}",
                "" if s.vertical_g is None else f"{s.vertical_g:.4f}",
                f"{s.yaw_deg:.3f}", f"{s.yaw_rate_dps:.3f}", f"{s.curvature_1pm:.6f}",
                f"{s.pitch_deg:.3f}", f"{s.roll_deg:.3f}", f"{s.pos_uncertainty_m:.3f}",
            ])


def html_payload(laps: list[tuple[str, list[Sample]]]):
    output = []
    for name, lap in laps:
        if not lap:
            continue
        d0 = lap[0].dist_m
        t0 = lap[0].t
        output.append({
            "name": name,
            "xy": [[s.x_m, s.y_m] for s in lap[::max(1, len(lap) // 1200)]],
            "speed": [[s.dist_m - d0, s.speed_mph, s.t - t0] for s in lap[::max(1, len(lap) // 1200)]],
        })
    return output


def write_overlay_html(path: Path, laps: list[tuple[str, list[Sample]]]):
    data = json.dumps(html_payload(laps))
    body = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VN300 Lap Overlay</title>
<style>
body{{font-family:Arial,sans-serif;margin:0;background:#111;color:#eee}}
header{{padding:14px 18px;background:#1e2328;border-bottom:1px solid #333}}
main{{display:grid;grid-template-columns:1fr;gap:14px;padding:14px}}
canvas{{width:100%;height:380px;background:#171b20;border:1px solid #333;border-radius:6px}}
.legend{{display:flex;flex-wrap:wrap;gap:12px;font-size:14px}}
</style>
</head>
<body>
<header><h1>VN300 Lap Overlay</h1><div class="legend" id="legend"></div></header>
<main><canvas id="track" width="1200" height="520"></canvas><canvas id="speed" width="1200" height="520"></canvas></main>
<script>
const laps={data};
const colors=['#58a6ff','#f778ba','#56d364','#e3b341','#ff7b72','#a371f7','#39c5cf'];
function bounds(points){{let xs=[],ys=[];points.forEach(p=>{{xs.push(p[0]);ys.push(p[1])}});return [Math.min(...xs),Math.max(...xs),Math.min(...ys),Math.max(...ys)]}}
function drawPlot(id, series, xIndex, yIndex, xlabel){{const c=document.getElementById(id),ctx=c.getContext('2d');ctx.clearRect(0,0,c.width,c.height);ctx.strokeStyle='#30363d';for(let i=0;i<6;i++){{let y=30+i*(c.height-60)/5;ctx.beginPath();ctx.moveTo(50,y);ctx.lineTo(c.width-20,y);ctx.stroke()}}
let pts=series.flatMap(s=>s.points);if(!pts.length)return;let xs=pts.map(p=>p[xIndex]),ys=pts.map(p=>p[yIndex]);let minx=Math.min(...xs),maxx=Math.max(...xs),miny=Math.min(...ys),maxy=Math.max(...ys);if(maxx===minx)maxx=minx+1;if(maxy===miny)maxy=miny+1;
series.forEach((s,si)=>{{ctx.strokeStyle=colors[si%colors.length];ctx.lineWidth=2;ctx.beginPath();s.points.forEach((p,i)=>{{let x=50+(p[xIndex]-minx)/(maxx-minx)*(c.width-70);let y=c.height-30-(p[yIndex]-miny)/(maxy-miny)*(c.height-60);if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y)}});ctx.stroke()}});
ctx.fillStyle='#bbb';ctx.fillText(xlabel,55,c.height-10)}}
document.getElementById('legend').innerHTML=laps.map((l,i)=>`<span style="color:${{colors[i%colors.length]}}">${{l.name}}</span>`).join('');
drawPlot('track', laps.map(l=>({{name:l.name,points:l.xy}})), 0, 1, 'GPS position, meters');
drawPlot('speed', laps.map(l=>({{name:l.name,points:l.speed}})), 0, 1, 'Distance, meters vs speed, mph');
</script>
</body>
</html>"""
    path.write_text(body, encoding="utf-8")


def report_payload(segments: list[tuple[str, list[Sample]]]):
    reference_name = None
    reference_segment = None
    timed_segments = [(name, segment) for name, segment in segments if len(segment) >= 2 and segment_duration(segment) > 0]
    if timed_segments:
        reference_name, reference_segment = min(timed_segments, key=lambda item: segment_duration(item[1]))
    output = []
    for name, segment in segments:
        if not segment:
            continue
        d0 = segment[0].dist_m
        step = sample_step(segment)
        delta_points = []
        if reference_segment and segment is not reference_segment:
            t0 = segment[0].t
            for s in segment[::step]:
                distance_m = s.dist_m - d0
                reference_elapsed = reference_time_at_distance(reference_segment, distance_m)
                if reference_elapsed is not None:
                    delta_points.append([distance_m, (s.t - t0) - reference_elapsed])
        output.append({
            "name": name,
            "isReference": segment is reference_segment,
            "referenceName": reference_name,
            "gg": [
                [s.lateral_g or 0.0, s.longitudinal_g or 0.0, s.speed_mph]
                for s in segment[::step]
                if s.longitudinal_g is not None or s.lateral_g is not None
            ],
            "speed": [[s.dist_m - d0, s.speed_mph] for s in segment[::step]],
            "latg": [[s.dist_m - d0, s.lateral_g or 0.0] for s in segment[::step]],
            "longg": [[s.dist_m - d0, s.longitudinal_g or 0.0] for s in segment[::step]],
            "yaw": [[s.dist_m - d0, s.yaw_rate_dps] for s in segment[::step]],
            "delta": delta_points,
        })
    return output


def html_table(rows: list[dict], columns: list[str], max_rows: int = 200) -> str:
    if not rows:
        return "<p>No rows.</p>"
    head = "".join(f"<th>{html.escape(column)}</th>" for column in columns)
    body_rows = []
    for row in rows[:max_rows]:
        cells = []
        for column in columns:
            value = row.get(column, "")
            if isinstance(value, float):
                value = f"{value:.3f}"
            cells.append(f"<td>{html.escape(str(value))}</td>")
        body_rows.append(f"<tr>{''.join(cells)}</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"


def write_report_html(
    path: Path,
    summaries: list[dict],
    quality_reports: list[dict],
    segments: list[tuple[str, list[Sample]]],
    gg_prediction: dict | None = None,
):
    data = json.dumps(report_payload(segments))
    summary_columns = [
        "name", "segment_type", "segment_number", "duration_s", "distance_m",
        "max_speed_mph", "peak_lateral_g_abs", "peak_braking_g",
        "peak_accel_g", "peak_combined_g", "avg_combined_g_active",
        "gps_uncertainty_over_4m_pct", "timing_warning",
        "meta_run_number", "meta_driver", "meta_test_type", "meta_notes",
    ]
    summary_columns = [column for column in summary_columns if any(column in row for row in summaries)]
    quality_columns = [
        "source", "status", "sample_rate_hz", "zero_latlon_samples",
        "high_gps_uncertainty_samples", "gps_jump_count", "warnings", "failures",
    ]
    quality_columns = [column for column in quality_columns if any(column in row for row in quality_reports)]
    body = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VN300 Vehicle Dynamics Report</title>
<style>
body{{font-family:Arial,sans-serif;margin:0;background:#f5f7f8;color:#182026}}
header{{padding:18px 22px;background:#17212b;color:#fff}}
main{{padding:18px 22px;display:grid;gap:18px}}
h1{{font-size:24px;margin:0}}h2{{font-size:18px;margin:0 0 10px}}
section{{background:#fff;border:1px solid #d9e0e6;border-radius:6px;padding:14px;overflow:auto}}
table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{border-bottom:1px solid #e3e7eb;padding:6px 8px;text-align:left;vertical-align:top}}th{{background:#eef2f5;position:sticky;top:0}}
canvas{{width:100%;height:360px;border:1px solid #ccd4db;border-radius:4px;background:#fff}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:14px}}
.note{{color:#52606b;font-size:13px}}
</style>
</head>
<body>
<header><h1>VN300 Vehicle Dynamics Report</h1><div class="note">Generated by vn300_lap_analysis.py</div></header>
<main>
{gg_prediction_section(gg_prediction)}
<section><h2>Data Quality</h2>{html_table(quality_reports, quality_columns)}</section>
<section><h2>Summary And Metadata</h2>{html_table(summaries, summary_columns)}</section>
<section class="grid">
<div><h2>G-G Diagram</h2><canvas id="gg" width="900" height="520"></canvas></div>
<div><h2>Speed vs Distance</h2><canvas id="speed" width="900" height="520"></canvas></div>
<div><h2>Lateral G vs Distance</h2><canvas id="latg" width="900" height="520"></canvas></div>
<div><h2>Longitudinal G vs Distance</h2><canvas id="longg" width="900" height="520"></canvas></div>
<div><h2>Yaw Rate vs Distance</h2><canvas id="yaw" width="900" height="520"></canvas></div>
<div><h2>Delta To Fastest vs Distance</h2><canvas id="delta" width="900" height="520"></canvas></div>
</section>
</main>
<script>
const series={data};
const colors=['#1464a5','#c03a2b','#12805c','#8a5a00','#6f42c1','#007582','#a23b72','#4f6f00'];
function drawXY(id, getPoints, xlabel, ylabel){{const c=document.getElementById(id),ctx=c.getContext('2d');ctx.clearRect(0,0,c.width,c.height);let all=series.flatMap(getPoints);if(!all.length)return;let xs=all.map(p=>p[0]),ys=all.map(p=>p[1]);let minx=Math.min(...xs),maxx=Math.max(...xs),miny=Math.min(...ys),maxy=Math.max(...ys);if(minx===maxx)maxx=minx+1;if(miny===maxy)maxy=miny+1;let pad=46;ctx.strokeStyle='#d7dde2';ctx.lineWidth=1;for(let i=0;i<6;i++){{let x=pad+i*(c.width-pad-18)/5;let y=18+i*(c.height-pad-18)/5;ctx.beginPath();ctx.moveTo(x,18);ctx.lineTo(x,c.height-pad);ctx.stroke();ctx.beginPath();ctx.moveTo(pad,y);ctx.lineTo(c.width-18,y);ctx.stroke();}}series.forEach((s,si)=>{{let pts=getPoints(s);ctx.strokeStyle=colors[si%colors.length];ctx.fillStyle=colors[si%colors.length];ctx.lineWidth=2;ctx.beginPath();pts.forEach((p,i)=>{{let x=pad+(p[0]-minx)/(maxx-minx)*(c.width-pad-18);let y=c.height-pad-(p[1]-miny)/(maxy-miny)*(c.height-pad-18);if(id==='gg'){{ctx.fillRect(x-1.5,y-1.5,3,3)}}else if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);}});if(id!=='gg')ctx.stroke();}});ctx.fillStyle='#44515c';ctx.fillText(xlabel,pad,c.height-12);ctx.save();ctx.translate(14,c.height-pad);ctx.rotate(-Math.PI/2);ctx.fillText(ylabel,0,0);ctx.restore();}}
drawXY('gg', s=>s.gg.map(p=>[p[0],p[1]]), 'Lateral G', 'Longitudinal G');
drawXY('speed', s=>s.speed, 'Distance, m', 'Speed, mph');
drawXY('latg', s=>s.latg, 'Distance, m', 'Lateral G');
drawXY('longg', s=>s.longg, 'Distance, m', 'Longitudinal G');
drawXY('yaw', s=>s.yaw, 'Distance, m', 'Yaw rate, deg/s');
drawXY('delta', s=>s.delta||[], 'Distance, m', 'Delta to fastest, s');
</script>
</body>
</html>"""
    path.write_text(body, encoding="utf-8")


def prompt_choice(prompt: str, choices: set[str]) -> str:
    while True:
        value = input(prompt).strip().lower()
        if value in choices:
            return value
        print(f"Enter one of: {', '.join(sorted(choices))}")


def prompt_float(prompt: str) -> float:
    while True:
        try:
            return float(input(prompt).strip())
        except ValueError:
            print("Enter a number.")


def line_from_args(args, prefix: str):
    values = [
        getattr(args, f"{prefix}_lat1"),
        getattr(args, f"{prefix}_lon1"),
        getattr(args, f"{prefix}_lat2"),
        getattr(args, f"{prefix}_lon2"),
    ]
    if None in values:
        return None
    return tuple(values)


def prompt_line(label: str):
    print(f"{label} line endpoints:")
    return (
        prompt_float("  lat 1: "),
        prompt_float("  lon 1: "),
        prompt_float("  lat 2: "),
        prompt_float("  lon 2: "),
    )


def session_key(path: Path):
    stem = path.stem
    for suffix in ("_BINARY", "_VNINS"):
        if stem.endswith(suffix):
            stem = stem[:-len(suffix)]
            break
    return path.parent.resolve(), stem


def session_stem_from_name(name: str) -> str:
    stem = Path(name).stem
    for suffix in ("_BINARY", "_VNINS", "_VNIMU"):
        if stem.endswith(suffix):
            return stem[:-len(suffix)]
    return stem


def metadata_keys_for_path(path: Path) -> set[str]:
    return {
        path.name.lower(),
        path.stem.lower(),
        session_stem_from_name(path.name).lower(),
        str(path).lower(),
        str(path.resolve()).lower(),
    }


def load_metadata(path: Path | None) -> tuple[dict[str, dict], list[str]]:
    if path is None:
        return {}, []
    if not path.exists():
        raise SystemExit(f"Metadata file not found: {path}")
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])

    key_columns = [
        "session_file", "source", "file", "filename", "log_file",
        "session", "session_name", "run_file",
    ]
    metadata = {}
    for row in rows:
        keys = set()
        for column in key_columns:
            value = (row.get(column) or "").strip()
            if value:
                keys.add(value.lower())
                keys.add(Path(value).name.lower())
                keys.add(Path(value).stem.lower())
                keys.add(session_stem_from_name(value).lower())
        if not keys:
            continue
        cleaned = {f"meta_{key}": value for key, value in row.items() if key}
        for key in keys:
            metadata[key] = cleaned
    return metadata, [f"meta_{name}" for name in fieldnames]


def metadata_for_path(metadata: dict[str, dict], path: Path) -> dict:
    for key in metadata_keys_for_path(path):
        if key in metadata:
            return dict(metadata[key])
    return {}


def load_driver_map(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    if not path.exists():
        raise SystemExit(f"Driver map file not found: {path}")
    mapping = {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            driver = (row.get("driver") or row.get("Driver") or "").strip()
            if not driver:
                continue
            for column in ("run_id", "session_file", "file", "filename", "session", "source"):
                value = (row.get(column) or "").strip()
                if not value:
                    continue
                mapping[value.lower()] = driver
                mapping[Path(value).name.lower()] = driver
                mapping[Path(value).stem.lower()] = driver
                mapping[session_stem_from_name(value).lower()] = driver
    return mapping


def driver_override_for_path(
    path: Path,
    path_index: int,
    driver_map: dict[str, str],
    driver_order: list[str],
    driver_order_offset: int,
) -> str | None:
    keys = metadata_keys_for_path(path)
    keys.update({
        path.name.lower(),
        path.stem.lower(),
        session_stem_from_name(path.name).lower(),
    })
    for key in keys:
        if key in driver_map:
            return driver_map[key]

    order_index = path_index - driver_order_offset
    if 0 <= order_index < len(driver_order):
        return driver_order[order_index]
    return None


def expand_input_paths(paths: list[Path], include_ascii: bool = False) -> list[Path]:
    csv_paths = []
    for path in paths:
        if path.is_dir():
            csv_paths.extend(sorted(path.rglob("*_VNINS.csv")))
            csv_paths.extend(sorted(path.rglob("*_BINARY.csv")))
        elif path.is_file():
            csv_paths.append(path)
    unique = []
    seen = set()
    for path in csv_paths:
        resolved = path.resolve()
        if resolved not in seen:
            unique.append(path)
            seen.add(resolved)

    if include_ascii:
        return unique

    grouped = {}
    passthrough = []
    for path in unique:
        if path.name.endswith("_BINARY.csv") or path.name.endswith("_VNINS.csv"):
            entry = grouped.setdefault(session_key(path), {"binary": [], "ascii": []})
            if path.name.endswith("_BINARY.csv"):
                entry["binary"].append(path)
            else:
                entry["ascii"].append(path)
        else:
            passthrough.append(path)

    selected = list(passthrough)
    for entry in grouped.values():
        selected.extend(sorted(entry["binary"] or entry["ascii"]))
    return sorted(selected, key=lambda p: str(p).lower())


def latest_dashboard_config(paths: list[Path]):
    config_paths = []
    for path in paths:
        if path.is_dir():
            config_paths.extend(path.rglob("VN300_dashboard_timing_config.csv"))
        elif path.name == "VN300_dashboard_timing_config.csv":
            config_paths.append(path)
        elif path.is_file():
            candidate = path.parent / "VN300_dashboard_timing_config.csv"
            if candidate.exists():
                config_paths.append(candidate)

    config_paths = sorted(set(config_paths), key=lambda p: p.stat().st_mtime)
    if not config_paths:
        return None

    latest_row = None
    latest_path = config_paths[-1]
    with latest_path.open(newline="") as f:
        for row in csv.DictReader(f):
            latest_row = row
    if not latest_row:
        return None

    def line(prefix: str):
        lat1 = latest_row.get(f"{prefix}_Lat_1")
        lon1 = latest_row.get(f"{prefix}_Lon_1")
        lat2 = latest_row.get(f"{prefix}_Lat_2")
        lon2 = latest_row.get(f"{prefix}_Lon_2")
        if "" in (lat1, lon1, lat2, lon2) or None in (lat1, lon1, lat2, lon2):
            return None
        return (float(lat1), float(lon1), float(lat2), float(lon2))

    return {
        "path": latest_path,
        "mode": latest_row.get("Mode", "").lower() or None,
        "start_line": line("Start"),
        "finish_line": line("Finish"),
        "min_speed_mph": float(latest_row.get("Min_Speed_mph") or 5.0),
        "min_gap_s": float(latest_row.get("Min_Gap_s") or 20.0),
    }


def latest_run_metadata_path(paths: list[Path]):
    metadata_paths = []
    for path in paths:
        if path.is_dir():
            metadata_paths.extend(path.rglob("VN300_run_metadata.csv"))
        elif path.name == "VN300_run_metadata.csv":
            metadata_paths.append(path)
        elif path.is_file():
            candidate = path.parent / "VN300_run_metadata.csv"
            if candidate.exists():
                metadata_paths.append(candidate)
    metadata_paths = sorted(set(metadata_paths), key=lambda p: p.stat().st_mtime)
    return metadata_paths[-1] if metadata_paths else None


def segment_duration(segment: list[Sample]) -> float:
    return segment[-1].t - segment[0].t if segment else 0.0


def laps_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_files", nargs="+", type=Path, help="VNINS/BINARY CSV files or folders containing logger CSV files")
    parser.add_argument("--out", type=Path, default=Path("vn300_analysis_output"))
    parser.add_argument("--mode", choices=("lap", "autocross"))
    parser.add_argument("--start-lat1", type=float)
    parser.add_argument("--start-lon1", type=float)
    parser.add_argument("--start-lat2", type=float)
    parser.add_argument("--start-lon2", type=float)
    parser.add_argument("--finish-lat1", type=float)
    parser.add_argument("--finish-lon1", type=float)
    parser.add_argument("--finish-lat2", type=float)
    parser.add_argument("--finish-lon2", type=float)
    parser.add_argument("--line-lat1", type=float)
    parser.add_argument("--line-lon1", type=float)
    parser.add_argument("--line-lat2", type=float)
    parser.add_argument("--line-lon2", type=float)
    parser.add_argument("--min-speed-mph", type=float, default=5.0)
    parser.add_argument("--min-lap-seconds", type=float, default=20.0, help="minimum time between repeated line crossings")
    parser.add_argument("--no-prompts", action="store_true", help="only summarize if timing setup arguments are incomplete")
    parser.add_argument("--include-ascii", action="store_true", help="include *_VNINS.csv even when a matching *_BINARY.csv exists")
    parser.add_argument("--metadata", type=Path, help="optional CSV metadata/run sheet to merge into summaries and reports")
    parser.add_argument("--driver-map", type=Path, help="optional CSV with run_id/session_file and driver columns")
    parser.add_argument("--driver-order", help="comma-separated driver names mapped onto sorted input files")
    parser.add_argument("--driver-order-offset", type=int, default=0, help="number of sorted input files to skip before applying --driver-order")
    parser.add_argument("--auto-sectors", type=int, default=3, help="automatically split each timed lap/run into this many sectors; use 0 to disable")
    parser.add_argument("--sector-report-min-seconds", type=float, default=20.0, help="minimum timed segment duration to include in lap sector/theoretical-best output")
    parser.add_argument("--no-gg-lap-prediction", action="store_true", help="disable the measured car G-G same-line minimum-lap prediction")
    parser.add_argument("--gg-envelope-percentile", type=float, default=0.98, help="directional car G-G percentile used by the lap predictor")
    parser.add_argument("--gg-angle-bin-deg", type=int, default=10, help="directional G-G envelope bin width; must divide 360")
    parser.add_argument("--gg-min-bin-points", type=int, default=12, help="minimum samples per driver and direction for the car G-G envelope")
    parser.add_argument("--gg-speed-max-mph", type=float, default=60.0, help="maximum telemetry speed admitted to the car G-G envelope and reference-lap check")
    parser.add_argument("--gg-gps-uncertainty-max-m", type=float, default=4.0, help="maximum GPS position uncertainty admitted to the car G-G predictor")
    parser.add_argument("--gg-predictor-max-speed-mph", type=float, default=80.0, help="hard speed cap for the car G-G lap predictor")
    parser.add_argument("--gg-predictor-grid-step-m", type=float, default=0.5, help="distance step for the car G-G lap solver")
    parser.add_argument("--gg-predictor-curvature-smoothing-m", type=float, default=2.5, help="curvature smoothing distance for the car G-G lap solver")
    return parser


def laps_main():
    args = laps_parser().parse_args()

    if not (0.0 < args.gg_envelope_percentile <= 1.0):
        raise SystemExit("--gg-envelope-percentile must be in the range (0, 1].")
    if args.gg_angle_bin_deg <= 0 or 360 % args.gg_angle_bin_deg != 0:
        raise SystemExit("--gg-angle-bin-deg must be a positive integer that divides 360.")
    if args.gg_min_bin_points < 1:
        raise SystemExit("--gg-min-bin-points must be positive.")
    if args.gg_speed_max_mph <= args.min_speed_mph or args.gg_predictor_max_speed_mph <= 0.0:
        raise SystemExit("G-G maximum telemetry and predictor speeds must be valid positive ranges.")
    if args.gg_gps_uncertainty_max_m <= 0.0:
        raise SystemExit("--gg-gps-uncertainty-max-m must be positive.")
    if args.gg_predictor_grid_step_m <= 0.0 or args.gg_predictor_curvature_smoothing_m <= 0.0:
        raise SystemExit("G-G predictor grid step and curvature smoothing must be positive.")

    args.out.mkdir(parents=True, exist_ok=True)
    csv_paths = expand_input_paths(args.csv_files, include_ascii=args.include_ascii)
    if not csv_paths:
        raise SystemExit("No *_VNINS.csv or *_BINARY.csv files found in the selected input path(s).")
    metadata_path = args.metadata or latest_run_metadata_path(args.csv_files)
    metadata, _metadata_columns = load_metadata(metadata_path)
    if metadata_path:
        print(f"Loaded metadata: {metadata_path}")
    driver_map = load_driver_map(args.driver_map)
    driver_order = [item.strip() for item in (args.driver_order or "").split(",") if item.strip()]
    mode = args.mode
    start_line = line_from_args(args, "start")
    finish_line = line_from_args(args, "finish")

    dashboard_config = latest_dashboard_config(args.csv_files)
    if dashboard_config:
        if mode is None:
            mode = dashboard_config["mode"]
        if start_line is None:
            start_line = dashboard_config["start_line"]
        if finish_line is None:
            finish_line = dashboard_config["finish_line"]
        if args.min_speed_mph == 5.0:
            args.min_speed_mph = dashboard_config["min_speed_mph"]
        if args.min_lap_seconds == 20.0:
            args.min_lap_seconds = dashboard_config["min_gap_s"]
        print(f"Loaded dashboard timing config: {dashboard_config['path']}")

    legacy_line = None
    if None not in (args.line_lat1, args.line_lon1, args.line_lat2, args.line_lon2):
        legacy_line = (args.line_lat1, args.line_lon1, args.line_lat2, args.line_lon2)
        start_line = start_line or legacy_line
        mode = mode or "lap"

    if not args.no_prompts and mode is None:
        mode = prompt_choice("Track type [lap/autocross]: ", {"lap", "autocross"})
    if not args.no_prompts and mode and start_line is None:
        start_line = prompt_line("Start/finish" if mode == "lap" else "Start")
    if not args.no_prompts and mode == "autocross" and finish_line is None:
        finish_line = prompt_line("Finish")

    timing_ready = mode == "lap" and start_line is not None
    timing_ready = timing_ready or (mode == "autocross" and start_line is not None and finish_line is not None)

    summaries = []
    quality_reports = []
    sector_summaries = []
    lap_sector_rows = []
    lap_sector_excluded_rows = []
    overlay_laps = []
    report_segments = []
    best_segment = None
    best_duration = None
    segment_count = 0
    gg_prediction_segments = []
    for path_index, path in enumerate(csv_paths):
        fieldnames = inspect_csv_fieldnames(path)
        samples = load_vnins(path)
        quality = data_quality_report(path, samples, fieldnames)
        quality_reports.append(quality)
        if not samples:
            print(f"{path}: no valid samples")
            continue

        path_metadata = metadata_for_path(metadata, path)
        driver_override = driver_override_for_path(
            path,
            path_index,
            driver_map,
            driver_order,
            args.driver_order_offset,
        )
        if driver_override:
            path_metadata["meta_driver"] = driver_override
        run_summary = add_analysis_fields(summarize_segment(path.stem, samples), samples)
        run_summary.update(path_metadata)
        run_summary["data_quality_status"] = quality.get("status", "")
        summaries.append(run_summary)
        print(f"{path.name}: {len(samples)} samples, {run_summary['duration_s']:.1f}s, max {run_summary['max_speed_mph']:.1f} mph")

        if not timing_ready:
            overlay_laps.append((path.stem, samples))
            report_segments.append((path.stem, samples))
            continue

        if mode == "lap":
            crossings = crossing_indices(samples, start_line, args.min_speed_mph / 2.2369362921, args.min_lap_seconds)
            segments = split_laps(samples, crossings)
            segment_type = "lap"
            print(f"  crossings={len(crossings)} laps={len(segments)}")
        else:
            start_crossings = crossing_indices(samples, start_line, args.min_speed_mph / 2.2369362921, args.min_lap_seconds)
            finish_crossings = crossing_indices(samples, finish_line, args.min_speed_mph / 2.2369362921, args.min_lap_seconds)
            segments = split_autocross_runs(samples, start_crossings, finish_crossings)
            segment_type = "run"
            print(f"  start_crossings={len(start_crossings)} finish_crossings={len(finish_crossings)} runs={len(segments)}")

        for local_segment_number, segment_info in enumerate(segments, start=1):
            segment = segment_info["samples"]
            timing_warning = segment_info.get("warning", "")
            segment_count += 1
            duration = segment_duration(segment)
            delta = None if best_duration is None else duration - best_duration
            live_deltas = live_deltas_to_reference(segment, best_segment)
            valid_timing = not timing_warning
            name = f"{path.stem}_{segment_type}_{segment_count:02d}"
            summary = add_analysis_fields(summarize_segment(name, segment), segment)
            summary["segment_type"] = segment_type
            summary["segment_number"] = segment_count
            summary["source_segment_number"] = local_segment_number
            summary["delta_to_best_s"] = delta
            summary["best_after_segment_s"] = (
                duration if valid_timing and (best_duration is None or duration < best_duration) else best_duration
            )
            summary["is_new_best"] = valid_timing and (best_duration is None or duration < best_duration)
            summary["timing_warning"] = timing_warning
            summary["data_quality_status"] = quality.get("status", "")
            summary.update(path_metadata)
            summaries.append(summary)
            write_lap_csv(args.out / f"{name}.csv", segment, segment_type, segment_count, delta, live_deltas, timing_warning)
            if duration >= args.sector_report_min_seconds:
                append_lap_sector_split(
                    lap_sector_rows,
                    name,
                    segment,
                    segment_type,
                    segment_count,
                    local_segment_number,
                    timing_warning,
                    path_metadata,
                )
                gg_usable_fraction = usable_sample_fraction(
                    segment,
                    args.min_speed_mph,
                    args.gg_speed_max_mph,
                    args.gg_gps_uncertainty_max_m,
                )
                sane_speed_fraction = sum(
                    1 for sample in segment
                    if math.isfinite(sample.speed_mph) and 0.0 <= sample.speed_mph <= args.gg_speed_max_mph
                ) / len(segment)
                if (
                    valid_timing
                    and mode == "lap"
                    and gg_usable_fraction >= 0.75
                    and sane_speed_fraction >= 0.98
                ):
                    gg_prediction_segments.append({
                        "name": name,
                        "display_name": f"{session_stem_from_name(path.name)} lap {local_segment_number}",
                        "local_lap_number": local_segment_number,
                        "driver": path_metadata.get("meta_driver", "") or "Unknown",
                        "duration_s": duration,
                        "gg_usable_fraction": gg_usable_fraction,
                        "sane_speed_fraction": sane_speed_fraction,
                        "samples": segment,
                    })
            else:
                lap_sector_excluded_rows.append({
                    "name": name,
                    "source": path.name,
                    "driver": path_metadata.get("meta_driver", ""),
                    "segment_type": segment_type,
                    "segment_number": segment_count,
                    "source_segment_number": local_segment_number,
                    "duration_s": duration,
                    "reason": f"below sector report minimum {args.sector_report_min_seconds:.1f} s",
                })
            overlay_laps.append((name, segment))
            report_segments.append((name, segment))
            if args.auto_sectors >= 2:
                auto_sectors = split_auto_time_sectors(segment, args.auto_sectors)
                append_sector_summaries(
                    sector_summaries,
                    auto_sectors,
                    name,
                    segment_type,
                    segment_count,
                    quality,
                    path_metadata,
                )

            if valid_timing and (best_duration is None or duration < best_duration):
                best_duration = duration
                best_segment = segment

    gg_prediction = None
    if not args.no_gg_lap_prediction and mode == "lap" and gg_prediction_segments:
        try:
            consistent_laps, course_distance_center = select_consistent_lap_records(
                gg_prediction_segments,
                args.gg_speed_max_mph,
            )
            reference_record = min(consistent_laps, key=lambda row: row["duration_s"])
            gg_prediction = predict_car_gg_lap(
                consistent_laps,
                reference_record,
                envelope_percentile=args.gg_envelope_percentile,
                angle_bin_deg=args.gg_angle_bin_deg,
                min_bin_points=args.gg_min_bin_points,
                speed_min_mph=args.min_speed_mph,
                speed_max_mph=args.gg_speed_max_mph,
                gps_limit_m=args.gg_gps_uncertainty_max_m,
                grid_step_m=args.gg_predictor_grid_step_m,
                smoothing_m=args.gg_predictor_curvature_smoothing_m,
                maximum_speed_mph=args.gg_predictor_max_speed_mph,
            )
            gg_prediction["summary"]["course_consistent_laps"] = len(consistent_laps)
            gg_prediction["summary"]["course_distance_cluster_center_m"] = course_distance_center
            valid_sector_rows, _ = credible_sector_rows(lap_sector_rows)
            if valid_sector_rows:
                sector_composite = sum(
                    min(row[f"sector_{sector_number}_s"] for row in valid_sector_rows)
                    for sector_number in range(1, 4)
                )
                gg_prediction["summary"]["observed_sector_composite_s"] = sector_composite
                gg_prediction["summary"]["gg_prediction_vs_sector_composite_s"] = (
                    gg_prediction["summary"]["predicted_lap_time_s"] - sector_composite
                )
            write_summary(args.out / "car_gg_lap_prediction.csv", [gg_prediction["summary"]])
            write_summary(args.out / "car_gg_envelope.csv", gg_prediction["car_envelope"])
            write_summary(args.out / "car_gg_driver_envelopes.csv", gg_prediction["driver_envelopes"])
            write_summary(args.out / "car_gg_propulsion_envelope.csv", gg_prediction["propulsion_envelope"])
            write_summary(args.out / "car_gg_lap_trace.csv", gg_prediction["trace"])
        except ValueError as error:
            print(f"Car G-G lap prediction skipped: {error}")

    write_summary(args.out / "summary.csv", summaries)
    write_summary(args.out / "data_quality.csv", quality_reports)
    if sector_summaries:
        write_sector_summary(args.out / "sector_summary.csv", sector_summaries)
    write_lap_sector_outputs(args.out, lap_sector_rows, lap_sector_excluded_rows, gg_prediction=gg_prediction)
    write_overlay_html(args.out / "overlay.html", overlay_laps)
    write_report_html(args.out / "report.html", summaries, quality_reports, report_segments, gg_prediction)
    print(f"Wrote {args.out / 'summary.csv'}")
    print(f"Wrote {args.out / 'data_quality.csv'}")
    if sector_summaries:
        print(f"Wrote {args.out / 'sector_summary.csv'}")
    if (args.out / "lap_times_sector_splits.html").exists():
        print(f"Wrote {args.out / 'lap_times_sector_splits.html'}")
        print(f"Wrote {args.out / 'lap_sector_splits.csv'}")
        print(f"Wrote {args.out / 'theoretical_best_by_driver.csv'}")
        print(f"Wrote {args.out / 'overall_best_sectors.csv'}")
    if gg_prediction:
        print(
            f"Car G-G mathematical best: {gg_prediction['summary']['predicted_lap_time_s']:.3f} s "
            f"on {gg_prediction['summary']['reference_name']}"
        )
        print(f"Wrote {args.out / 'car_gg_lap_prediction.csv'}")
        print(f"Wrote {args.out / 'car_gg_envelope.csv'}")
        print(f"Wrote {args.out / 'car_gg_lap_trace.csv'}")
    print(f"Wrote {args.out / 'overlay.html'}")
    print(f"Wrote {args.out / 'report.html'}")


# Vn300 Gg Analysis
#!/usr/bin/env python3






DEFAULT_SPEED_MIN_MPH = 5.0
DEFAULT_SPEED_MAX_MPH = 60.0
DEFAULT_GPS_UNCERTAINTY_MAX_M = 4.0
DEFAULT_ABS_G_HARD_LIMIT = 2.8
DEFAULT_COMBINED_G_HARD_LIMIT = 3.0
DEFAULT_ENVELOPE_PERCENTILE = 0.98
DEFAULT_BIN_DEG = 10
DEFAULT_MIN_BIN_POINTS = 12
DEFAULT_MAX_PLOT_POINTS = 22000
DEFAULT_MIN_DRIVER_POINTS = 100


def gg_parse_float(row: dict, name: str) -> float | None:
    try:
        value = row.get(name, "")
        if value in ("", None):
            return None
        numeric = float(value)
        if not math.isfinite(numeric):
            return None
        return numeric
    except (TypeError, ValueError):
        return None


def gg_percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    if len(values) == 1:
        return values[0]
    index = (len(values) - 1) * p
    low = math.floor(index)
    high = math.ceil(index)
    if low == high:
        return values[low]
    return values[low] + (values[high] - values[low]) * (index - low)


def gg_decimate(points: list[tuple], max_points: int) -> tuple[list[tuple], int]:
    if len(points) <= max_points:
        return points, 1
    step = math.ceil(len(points) / max_points)
    return points[::step], step


def gg_load_driver_map(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    if not path.exists():
        raise SystemExit(f"Driver map not found: {path}")
    mapping = {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            driver = (row.get("driver") or row.get("Driver") or "").strip()
            if not driver:
                continue
            keys = [
                row.get("run_id"),
                row.get("session_file"),
                row.get("file"),
                row.get("filename"),
                row.get("session"),
            ]
            for value in keys:
                value = (value or "").strip()
                if not value:
                    continue
                mapping[value.lower()] = driver
                mapping[Path(value).name.lower()] = driver
                mapping[Path(value).stem.lower()] = driver
                mapping[session_stem_from_name(value).lower()] = driver
    return mapping


def gg_driver_from_sources(
    path: Path,
    index: int,
    metadata: dict[str, dict],
    driver_map: dict[str, str],
    driver_order: list[str],
    driver_order_offset: int,
) -> str:
    keys = {
        path.name.lower(),
        path.stem.lower(),
        session_stem_from_name(path.name).lower(),
    }
    for key in keys:
        if key in driver_map:
            return driver_map[key]

    order_index = index - driver_order_offset
    if 0 <= order_index < len(driver_order):
        return driver_order[order_index]

    meta = metadata_for_path(metadata, path)
    driver = (meta.get("meta_driver") or "").strip()
    if driver:
        return driver

    return session_stem_from_name(path.name)


def gg_point_allowed(row: dict, args: argparse.Namespace) -> tuple[bool, str, tuple | None]:
    lateral_g = gg_parse_float(row, "Lateral_G")
    longitudinal_g = gg_parse_float(row, "Longitudinal_G")
    speed_mph = gg_parse_float(row, "Speed_mph")
    pos_uncertainty_m = gg_parse_float(row, "PosUncertainty_m")
    if lateral_g is None or longitudinal_g is None or speed_mph is None or pos_uncertainty_m is None:
        return False, "missing", None
    if speed_mph < args.speed_min_mph or speed_mph > args.speed_max_mph:
        return False, "speed", None
    if pos_uncertainty_m < 0 or pos_uncertainty_m > args.gps_uncertainty_max_m:
        return False, "gps_uncertainty", None

    combined_g = math.hypot(lateral_g, longitudinal_g)
    if (
        abs(lateral_g) > args.abs_g_hard_limit
        or abs(longitudinal_g) > args.abs_g_hard_limit
        or combined_g > args.combined_g_hard_limit
    ):
        return False, "g_outlier", None

    angle_deg = (math.degrees(math.atan2(longitudinal_g, lateral_g)) + 360.0) % 360.0
    return True, "", (lateral_g, longitudinal_g, speed_mph, combined_g, angle_deg)


def gg_build_envelope(
    points: list[tuple],
    driver: str,
    run_ids: list[str],
    args: argparse.Namespace,
) -> tuple[list[tuple], list[dict]]:
    bin_count = int(360 / args.angle_bin_deg)
    bins = {i: [] for i in range(bin_count)}
    for point in points:
        bin_id = int(point[4] // args.angle_bin_deg) % bin_count
        bins[bin_id].append(point)

    envelope = []
    rows = []
    for bin_id in range(bin_count):
        bin_points = bins[bin_id]
        angle_center = bin_id * args.angle_bin_deg + args.angle_bin_deg / 2.0
        row = {
            "driver": driver,
            "run_ids": ";".join(run_ids),
            "angle_center_deg": f"{angle_center:.1f}",
            "samples": len(bin_points),
            "percentile": args.envelope_percentile,
            "p_combined_g": "",
            "lateral_g": "",
            "longitudinal_g": "",
            "speed_mph": "",
            "used": "no_not_enough_samples",
        }
        if len(bin_points) >= args.min_bin_points:
            radius = gg_percentile([point[3] for point in bin_points], args.envelope_percentile)
            if radius is not None:
                chosen = min(bin_points, key=lambda point: abs(point[3] - radius))
                envelope.append(chosen)
                row.update({
                    "p_combined_g": f"{radius:.5f}",
                    "lateral_g": f"{chosen[0]:.5f}",
                    "longitudinal_g": f"{chosen[1]:.5f}",
                    "speed_mph": f"{chosen[2]:.2f}",
                    "used": "yes",
                })
        rows.append(row)
    return envelope, rows


def gg_html_table(rows: list[dict], columns: list[str]) -> str:
    head = "".join(f"<th>{html.escape(column)}</th>" for column in columns)
    body = []
    for row in rows:
        cells = "".join(f"<td>{html.escape(str(row.get(column, '')))}</td>" for column in columns)
        body.append(f"<tr>{cells}</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def gg_write_gg_html(path: Path, data: list[dict], summary_rows: list[dict], args: argparse.Namespace):
    summary_columns = [
        "driver", "run_ids", "kept_rows", "p_envelope_peak_combined_g",
        "peak_combined_g_raw_retained", "max_brake_g", "max_accel_g",
        "max_left_g", "max_right_g", "excluded_gps_uncertainty",
    ]
    body = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VN300 Corrected G-G Diagrams</title>
<style>
body{{margin:0;background:#f5f7f9;color:#17202a;font-family:Arial,Helvetica,sans-serif}}
header{{padding:16px 20px;background:#17202a;color:#eef3f7;border-bottom:1px solid #2f3a46}}
main{{padding:16px;display:grid;gap:16px}}
.plots{{display:grid;grid-template-columns:repeat(auto-fit,minmax(450px,1fr));gap:16px}}
.panel,section{{background:#fff;border:1px solid #cfd8e3;border-radius:6px;padding:12px;overflow:auto}}
canvas{{width:100%;height:390px;background:#fbfcfd;border:1px solid #cad4df}}
table{{border-collapse:collapse;font-size:13px;width:100%;margin-top:8px}}td,th{{border-bottom:1px solid #dde5ed;padding:5px;text-align:right}}td:first-child,th:first-child{{text-align:left}}
.small{{color:#b9c6d3;font-size:13px}}.note{{color:#5c6b78;font-size:13px;line-height:1.35}}h1{{margin:0 0 6px}}h2{{margin:0 0 8px;font-size:18px}}
.legend{{display:flex;gap:14px;align-items:center;flex-wrap:wrap;margin:8px 0;color:#5c6b78;font-size:13px}}
.sw{{width:20px;height:3px;display:inline-block;vertical-align:middle;margin-right:5px}}
</style>
</head>
<body>
<header>
<h1>VN300 Corrected G-G Diagrams</h1>
<div class="small">Equal-axis lateral vs longitudinal acceleration. Scatter is cleaned telemetry; outer shape is the {args.envelope_percentile:.0%} directional envelope.</div>
</header>
<main>
<section><h2>Summary</h2>{gg_html_table(summary_rows, summary_columns)}</section>
<div class="plots" id="plots"></div>
</main>
<script>
const data = {json.dumps(data)};
function draw(canvas, d){{
  const ctx = canvas.getContext('2d');
  const w = canvas.width, h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  const pad = 48;
  let maxAbs = 1.5;
  for (const p of d.points) maxAbs = Math.max(maxAbs, Math.abs(p[0]), Math.abs(p[1]));
  for (const p of d.envelope) maxAbs = Math.max(maxAbs, Math.abs(p[0]), Math.abs(p[1]));
  const lim = Math.min({args.combined_g_hard_limit:.3f}, Math.ceil((maxAbs + 0.18) * 2) / 2);
  const min = -lim, max = lim;
  const sx = x => pad + (x - min) / (max - min) * (w - 2 * pad);
  const sy = y => h - pad - (y - min) / (max - min) * (h - 2 * pad);
  ctx.fillStyle = '#fbfcfd'; ctx.fillRect(0, 0, w, h);
  ctx.strokeStyle = '#e2e8ef'; ctx.lineWidth = 1; ctx.font = '12px Arial'; ctx.fillStyle = '#637282';
  for (let g = Math.ceil(min * 2) / 2; g <= max + 1e-9; g += 0.5) {{
    ctx.beginPath(); ctx.moveTo(sx(g), sy(min)); ctx.lineTo(sx(g), sy(max)); ctx.moveTo(sx(min), sy(g)); ctx.lineTo(sx(max), sy(g)); ctx.stroke();
    if (Math.abs(g) > 1e-9) {{ ctx.fillText(g.toFixed(1), sx(g)-10, sy(0)+16); ctx.fillText(g.toFixed(1), sx(0)+8, sy(g)+4); }}
  }}
  ctx.strokeStyle = '#7b8794'; ctx.lineWidth = 1.4;
  ctx.beginPath(); ctx.moveTo(sx(min), sy(0)); ctx.lineTo(sx(max), sy(0)); ctx.moveTo(sx(0), sy(min)); ctx.lineTo(sx(0), sy(max)); ctx.stroke();
  ctx.globalAlpha = 0.17; ctx.fillStyle = d.color;
  for (const p of d.points) ctx.fillRect(sx(p[0]), sy(p[1]), 2, 2);
  ctx.globalAlpha = 1;
  if (d.envelope.length > 2) {{
    const env = d.envelope.slice().sort((a,b)=>Math.atan2(a[1],a[0])-Math.atan2(b[1],b[0]));
    ctx.strokeStyle = d.color; ctx.fillStyle = d.color; ctx.lineWidth = 3;
    ctx.beginPath();
    env.forEach((p,i)=>{{ if(i===0) ctx.moveTo(sx(p[0]), sy(p[1])); else ctx.lineTo(sx(p[0]), sy(p[1])); }});
    ctx.closePath(); ctx.stroke();
    for (const p of env) {{ ctx.beginPath(); ctx.arc(sx(p[0]), sy(p[1]), 3.2, 0, Math.PI*2); ctx.fill(); }}
  }}
  ctx.fillStyle = '#17202a'; ctx.font = '13px Arial';
  ctx.fillText('Lateral G', w/2 - 28, h - 13);
  ctx.save(); ctx.translate(15, h/2 + 43); ctx.rotate(-Math.PI/2); ctx.fillText('Longitudinal G', 0, 0); ctx.restore();
  ctx.fillStyle = '#637282'; ctx.font = '12px Arial';
  ctx.fillText('braking', sx(min)+4, sy(-lim)+14); ctx.fillText('accel', sx(min)+4, sy(lim)+14);
}}
const plots = document.getElementById('plots');
for (const d of data) {{
  const s = d.summary;
  const div = document.createElement('section');
  div.className = 'panel';
  div.innerHTML = `<h2>${{d.driver}}</h2>
  <canvas width="800" height="480"></canvas>
  <div class="legend"><span><span class="sw" style="background:${{d.color}}"></span>${{Math.round(d.envelopePercentile*100)}}th percentile envelope</span><span>faint dots = cleaned samples</span></div>
  <table><tr><th>Metric</th><th>Value</th></tr>
  <tr><td>Envelope peak</td><td>${{Number(s.p_envelope_peak_combined_g).toFixed(3)}} g</td></tr>
  <tr><td>Peak retained sample</td><td>${{Number(s.peak_combined_g_raw_retained).toFixed(3)}} g</td></tr>
  <tr><td>Max braking</td><td>${{Number(s.max_brake_g).toFixed(3)}} g</td></tr>
  <tr><td>Max accel</td><td>${{Number(s.max_accel_g).toFixed(3)}} g</td></tr>
  <tr><td>Left / right lateral</td><td>${{Number(s.max_left_g).toFixed(3)}} / ${{Number(s.max_right_g).toFixed(3)}} g</td></tr>
  <tr><td>Kept samples</td><td>${{s.kept_rows}}</td></tr>
  <tr><td>GPS uncertainty excluded</td><td>${{s.excluded_gps_uncertainty}}</td></tr></table>`;
  plots.appendChild(div);
  draw(div.querySelector('canvas'), d);
}}
</script>
</body>
</html>"""
    path.write_text(body, encoding="utf-8")


def gg_run_analysis(args: argparse.Namespace):
    args.out.mkdir(parents=True, exist_ok=True)
    csv_paths = expand_input_paths(args.csv_files, include_ascii=args.include_ascii)
    if not csv_paths:
        raise SystemExit("No *_VNINS.csv or *_BINARY.csv files found in the selected input path(s).")

    metadata_path = args.metadata or latest_run_metadata_path(args.csv_files)
    metadata, _metadata_columns = load_metadata(metadata_path)
    if metadata_path:
        print(f"Loaded metadata: {metadata_path}")

    driver_map = gg_load_driver_map(args.driver_map)
    driver_order = [item.strip() for item in (args.driver_order or "").split(",") if item.strip()]

    grouped = {}
    excluded_by_driver = {}
    colors = ["#48cae4", "#f72585", "#80ed99", "#ffd166", "#bdb2ff", "#ff9f1c", "#06d6a0", "#3a86ff", "#ef476f"]

    for index, path in enumerate(csv_paths):
        driver = gg_driver_from_sources(path, index, metadata, driver_map, driver_order, args.driver_order_offset)
        group = grouped.setdefault(driver, {"run_ids": [], "points": [], "raw_rows": 0})
        run_id = session_stem_from_name(path.name)
        if run_id not in group["run_ids"]:
            group["run_ids"].append(run_id)

        excluded = excluded_by_driver.setdefault(driver, {
            "excluded_missing": 0,
            "excluded_speed": 0,
            "excluded_gps_uncertainty": 0,
            "excluded_hard_g_outlier": 0,
        })

        with path.open(newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                group["raw_rows"] += 1
                allowed, reason, point = gg_point_allowed(row, args)
                if allowed and point is not None:
                    group["points"].append(point)
                elif reason == "missing":
                    excluded["excluded_missing"] += 1
                elif reason == "speed":
                    excluded["excluded_speed"] += 1
                elif reason == "gps_uncertainty":
                    excluded["excluded_gps_uncertainty"] += 1
                elif reason == "g_outlier":
                    excluded["excluded_hard_g_outlier"] += 1
        print(f"{path.name}: driver={driver}, raw={group['raw_rows']}, kept={len(group['points'])}")

    summary_rows = []
    envelope_rows = []
    html_data = []
    for color_index, (driver, group) in enumerate(grouped.items()):
        points = group["points"]
        if len(points) < args.min_driver_points:
            print(f"Skipping {driver}: only {len(points)} retained points")
            continue
        run_ids = group["run_ids"]
        envelope, driver_envelope_rows = gg_build_envelope(points, driver, run_ids, args)
        envelope_rows.extend(driver_envelope_rows)
        plot_points, step = gg_decimate(points, args.max_plot_points)
        peak = max(points, key=lambda point: point[3])
        envelope_peak = max((point[3] for point in envelope), default=0.0)
        max_accel = max((point[1] for point in points), default=0.0)
        max_brake = min((point[1] for point in points), default=0.0)
        max_left = max((point[0] for point in points), default=0.0)
        max_right = min((point[0] for point in points), default=0.0)
        excluded = excluded_by_driver[driver]

        points_csv = args.out / f"{driver.lower().replace(' ', '_')}_gg_points.csv"
        with points_csv.open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["lateral_g", "longitudinal_g", "speed_mph", "combined_g", "angle_deg"])
            for point in plot_points:
                writer.writerow([
                    f"{point[0]:.5f}",
                    f"{point[1]:.5f}",
                    f"{point[2]:.2f}",
                    f"{point[3]:.5f}",
                    f"{point[4]:.2f}",
                ])

        summary = {
            "driver": driver,
            "run_ids": ";".join(run_ids),
            "raw_rows": group["raw_rows"],
            "kept_rows": len(points),
            "plot_points": len(plot_points),
            "decimation_step": step,
            **excluded,
            "peak_combined_g_raw_retained": f"{peak[3]:.5f}",
            "p_envelope_peak_combined_g": f"{envelope_peak:.5f}",
            "max_accel_g": f"{max_accel:.5f}",
            "max_brake_g": f"{max_brake:.5f}",
            "max_left_g": f"{max_left:.5f}",
            "max_right_g": f"{max_right:.5f}",
            "points_csv": points_csv.name,
        }
        summary_rows.append(summary)
        html_data.append({
            "driver": driver,
            "color": colors[color_index % len(colors)],
            "points": [[round(p[0], 4), round(p[1], 4), round(p[2], 2), round(p[3], 4)] for p in plot_points],
            "envelope": [[round(p[0], 4), round(p[1], 4), round(p[2], 2), round(p[3], 4)] for p in envelope],
            "envelopePercentile": args.envelope_percentile,
            "summary": summary,
        })

    if not html_data:
        raise SystemExit("No usable G-G samples remained. Check Speed_mph, Longitudinal_G, Lateral_G, GPS uncertainty, and filter settings.")
    write_summary(args.out / "gg_summary_by_driver.csv", summary_rows)
    write_summary(args.out / "gg_envelope_by_driver.csv", envelope_rows)
    gg_write_gg_html(args.out / "gg_diagrams_by_driver.html", html_data, summary_rows, args)
    print(f"Wrote {args.out / 'gg_diagrams_by_driver.html'}")
    print(f"Wrote {args.out / 'gg_summary_by_driver.csv'}")
    print(f"Wrote {args.out / 'gg_envelope_by_driver.csv'}")


def gg_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_files", nargs="+", type=Path, help="VNINS/BINARY CSV files or folders containing logger CSV files")
    parser.add_argument("--out", type=Path, default=Path("vn300_gg_analysis_output"))
    parser.add_argument("--include-ascii", action="store_true", help="include *_VNINS.csv even when a matching *_BINARY.csv exists")
    parser.add_argument("--metadata", type=Path, help="optional CSV metadata/run sheet to merge driver names")
    parser.add_argument("--driver-map", type=Path, help="optional CSV with run_id/session_file and driver columns")
    parser.add_argument("--driver-order", help="comma-separated driver names mapped onto sorted input files")
    parser.add_argument("--driver-order-offset", type=int, default=0, help="number of sorted input files to skip before applying --driver-order")
    parser.add_argument("--speed-min-mph", type=float, default=DEFAULT_SPEED_MIN_MPH)
    parser.add_argument("--speed-max-mph", type=float, default=DEFAULT_SPEED_MAX_MPH)
    parser.add_argument("--gps-uncertainty-max-m", type=float, default=DEFAULT_GPS_UNCERTAINTY_MAX_M)
    parser.add_argument("--abs-g-hard-limit", type=float, default=DEFAULT_ABS_G_HARD_LIMIT)
    parser.add_argument("--combined-g-hard-limit", type=float, default=DEFAULT_COMBINED_G_HARD_LIMIT)
    parser.add_argument("--envelope-percentile", type=float, default=DEFAULT_ENVELOPE_PERCENTILE)
    parser.add_argument("--angle-bin-deg", type=float, default=DEFAULT_BIN_DEG)
    parser.add_argument("--min-bin-points", type=int, default=DEFAULT_MIN_BIN_POINTS)
    parser.add_argument("--max-plot-points", type=int, default=DEFAULT_MAX_PLOT_POINTS)
    parser.add_argument("--min-driver-points", type=int, default=DEFAULT_MIN_DRIVER_POINTS)
    return parser


def gg_main():
    args = gg_parser().parse_args()

    if not (0.0 < args.envelope_percentile <= 1.0):
        raise SystemExit("--envelope-percentile must be in the range (0, 1].")
    if 360 % args.angle_bin_deg != 0:
        raise SystemExit("--angle-bin-deg must divide evenly into 360.")
    gg_run_analysis(args)


# Lc0 Theoretical Gg
#!/usr/bin/env python3





MODEL_NAME = "Hoosier 18x6-10 LC0"
TTC_RUNS = "TTC Round 6 / B1654: cornering 30-31, drive-brake 44-45"
TTC_SURFACE = "120-grit 3Mite abrasive flat belt, stoned once"


def tire_parse_number(value: object) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def tire_read_tir_parameters(path: Path) -> dict[str, float]:
    if not path.exists():
        raise SystemExit(f"TIR file not found: {path}")
    parameters: dict[str, float] = {}
    pattern = re.compile(
        r"^\s*([A-Z][A-Z0-9_]*)\s*=\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?)"
    )
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = pattern.match(line)
        if match:
            parameters[match.group(1)] = float(match.group(2))
    required = {"FNOMIN", "NOMPRES", "PDX1", "PDX2", "PDX3", "PDY1", "PDY2", "PDY3"}
    missing = sorted(required - parameters.keys())
    if missing:
        raise SystemExit(f"Missing required TIR parameters: {', '.join(missing)}")
    return parameters


def tire_pure_slip_limits(
    parameters: dict[str, float],
    normal_load_n: float,
    pressure_pa: float,
    camber_deg: float,
    grip_scale: float,
) -> tuple[float, float]:
    """Return longitudinal and lateral peak friction coefficients.

    Equations follow the MF6.1 peak-factor load, pressure, and camber terms.
    Pressure coefficients are optional because some fitted files omit them.
    """
    dfz = (normal_load_n - parameters["FNOMIN"]) / parameters["FNOMIN"]
    dpi = (pressure_pa - parameters["NOMPRES"]) / parameters["NOMPRES"]
    gamma = math.radians(camber_deg)

    pressure_x = 1.0 + parameters.get("PPX3", 0.0) * dpi + parameters.get("PPX4", 0.0) * dpi**2
    pressure_y = 1.0 + parameters.get("PPY3", 0.0) * dpi + parameters.get("PPY4", 0.0) * dpi**2
    mu_x = (
        (parameters["PDX1"] + parameters["PDX2"] * dfz)
        * (1.0 - parameters["PDX3"] * gamma**2)
        * pressure_x
        * parameters.get("LMUX", 1.0)
        * grip_scale
    )
    mu_y = (
        (parameters["PDY1"] + parameters["PDY2"] * dfz)
        * (1.0 - parameters["PDY3"] * gamma**2)
        * pressure_y
        * parameters.get("LMUY", 1.0)
        * grip_scale
    )
    if mu_x <= 0 or mu_y <= 0:
        raise SystemExit("Calculated friction limits are not positive; check load, pressure, camber, and scale.")
    return mu_x, mu_y


def tire_theoretical_radius(angle_deg: float, lateral_limit_g: float, longitudinal_limit_g: float, exponent: float) -> float:
    angle = math.radians(angle_deg)
    lateral_part = abs(math.cos(angle)) / lateral_limit_g
    longitudinal_part = abs(math.sin(angle)) / longitudinal_limit_g
    return (lateral_part**exponent + longitudinal_part**exponent) ** (-1.0 / exponent)


def tire_load_envelopes(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(f"Driver envelope CSV not found: {path}")
    rows = []
    with path.open(newline="", encoding="utf-8-sig") as file:
        for source in csv.DictReader(file):
            if not str(source.get("used", "yes")).lower().startswith("yes"):
                continue
            angle = tire_parse_number(source.get("angle_center_deg"))
            lateral = tire_parse_number(source.get("lateral_g"))
            longitudinal = tire_parse_number(source.get("longitudinal_g"))
            radius = tire_parse_number(source.get("p98_combined_g") or source.get("p_combined_g"))
            driver = (source.get("driver") or "Unknown").strip()
            if angle is None or lateral is None or longitudinal is None:
                continue
            if radius is None:
                radius = math.hypot(lateral, longitudinal)
            rows.append({
                "driver": driver,
                "run_id": source.get("run_id") or source.get("run_ids") or "",
                "angle_deg": angle,
                "lateral_g": lateral,
                "longitudinal_g": longitudinal,
                "recorded_g": radius,
            })
    if not rows:
        raise SystemExit(f"No usable envelope rows found in: {path}")
    return rows


def tire_nearest_usage(rows: list[dict], target_deg: float) -> float | None:
    if not rows:
        return None
    def angular_distance(row: dict) -> float:
        return abs((row["angle_deg"] - target_deg + 180.0) % 360.0 - 180.0)
    return min(rows, key=angular_distance)["usage_pct"]


def tire_summarize(name: str, rows: list[dict]) -> dict:
    usages = [row["usage_pct"] for row in rows]
    return {
        "driver": name,
        "directions": len(rows),
        "mean_directional_usage_pct": sum(usages) / len(usages),
        "peak_directional_usage_pct": max(usages),
        "right_lateral_usage_pct": tire_nearest_usage(rows, 180.0),
        "acceleration_usage_pct": tire_nearest_usage(rows, 90.0),
        "left_lateral_usage_pct": tire_nearest_usage(rows, 0.0),
        "braking_usage_pct": tire_nearest_usage(rows, 270.0),
    }


def tire_write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def tire_format_summary_rows(rows: list[dict]) -> str:
    body = []
    for row in rows:
        body.append(
            "<tr>"
            f"<td>{html.escape(row['driver'])}</td>"
            f"<td>{row['mean_directional_usage_pct']:.1f}%</td>"
            f"<td>{row['right_lateral_usage_pct']:.1f}%</td>"
            f"<td>{row['left_lateral_usage_pct']:.1f}%</td>"
            f"<td>{row['acceleration_usage_pct']:.1f}%</td>"
            f"<td>{row['braking_usage_pct']:.1f}%</td>"
            f"<td>{row['peak_directional_usage_pct']:.1f}%</td>"
            "</tr>"
        )
    return "".join(body)


def tire_write_html(path: Path, plot_data: list[dict], summaries: list[dict], model: dict) -> None:
    model_json = json.dumps(model)
    plots_json = json.dumps(plot_data)
    table_rows = tire_format_summary_rows(summaries)
    body = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{MODEL_NAME} theoretical G-G utilization</title>
<style>
body{{margin:0;background:#f4f6f8;color:#18212a;font-family:Arial,Helvetica,sans-serif}}
header{{padding:18px 22px;background:#17202a;color:#f2f5f7;border-bottom:3px solid #d1495b}}
main{{padding:16px;display:grid;gap:16px}}section{{background:#fff;border:1px solid #ccd5de;padding:14px;overflow:auto}}
.metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:1px;background:#ccd5de;border:1px solid #ccd5de}}
.metric{{background:#fff;padding:12px}}.metric strong{{font-size:22px;display:block;margin-top:4px}}
.plots{{display:grid;grid-template-columns:repeat(auto-fit,minmax(440px,1fr));gap:14px}}.plot{{border:1px solid #ccd5de;padding:10px}}
canvas{{width:100%;height:420px;background:#fbfcfd;border:1px solid #cbd4dc}}
table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{border-bottom:1px solid #dce3e9;padding:7px;text-align:right}}th:first-child,td:first-child{{text-align:left}}
.note{{font-size:13px;line-height:1.45;color:#566573}}.legend{{font-size:13px;color:#566573;display:flex;gap:18px;flex-wrap:wrap;margin-top:7px}}
.line{{display:inline-block;width:22px;height:3px;margin-right:6px;vertical-align:middle}}h1{{margin:0 0 5px;font-size:25px;letter-spacing:0}}h2{{margin:0 0 10px;font-size:18px;letter-spacing:0}}
@media(max-width:540px){{main{{padding:8px}}.plots{{grid-template-columns:1fr}}canvas{{height:330px}}}}
</style>
</head>
<body>
<header><h1>{MODEL_NAME} theoretical G-G utilization</h1><div>{TTC_RUNS}</div></header>
<main>
<div class="metrics">
<div class="metric">Pure lateral ceiling<strong>{model['lateral_limit_g']:.3f} g</strong></div>
<div class="metric">Pure longitudinal ceiling<strong>{model['longitudinal_limit_g']:.3f} g</strong></div>
<div class="metric">Combined-slip exponent<strong>{model['ellipse_exponent']:.2f}</strong></div>
<div class="metric">Recorded car mean usage<strong>{summaries[0]['mean_directional_usage_pct']:.1f}%</strong></div>
</div>
<section><h2>Utilization Summary</h2><table><thead><tr><th>Envelope</th><th>Mean</th><th>Right</th><th>Left</th><th>Accel</th><th>Brake</th><th>Peak</th></tr></thead><tbody>{table_rows}</tbody></table></section>
<div class="plots" id="plots"></div>
<section class="note"><h2>Model Boundary</h2>
The red boundary is a tire-limited ceiling measured on Calspan's {model['test_surface']}, not a complete vehicle prediction. It uses MF6.1 pure-slip peak factors at {model['normal_load_n']:.0f} N, {model['pressure_kpa']:.1f} kPa, and {model['camber_deg']:.1f} degrees camber, then applies the combined-slip equation
<code>(|a_lat| / {model['lateral_limit_g']:.3f})^{model['ellipse_exponent']:.2f} + (|a_long| / {model['longitudinal_limit_g']:.3f})^{model['ellipse_exponent']:.2f} = 1</code>.
The envelope does not include track-surface loss, tire temperature, transient response, load transfer, aero, power limits, gearing, differential behavior, brake balance, or control limits. A point above 100% is therefore a prompt to validate assumptions and sensor data, not proof of impossible performance.</section>
</main>
<script>
const model={model_json}; const plots={plots_json};
function draw(canvas,d){{
 const ctx=canvas.getContext('2d'),w=canvas.width,h=canvas.height,pad=48;
 const limit=Math.ceil(Math.max(model.lateral_limit_g,model.longitudinal_limit_g,2.5)*2)/2+0.25;
 const sx=x=>pad+(x+limit)/(2*limit)*(w-2*pad), sy=y=>h-pad-(y+limit)/(2*limit)*(h-2*pad);
 ctx.fillStyle='#fbfcfd';ctx.fillRect(0,0,w,h);ctx.font='12px Arial';ctx.fillStyle='#647482';ctx.strokeStyle='#e0e6eb';ctx.lineWidth=1;
 for(let g=Math.ceil(-limit*2)/2;g<=limit;g+=0.5){{ctx.beginPath();ctx.moveTo(sx(g),sy(-limit));ctx.lineTo(sx(g),sy(limit));ctx.moveTo(sx(-limit),sy(g));ctx.lineTo(sx(limit),sy(g));ctx.stroke();if(Math.abs(g)>0.01){{ctx.fillText(g.toFixed(1),sx(g)-10,sy(0)+16);ctx.fillText(g.toFixed(1),sx(0)+7,sy(g)+4)}}}}
 ctx.strokeStyle='#75818b';ctx.lineWidth=1.3;ctx.beginPath();ctx.moveTo(sx(-limit),sy(0));ctx.lineTo(sx(limit),sy(0));ctx.moveTo(sx(0),sy(-limit));ctx.lineTo(sx(0),sy(limit));ctx.stroke();
 const theory=[];for(let a=0;a<=360;a+=2){{const r=Math.pow(Math.pow(Math.abs(Math.cos(a*Math.PI/180))/model.lateral_limit_g,model.ellipse_exponent)+Math.pow(Math.abs(Math.sin(a*Math.PI/180))/model.longitudinal_limit_g,model.ellipse_exponent),-1/model.ellipse_exponent);theory.push([r*Math.cos(a*Math.PI/180),r*Math.sin(a*Math.PI/180)])}}
 ctx.fillStyle='rgba(209,73,91,.08)';ctx.strokeStyle='#d1495b';ctx.lineWidth=3;ctx.beginPath();theory.forEach((p,i)=>i?ctx.lineTo(sx(p[0]),sy(p[1])):ctx.moveTo(sx(p[0]),sy(p[1])));ctx.closePath();ctx.fill();ctx.stroke();
 if(d.secondary){{ctx.strokeStyle='rgba(55,94,117,.22)';ctx.lineWidth=1;for(const line of d.secondary){{ctx.beginPath();line.points.forEach((p,i)=>i?ctx.lineTo(sx(p[0]),sy(p[1])):ctx.moveTo(sx(p[0]),sy(p[1])));ctx.closePath();ctx.stroke()}}}}
 ctx.strokeStyle=d.color;ctx.fillStyle=d.color;ctx.lineWidth=3;ctx.beginPath();d.points.forEach((p,i)=>i?ctx.lineTo(sx(p[0]),sy(p[1])):ctx.moveTo(sx(p[0]),sy(p[1])));ctx.closePath();ctx.stroke();for(const p of d.points){{ctx.beginPath();ctx.arc(sx(p[0]),sy(p[1]),2.8,0,Math.PI*2);ctx.fill()}}
 ctx.fillStyle='#18212a';ctx.font='13px Arial';ctx.fillText('Lateral G',w/2-28,h-12);ctx.save();ctx.translate(15,h/2+43);ctx.rotate(-Math.PI/2);ctx.fillText('Longitudinal G',0,0);ctx.restore();ctx.fillStyle='#647482';ctx.fillText('accel',sx(-limit)+4,sy(limit)+15);ctx.fillText('braking',sx(-limit)+4,sy(-limit)+15);
}}
const host=document.getElementById('plots');for(const d of plots){{const div=document.createElement('section');div.className='plot';div.innerHTML=`<h2>${{d.name}}</h2><canvas width="800" height="500"></canvas><div class="legend"><span><span class="line" style="background:#d1495b"></span>LC0 theoretical</span><span><span class="line" style="background:${{d.color}}"></span>${{d.name}} recorded P98</span></div>`;host.appendChild(div);draw(div.querySelector('canvas'),d)}}
</script></body></html>"""
    path.write_text(body, encoding="utf-8")


def tire_run(args: argparse.Namespace) -> None:
    if args.ellipse_exponent < 1.0:
        raise SystemExit("--ellipse-exponent must be at least 1.0")
    if not (0.0 < args.track_grip_scale <= 2.0):
        raise SystemExit("--track-grip-scale must be in (0, 2]")
    parameters = tire_read_tir_parameters(args.tir)
    normal_load_n = args.normal_load_n or parameters["FNOMIN"]
    pressure_pa = (args.pressure_kpa * 1000.0) if args.pressure_kpa is not None else parameters["NOMPRES"]
    mu_x, mu_y = tire_pure_slip_limits(
        parameters, normal_load_n, pressure_pa, args.camber_deg, args.track_grip_scale
    )

    source_rows = tire_load_envelopes(args.driver_envelope)
    directional_rows = []
    grouped: dict[str, list[dict]] = defaultdict(list)
    for source in source_rows:
        theory_g = tire_theoretical_radius(source["angle_deg"], mu_y, mu_x, args.ellipse_exponent)
        row = {**source, "theoretical_g": theory_g, "usage_pct": 100.0 * source["recorded_g"] / theory_g}
        directional_rows.append(row)
        grouped[row["driver"]].append(row)

    car_by_angle: dict[float, dict] = {}
    for row in directional_rows:
        angle = row["angle_deg"]
        if angle not in car_by_angle or row["recorded_g"] > car_by_angle[angle]["recorded_g"]:
            car_by_angle[angle] = {**row, "source_driver": row["driver"], "driver": "Recorded car (best driver per direction)"}
    car_rows = sorted(car_by_angle.values(), key=lambda row: row["angle_deg"])

    driver_summaries = [tire_summarize(driver, sorted(rows, key=lambda row: row["angle_deg"])) for driver, rows in grouped.items()]
    driver_summaries.sort(key=lambda row: row["mean_directional_usage_pct"], reverse=True)
    car_summary = tire_summarize("Recorded car (best driver per direction)", car_rows)
    summaries = [car_summary, *driver_summaries]

    args.out.mkdir(parents=True, exist_ok=True)
    directional_columns = [
        "driver", "run_id", "angle_deg", "lateral_g", "longitudinal_g", "recorded_g", "theoretical_g", "usage_pct"
    ]
    tire_write_csv(args.out / "lc0_theoretical_directional_usage.csv", directional_rows, directional_columns)
    tire_write_csv(
        args.out / "lc0_recorded_car_vs_theory.csv", car_rows,
        [*directional_columns, "source_driver"],
    )
    tire_write_csv(
        args.out / "lc0_theoretical_usage_by_driver.csv", summaries,
        ["driver", "directions", "mean_directional_usage_pct", "right_lateral_usage_pct", "left_lateral_usage_pct", "acceleration_usage_pct", "braking_usage_pct", "peak_directional_usage_pct"],
    )

    model = {
        "model_name": MODEL_NAME,
        "source_runs": TTC_RUNS,
        "test_surface": TTC_SURFACE,
        "tir_file": str(args.tir.resolve()),
        "driver_envelope_file": str(args.driver_envelope.resolve()),
        "normal_load_n": normal_load_n,
        "pressure_kpa": pressure_pa / 1000.0,
        "camber_deg": args.camber_deg,
        "track_grip_scale": args.track_grip_scale,
        "ellipse_exponent": args.ellipse_exponent,
        "longitudinal_limit_g": mu_x,
        "lateral_limit_g": mu_y,
        "limitations": [
            "TTC 120-grit 3Mite abrasive-belt ceiling; not a complete vehicle model",
            "No load transfer, aero, temperature, transient, or track-surface model",
            "No powertrain, gearing, differential, brake-balance, or control limits",
        ],
    }
    (args.out / "lc0_theoretical_model.json").write_text(json.dumps(model, indent=2), encoding="utf-8")

    colors = ["#236b8e", "#2a9d8f", "#e9a23b", "#6d597a", "#457b4c", "#c06c84", "#7a6a53", "#5c677d"]
    secondary = [
        {"name": driver, "points": [[row["lateral_g"], row["longitudinal_g"]] for row in sorted(rows, key=lambda item: item["angle_deg"])]}
        for driver, rows in grouped.items()
    ]
    plot_data = [{
        "name": car_summary["driver"], "color": "#236b8e",
        "points": [[row["lateral_g"], row["longitudinal_g"]] for row in car_rows], "secondary": secondary,
    }]
    for index, summary in enumerate(driver_summaries):
        rows = sorted(grouped[summary["driver"]], key=lambda row: row["angle_deg"])
        plot_data.append({
            "name": summary["driver"], "color": colors[(index + 1) % len(colors)],
            "points": [[row["lateral_g"], row["longitudinal_g"]] for row in rows],
        })
    tire_write_html(args.out / "lc0_theoretical_gg.html", plot_data, summaries, model)

    print(f"LC0 pure-slip limits: lateral={mu_y:.4f} g, longitudinal={mu_x:.4f} g")
    print(f"Recorded car mean directional utilization: {car_summary['mean_directional_usage_pct']:.2f}%")
    print(f"Wrote {args.out / 'lc0_theoretical_gg.html'}")
    print(f"Wrote {args.out / 'lc0_theoretical_usage_by_driver.csv'}")
    print(f"Wrote {args.out / 'lc0_theoretical_directional_usage.csv'}")
    print(f"Wrote {args.out / 'lc0_recorded_car_vs_theory.csv'}")
    print(f"Wrote {args.out / 'lc0_theoretical_model.json'}")


def tire_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Compare recorded G-G envelopes with an LC0 tire-limited friction ellipse.\n\nThis is deliberately a first-order vehicle model. Pure-slip friction\ncoefficients come from an MF6.1 TIR file and combined slip is represented by\na superellipse. It does not model power, brake balance, load transfer,\ndownforce, temperature, or the difference between the TTC belt and pavement.')
    parser.add_argument("driver_envelope", type=Path, help="gg_envelope_by_driver CSV from vn300_gg_analysis.py")
    parser.add_argument("--tir", type=Path, required=True, help="fitted Hoosier 18x6-10 LC0 MF6.1 TIR file")
    parser.add_argument("--out", type=Path, default=Path("lc0_theoretical_gg_output"))
    parser.add_argument("--normal-load-n", type=float, help="per-tire normal load; default is TIR FNOMIN")
    parser.add_argument("--pressure-kpa", type=float, help="inflation pressure; default is TIR NOMPRES")
    parser.add_argument("--camber-deg", type=float, default=0.0)
    parser.add_argument("--track-grip-scale", type=float, default=1.0, help="multiply TTC peak friction by a track correlation factor")
    parser.add_argument("--ellipse-exponent", type=float, default=2.0, help="2.0 is a basic friction ellipse")
    return parser


def tire_main():
    tire_run(tire_parser().parse_args())


# Lc0 Minimum Lap
#!/usr/bin/env python3




def load_model_dependencies():
    """Load numerical packages only when the LC0 model is requested."""
    global plt, np, pd, gaussian_filter1d, least_squares
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd
    from scipy.ndimage import gaussian_filter1d
    from scipy.optimize import least_squares



G_MPS2 = 9.80665
MPH_PER_MPS = 2.2369362921
EARTH_RADIUS_M = 6371000.0


def model_finite_interpolated(series: pd.Series, low: float, high: float) -> np.ndarray:
    values = pd.to_numeric(series, errors="coerce").astype(float)
    values = values.where(np.isfinite(values) & values.between(low, high))
    return values.interpolate(limit_direction="both").bfill().ffill().to_numpy(dtype=float)


def model_local_xy(lat: np.ndarray, lon: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    lat0 = float(np.nanmedian(lat))
    lon0 = float(np.nanmedian(lon))
    x = np.radians(lon - lon0) * EARTH_RADIUS_M * math.cos(math.radians(lat0))
    y = np.radians(lat - lat0) * EARTH_RADIUS_M
    return x, y


def model_load_reference_lap(
    path: Path,
    start_row: int,
    samples: int,
    duration_s: float,
    grid_step_m: float,
    curvature_smoothing_m: float,
) -> dict[str, np.ndarray | float | int]:
    columns = {
        "Latitude_deg",
        "Longitude_deg",
        "Common_AngularRate_Z_rps",
        "Imu_AngularRate_Z_rps",
        "Speed_mps",
        "Longitudinal_G",
        "Lateral_G",
    }
    raw = pd.read_csv(path, usecols=lambda name: name in columns)
    segment = raw.iloc[start_row:start_row + samples].copy().reset_index(drop=True)
    if len(segment) != samples:
        raise SystemExit(f"Reference slice requested {samples} rows but only {len(segment)} were available")

    speed = model_finite_interpolated(segment["Speed_mps"], 0.0, 45.0)
    long_g = model_finite_interpolated(segment["Longitudinal_G"], -3.0, 3.0)
    lat_g = model_finite_interpolated(segment["Lateral_G"], -3.0, 3.0)
    yaw_column = "Common_AngularRate_Z_rps" if "Common_AngularRate_Z_rps" in segment else "Imu_AngularRate_Z_rps"
    yaw_rate = model_finite_interpolated(segment[yaw_column], -6.0, 6.0)

    lat = pd.to_numeric(segment["Latitude_deg"], errors="coerce").astype(float)
    lon = pd.to_numeric(segment["Longitude_deg"], errors="coerce").astype(float)
    median_lat = float(lat[np.isfinite(lat)].median())
    median_lon = float(lon[np.isfinite(lon)].median())
    lat = lat.where(np.isfinite(lat) & lat.between(median_lat - 0.002, median_lat + 0.002))
    lon = lon.where(np.isfinite(lon) & lon.between(median_lon - 0.002, median_lon + 0.002))
    lat = lat.interpolate(limit_direction="both").bfill().ffill().to_numpy(dtype=float)
    lon = lon.interpolate(limit_direction="both").bfill().ffill().to_numpy(dtype=float)

    dt = duration_s / max(samples - 1, 1)
    incremental_distance = np.zeros(samples)
    incremental_distance[1:] = 0.5 * (speed[1:] + speed[:-1]) * dt
    source_s = np.cumsum(incremental_distance)
    distance_m = float(source_s[-1])
    if not math.isfinite(distance_m) or distance_m < 100.0:
        raise SystemExit("Reference lap speed integration did not produce a credible course length")

    points = max(200, int(round(distance_m / grid_step_m)))
    grid_s = np.linspace(0.0, distance_m, points, endpoint=False)
    ds_m = distance_m / points
    speed_grid = np.interp(grid_s, source_s, speed)
    long_grid = np.interp(grid_s, source_s, long_g)
    lat_grid = np.interp(grid_s, source_s, lat_g)
    yaw_grid = np.interp(grid_s, source_s, yaw_rate)

    curvature = yaw_grid / np.maximum(speed_grid, 3.0)
    sigma = max(0.5, curvature_smoothing_m / ds_m)
    curvature = gaussian_filter1d(curvature, sigma=sigma, mode="wrap")
    curvature = np.clip(curvature, -0.35, 0.35)
    raw_heading_change_rad = float(np.sum(curvature) * ds_m)
    if abs(raw_heading_change_rad) > math.pi:
        winding_count = max(1, round(abs(raw_heading_change_rad) / (2.0 * math.pi)))
        target_heading_change_rad = math.copysign(2.0 * math.pi * winding_count, raw_heading_change_rad)
        curvature *= target_heading_change_rad / raw_heading_change_rad

    x, y = model_local_xy(lat, lon)
    progress = source_s / distance_m
    x = x - progress * (x[-1] - x[0])
    y = y - progress * (y[-1] - y[0])
    x_grid = np.interp(grid_s, source_s, x)
    y_grid = np.interp(grid_s, source_s, y)
    x_grid = gaussian_filter1d(x_grid, sigma=sigma, mode="wrap")
    y_grid = gaussian_filter1d(y_grid, sigma=sigma, mode="wrap")

    return {
        "distance_m": distance_m,
        "duration_s": duration_s,
        "points": points,
        "ds_m": ds_m,
        "distance_grid_m": grid_s,
        "actual_speed_mps": speed_grid,
        "actual_longitudinal_g": long_grid,
        "actual_lateral_g": lat_grid,
        "curvature_1pm": curvature,
        "x_m": x_grid,
        "y_m": y_grid,
        "raw_integrated_heading_change_deg": math.degrees(raw_heading_change_rad),
        "integrated_heading_change_deg": math.degrees(float(np.sum(curvature) * ds_m)),
    }


def model_propulsion_model(v_mps: np.ndarray, launch_limit_g: float, specific_power_w_per_kg: float) -> np.ndarray:
    return np.minimum(launch_limit_g, specific_power_w_per_kg / (G_MPS2 * np.maximum(v_mps, 2.0)))


def model_fit_propulsion_envelope(root: Path | None, run_ids: list[str]) -> tuple[dict, list[dict]]:
    rows = []
    if root is not None and root.exists():
        for run_id in sorted(set(run_ids)):
            matches = list(root.rglob(f"{run_id}_BINARY.csv"))
            if not matches:
                continue
            frame = pd.read_csv(
                matches[0],
                usecols=lambda name: name in {"Speed_mph", "Longitudinal_G", "Lateral_G", "PosUncertainty_m"},
            )
            for name in frame:
                frame[name] = pd.to_numeric(frame[name], errors="coerce")
            clean = frame[
                frame["Speed_mph"].between(5.0, 55.0)
                & frame["Longitudinal_G"].between(0.0, 1.5)
                & frame["Lateral_G"].abs().lt(0.30)
                & frame["PosUncertainty_m"].between(0.0, 4.0)
            ]
            for low_mph in np.arange(5.0, 55.0, 5.0):
                part = clean[clean["Speed_mph"].between(low_mph, low_mph + 5.0, inclusive="left")]
                if len(part) >= 20:
                    rows.append({
                        "run_id": run_id,
                        "speed_bin_low_mph": float(low_mph),
                        "speed_bin_high_mph": float(low_mph + 5.0),
                        "speed_center_mph": float(low_mph + 2.5),
                        "samples": int(len(part)),
                        "p99_acceleration_g": float(np.percentile(part["Longitudinal_G"], 99.0)),
                    })

    if rows:
        frame = pd.DataFrame(rows)
        combined = []
        for center, group in frame.groupby("speed_center_mph"):
            combined.append({
                "speed_center_mph": float(center),
                "samples": int(group["samples"].sum()),
                "p99_acceleration_g": float(np.average(group["p99_acceleration_g"], weights=group["samples"])),
            })
        x = np.array([row["speed_center_mph"] / MPH_PER_MPS for row in combined])
        y = np.array([row["p99_acceleration_g"] for row in combined])

        def residual(parameters):
            return model_propulsion_model(x, parameters[0], parameters[1]) - y

        result = least_squares(residual, x0=[0.8, 100.0], bounds=([0.25, 25.0], [1.5, 250.0]), loss="soft_l1")
        launch_limit_g, specific_power = map(float, result.x)
        rmse = float(np.sqrt(np.mean(residual(result.x) ** 2)))
    else:
        combined = []
        launch_limit_g, specific_power, rmse = 0.75, 95.0, float("nan")

    model = {
        "launch_limit_g": launch_limit_g,
        "specific_power_w_per_kg": specific_power,
        "fit_rmse_g": rmse,
        "fit_bins": len(combined),
        "source": "99th-percentile straight-line VN-300 acceleration" if combined else "fallback assumption",
    }
    for row in combined:
        v = row["speed_center_mph"] / MPH_PER_MPS
        row["fitted_acceleration_g"] = float(model_propulsion_model(np.array([v]), launch_limit_g, specific_power)[0])
    return model, combined


def model_ellipse_longitudinal_capacity_g(
    speed_mps: float,
    curvature_1pm: float,
    lateral_limit_g: float,
    longitudinal_limit_g: float,
    exponent: float,
) -> float:
    lateral_g = abs(curvature_1pm) * speed_mps * speed_mps / G_MPS2
    fraction = min(1.0, lateral_g / lateral_limit_g)
    return longitudinal_limit_g * max(0.0, 1.0 - fraction**exponent) ** (1.0 / exponent)


def model_solve_minimum_lap(
    curvature: np.ndarray,
    ds_m: float,
    lateral_limit_g: float,
    longitudinal_limit_g: float,
    exponent: float,
    propulsion: dict,
    maximum_speed_mps: float,
) -> dict[str, np.ndarray | float | int]:
    count = len(curvature)
    lateral_speed_cap = np.sqrt(
        lateral_limit_g * G_MPS2 / np.maximum(np.abs(curvature), 1e-7)
    )
    speed = np.minimum(lateral_speed_cap, maximum_speed_mps)

    iterations = 0
    for iterations in range(1, 1001):
        previous = speed.copy()
        for i in range(count):
            source = (i - 1) % count
            tire_g = model_ellipse_longitudinal_capacity_g(
                speed[source], curvature[source], lateral_limit_g, longitudinal_limit_g, exponent
            )
            engine_g = float(model_propulsion_model(
                np.array([speed[source]]), propulsion["launch_limit_g"], propulsion["specific_power_w_per_kg"]
            )[0])
            acceleration = max(0.0, min(tire_g, engine_g)) * G_MPS2
            reachable = math.sqrt(max(0.0, speed[source] ** 2 + 2.0 * acceleration * ds_m))
            speed[i] = min(speed[i], reachable, lateral_speed_cap[i], maximum_speed_mps)

        for i in range(count - 1, -1, -1):
            target = (i + 1) % count
            if speed[i] > speed[target]:
                low = speed[target]
                high = min(speed[i], lateral_speed_cap[i], maximum_speed_mps)
                for _ in range(30):
                    candidate = 0.5 * (low + high)
                    required_g = (candidate**2 - speed[target] ** 2) / (2.0 * ds_m * G_MPS2)
                    available_g = model_ellipse_longitudinal_capacity_g(
                        candidate, curvature[i], lateral_limit_g, longitudinal_limit_g, exponent
                    )
                    if required_g <= available_g:
                        low = candidate
                    else:
                        high = candidate
                speed[i] = min(speed[i], low)

        if float(np.max(np.abs(speed - previous))) < 1e-7:
            break

    next_speed = np.roll(speed, -1)
    dt = 2.0 * ds_m / np.maximum(speed + next_speed, 0.2)
    longitudinal_g = (next_speed**2 - speed**2) / (2.0 * ds_m * G_MPS2)
    lateral_g = curvature * speed**2 / G_MPS2
    utilization = (
        (np.abs(longitudinal_g) / longitudinal_limit_g) ** exponent
        + (np.abs(lateral_g) / lateral_limit_g) ** exponent
    ) ** (1.0 / exponent)
    return {
        "lap_time_s": float(np.sum(dt)),
        "speed_mps": speed,
        "longitudinal_g": longitudinal_g,
        "lateral_g": lateral_g,
        "utilization": utilization,
        "iterations": iterations,
        "max_speed_mph": float(np.max(speed) * MPH_PER_MPS),
        "mean_speed_mph": float(len(speed) * ds_m / np.sum(dt) * MPH_PER_MPS),
    }


def model_load_lap_summary(path: Path | None) -> tuple[list[dict], list[str]]:
    if path is None or not path.exists():
        return [], []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    run_ids = [row.get("run_id", "") for row in rows if row.get("run_id")]
    clean = []
    for row in rows:
        try:
            row["lap_number"] = int(row["lap_number"])
            row["lap_time_s"] = float(row["lap_time_s"])
            row["distance_m"] = float(row["distance_m"])
            row["max_speed_mph"] = float(row["max_speed_mph"])
            for sector_number in range(1, 4):
                row[f"sector_{sector_number}_s"] = float(row[f"sector_{sector_number}_s"])
        except (KeyError, TypeError, ValueError):
            continue
        row["valid_for_benchmark"] = (
            25.0 <= row["lap_time_s"] <= 55.0
            and 300.0 <= row["distance_m"] <= 430.0
            and row["max_speed_mph"] <= 60.0
        )
        row["exclusion_reason"] = "" if row["valid_for_benchmark"] else "duration, distance, or speed sanity filter"
        clean.append(row)

    for run_id in sorted(set(row.get("run_id", "") for row in clean)):
        group = [row for row in clean if row.get("run_id") == run_id]
        notes = " ".join(str(row.get("notes", "")) for row in group).lower()
        candidates = [row for row in group if row["valid_for_benchmark"]]
        if "last two" in notes:
            for row in sorted(group, key=lambda item: item["lap_number"])[-2:]:
                row["valid_for_benchmark"] = False
                row["exclusion_reason"] = "run metadata: last two invalid"
        if "best lap was invalid" in notes and candidates:
            fastest = min(candidates, key=lambda item: item["lap_time_s"])
            fastest["valid_for_benchmark"] = False
            fastest["exclusion_reason"] = "run metadata: best lap invalid"
    return clean, run_ids


def model_build_sector_composite(lap_rows: list[dict]) -> dict | None:
    valid_laps = [row for row in lap_rows if row["valid_for_benchmark"]]
    if not valid_laps:
        return None
    sectors = []
    for sector_number in range(1, 4):
        key = f"sector_{sector_number}_s"
        candidates = [row for row in valid_laps if np.isfinite(row[key]) and row[key] > 0.0]
        if not candidates:
            return None
        best = min(candidates, key=lambda row: row[key])
        sectors.append({
            "sector": sector_number,
            "time_s": best[key],
            "driver": best.get("driver", "Unknown") or "Unknown",
            "run_id": best.get("run_id", ""),
            "lap_number": best["lap_number"],
        })
    return {"lap_time_s": sum(sector["time_s"] for sector in sectors), "sectors": sectors}


def model_write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def model_scale_key(scale: float) -> str:
    return f"{scale:.2f}".replace(".", "p")


def model_format_delta(value: float) -> str:
    return f"{value:+.3f}"


def model_write_report(
    path: Path,
    model: dict,
    summary_rows: list[dict],
    lap_rows: list[dict],
    trace_rows: list[dict],
) -> None:
    valid_laps = [row for row in lap_rows if row["valid_for_benchmark"]]
    driver_best = []
    for driver in sorted(set(row.get("driver", "Unknown") or "Unknown" for row in valid_laps)):
        row = min(
            [item for item in valid_laps if (item.get("driver", "Unknown") or "Unknown") == driver],
            key=lambda item: item["lap_time_s"],
        )
        driver_best.append(row)
    selected = min(summary_rows, key=lambda row: abs(row["grip_scale"] - 0.65))
    accel_key = model_scale_key(selected["grip_scale"])
    sector_composite = model.get("observed_sector_composite")
    sector_target = sector_composite["lap_time_s"] if sector_composite else model["recorded_best_time_s"]
    benchmark_label = "Fastest recorded valid lap" if valid_laps else "Reference duration"
    sector_label = "Observed sector composite" if sector_composite else "Reference benchmark"
    benchmark_note = ("This target combines sector times demonstrated in the selected sessions. Entry and exit states from separate laps may not stitch together perfectly."
                      if sector_composite else "No sector-composite benchmark was supplied. The reference duration provides a comparison baseline; it does not establish an observed sector target.")

    case_table = "".join(
        "<tr>"
        f"<td>{row['grip_scale']:.0%}</td><td>{row['lateral_limit_g']:.3f}</td>"
        f"<td>{row['longitudinal_limit_g']:.3f}</td><td>{row['lap_time_s']:.3f}</td>"
        f"<td>{model_format_delta(row['delta_vs_sector_composite_s'])}</td><td>{row['max_speed_mph']:.1f}</td><td>{row['recorded_floor_status']}</td>"
        "</tr>"
        for row in summary_rows
    )
    driver_table = "".join(
        "<tr>"
        f"<td>{row.get('driver') or 'Unknown'}</td><td>{row['lap_time_s']:.3f}</td>"
        f"<td>{row.get('run_id','')}</td><td>{row['lap_number']}</td>"
        f"<td>{row['lap_time_s'] - sector_target:+.3f}</td>"
        "</tr>"
        for row in sorted(driver_best, key=lambda item: item["lap_time_s"])
    )
    sector_table = "".join(
        "<tr>"
        f"<td>{sector['sector']}</td><td>{sector['time_s']:.3f}</td><td>{sector['driver']}</td>"
        f"<td>{sector['run_id']}</td><td>{sector['lap_number']}</td>"
        "</tr>"
        for sector in (sector_composite or {}).get("sectors", [])
    )
    payload = json.dumps({"trace": trace_rows, "cases": summary_rows})
    body = f"""<!doctype html><html><head><meta charset="utf-8"><title>LC0 minimum lap prediction</title>
<style>
body{{margin:0;background:#f4f6f7;color:#172127;font-family:Arial,sans-serif}}header{{background:#172127;color:white;padding:20px 28px}}header h1{{margin:0 0 5px;font-size:25px;letter-spacing:0}}main{{max-width:1180px;margin:auto;padding:18px}}.metrics{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}}.metric{{background:white;border:1px solid #dce2e5;padding:11px;border-radius:6px;font-size:12px;color:#52616a}}.metric strong{{display:block;color:#172127;font-size:20px;margin-top:4px}}section{{margin-top:12px;background:white;border:1px solid #dce2e5;padding:15px}}h2{{font-size:16px;margin:0 0 10px}}p,.note{{font-size:13px;line-height:1.5}}.warning{{border-left:4px solid #d97706;background:#fff8eb;padding:10px 12px}}table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{padding:7px;border-bottom:1px solid #e3e7e9;text-align:right}}th:first-child,td:first-child{{text-align:left}}canvas{{width:100%;height:320px;display:block}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}code{{font-family:Consolas,monospace}}@media(max-width:800px){{.metrics,.grid{{grid-template-columns:1fr}}}}
</style></head><body><header><h1>LC0 Same-Line Minimum Lap</h1><div>{html.escape(str(model["reference_driver"]))} · lap {model["reference_lap"]} · {html.escape(Path(model["reference_csv"]).name)}</div></header><main>
<div class="metrics"><div class="metric">{benchmark_label}<strong>{model['recorded_best_time_s']:.3f} s</strong></div><div class="metric">{sector_label}<strong>{sector_target:.3f} s</strong></div><div class="metric">Demonstrated execution potential<strong>{model['recorded_best_time_s'] - sector_target:.3f} s</strong></div><div class="metric">Recorded utilization floor<strong>{model['recorded_required_scale_p99']:.0%} TTC</strong></div></div>
<section><h2>Interpretation</h2><p class="warning">{html.escape(benchmark_note)}</p><p>The friction-ellipse cases below are mathematical lower bounds, not achievable-lap predictions. They apply steady-state tire force instantaneously and omit transient response, steering transitions, individual-tire load transfer, driven-axle and differential limits, brake balance, temperature, and cone-clearance constraints. The recorded trajectory required {model['recorded_required_scale_p99']:.0%} of the TTC ellipse at its robust 99th percentile; that is only a lower bound on grip available on the day. The 100% case is the Calspan-belt upper bound and should not be treated as attainable on dusty asphalt.</p></section>
<section><h2>Observed Sector Composite</h2><table><thead><tr><th>Sector</th><th>Time (s)</th><th>Driver</th><th>Run</th><th>Lap</th></tr></thead><tbody>{sector_table}</tbody></table></section>
<section><h2>Steady-State Lower-Bound Sensitivity</h2><table><thead><tr><th>TTC scale</th><th>Lateral limit (g)</th><th>Longitudinal limit (g)</th><th>Lower bound (s)</th><th>vs sector target (s)</th><th>Max speed (mph)</th><th>Observed-data check</th></tr></thead><tbody>{case_table}</tbody></table></section>
<section><h2>Speed Profile</h2><canvas id="speed"></canvas></section>
<div class="grid"><section><h2>{selected["grip_scale"]:.0%} Acceleration Profile</h2><canvas id="accel"></canvas></section><section><h2>Reference GPS Trace</h2><canvas id="track"></canvas></section></div>
<section><h2>Best Valid Lap By Driver</h2><table><thead><tr><th>Driver</th><th>Best lap (s)</th><th>Run</th><th>Lap</th><th>Gap to sector target (s)</th></tr></thead><tbody>{driver_table}</tbody></table></section>
<section class="note"><h2>Model Definition And Limits</h2><p>LC0 input: <code>{model['tir_file']}</code>. Per-tire condition: {model['normal_load_n']:.0f} N, {model['pressure_kpa']:.1f} kPa, {model['camber_deg']:.1f} deg camber. Base TTC limits: {model['base_lateral_limit_g']:.3f} g lateral and {model['base_longitudinal_limit_g']:.3f} g longitudinal. Propulsion fit: launch cap {model['propulsion']['launch_limit_g']:.3f} g, specific power {model['propulsion']['specific_power_w_per_kg']:.1f} W/kg, RMSE {model['propulsion']['fit_rmse_g']:.3f} g.</p><p>Excluded effects: racing-line optimization, individual tire load transfer, aero load and drag, banking/grade, tire temperature, transient relaxation, differential and driven-axle limits, brake balance, steering limits, cone clearance, driver reaction, and surface changes. Course distance comes from speed integration; curvature comes from smoothed yaw rate divided by speed. Gyro-derived heading accumulated {model['raw_integrated_heading_change_deg']:.1f} deg and was closed to {model['integrated_heading_change_deg']:.1f} deg for the periodic lap.</p></section>
</main><script>
const data={payload};const trace=data.trace;const colors=['#2563eb','#0f766e','#d97706','#dc2626','#7c3aed','#059669','#92400e'];
function chart(id,series,xKey,yMin,yMax,xLabel,yLabel){{const c=document.getElementById(id),dpr=window.devicePixelRatio||1,w=c.clientWidth,h=c.clientHeight;c.width=w*dpr;c.height=h*dpr;const g=c.getContext('2d');g.scale(dpr,dpr);g.clearRect(0,0,w,h);const m={{l:48,r:14,t:14,b:34}},pw=w-m.l-m.r,ph=h-m.t-m.b;const xs=trace.map(r=>r[xKey]);const xmin=Math.min(...xs),xmax=Math.max(...xs);g.strokeStyle='#dfe5e8';g.lineWidth=1;for(let i=0;i<=5;i++){{let y=m.t+ph*i/5;g.beginPath();g.moveTo(m.l,y);g.lineTo(w-m.r,y);g.stroke();g.fillStyle='#5b6870';g.font='11px Arial';g.fillText((yMax-(yMax-yMin)*i/5).toFixed(1),4,y+4)}}for(let i=0;i<=5;i++){{let x=m.l+pw*i/5;g.fillStyle='#5b6870';g.fillText((xmin+(xmax-xmin)*i/5).toFixed(0),x-8,h-12)}}for(const [j,s] of series.entries()){{g.strokeStyle=s.color||colors[j%colors.length];g.lineWidth=s.width||2;g.beginPath();let started=false;for(const r of trace){{const xv=r[xKey],yv=r[s.key];if(!Number.isFinite(yv))continue;const x=m.l+(xv-xmin)/(xmax-xmin)*pw,y=m.t+(yMax-yv)/(yMax-yMin)*ph;if(!started){{g.moveTo(x,y);started=true}}else g.lineTo(x,y)}}g.stroke()}}g.fillStyle='#172127';g.font='12px Arial';g.fillText(xLabel,m.l+pw/2-35,h-2);g.save();g.translate(12,m.t+ph/2+25);g.rotate(-Math.PI/2);g.fillText(yLabel,0,0);g.restore();let lx=m.l;for(const [j,s] of series.entries()){{g.fillStyle=s.color||colors[j%colors.length];g.fillRect(lx,m.t,14,3);g.fillStyle='#172127';g.fillText(s.name,lx+18,m.t+5);lx+=18+g.measureText(s.name).width+18}}}}
function track(){{const c=document.getElementById('track'),dpr=window.devicePixelRatio||1,w=c.clientWidth,h=c.clientHeight;c.width=w*dpr;c.height=h*dpr;const g=c.getContext('2d');g.scale(dpr,dpr);const xs=trace.map(r=>r.x_m),ys=trace.map(r=>r.y_m),xmin=Math.min(...xs),xmax=Math.max(...xs),ymin=Math.min(...ys),ymax=Math.max(...ys),pad=18,s=Math.min((w-2*pad)/(xmax-xmin),(h-2*pad)/(ymax-ymin));g.strokeStyle='#172127';g.lineWidth=3;g.beginPath();trace.forEach((r,i)=>{{const x=w/2+(r.x_m-(xmin+xmax)/2)*s,y=h/2-(r.y_m-(ymin+ymax)/2)*s;i?g.lineTo(x,y):g.moveTo(x,y)}});g.closePath();g.stroke();g.fillStyle='#dc2626';const a=trace[0];g.beginPath();g.arc(w/2+(a.x_m-(xmin+xmax)/2)*s,h/2-(a.y_m-(ymin+ymax)/2)*s,5,0,2*Math.PI);g.fill()}}
function draw(){{const speedSeries=[{{name:'Recorded',key:'actual_speed_mph',color:'#172127',width:2}}];data.cases.forEach((c,i)=>speedSeries.push({{name:(c.grip_scale*100).toFixed(0)+'%',key:c.speed_key,color:colors[i]}}));chart('speed',speedSeries,'distance_m',0,Math.max(...trace.flatMap(r=>speedSeries.map(s=>r[s.key]||0)))*1.08,'Distance (m)','Speed (mph)');chart('accel',[{{name:'Lateral',key:'lateral_g_scale_{accel_key}',color:'#2563eb'}},{{name:'Longitudinal',key:'longitudinal_g_scale_{accel_key}',color:'#dc2626'}}],'distance_m',-2,2,'Distance (m)','Acceleration (g)');track()}}window.addEventListener('resize',draw);draw();
</script></body></html>"""
    path.write_text(body, encoding="utf-8")


def model_write_preview(path: Path, trace: pd.DataFrame, summary_rows: list[dict]) -> None:
    selected = min(summary_rows, key=lambda row: abs(row["grip_scale"] - 0.65))
    accel_key = model_scale_key(selected["grip_scale"])
    fig, axes = plt.subplots(2, 2, figsize=(14, 9), constrained_layout=True)
    axes[0, 0].plot(trace["distance_m"], trace["actual_speed_mph"], color="#172127", label="Recorded")
    for row in summary_rows:
        axes[0, 0].plot(trace["distance_m"], trace[row["speed_key"]], label=f"{row['grip_scale']:.0%}")
    axes[0, 0].set(xlabel="Distance (m)", ylabel="Speed (mph)", title="Minimum-lap speed profiles")
    axes[0, 0].grid(alpha=0.25)
    axes[0, 0].legend(ncol=3, fontsize=8)
    axes[0, 1].plot(trace["x_m"], trace["y_m"], color="#172127")
    axes[0, 1].scatter(trace["x_m"].iloc[0], trace["y_m"].iloc[0], color="#dc2626", zorder=3)
    axes[0, 1].axis("equal")
    axes[0, 1].set(title="Reference GPS trace", xlabel="East (m)", ylabel="North (m)")
    axes[1, 0].plot(trace["distance_m"], trace[f"lateral_g_scale_{accel_key}"], label="Lateral")
    axes[1, 0].plot(trace["distance_m"], trace[f"longitudinal_g_scale_{accel_key}"], label="Longitudinal")
    axes[1, 0].set(xlabel="Distance (m)", ylabel="Acceleration (g)", title=f"{selected['grip_scale']:.0%} TTC case")
    axes[1, 0].grid(alpha=0.25)
    axes[1, 0].legend()
    axes[1, 1].plot([row["grip_scale"] for row in summary_rows], [row["lap_time_s"] for row in summary_rows], marker="o")
    axes[1, 1].set(xlabel="TTC grip scale", ylabel="Predicted lap time (s)", title="Surface sensitivity")
    axes[1, 1].grid(alpha=0.25)
    fig.suptitle("LC0 same-line minimum lap", fontsize=16)
    fig.savefig(path, dpi=160)
    plt.close(fig)


def model_run(args: argparse.Namespace) -> None:
    load_model_dependencies()
    args.out.mkdir(parents=True, exist_ok=True)
    scales = sorted(set(float(value) for value in args.grip_scales.split(",") if value.strip()))
    if not scales or any(value <= 0.0 or value > 1.5 for value in scales):
        raise SystemExit("--grip-scales must contain positive comma-separated values no greater than 1.5")
    if args.ellipse_exponent < 1.0:
        raise SystemExit("--ellipse-exponent must be at least 1.0")

    lap_rows, run_ids = model_load_lap_summary(args.lap_summary)
    valid_laps = [row for row in lap_rows if row["valid_for_benchmark"]]
    recorded_best = min(valid_laps, key=lambda row: row["lap_time_s"]) if valid_laps else None
    recorded_best_time = recorded_best["lap_time_s"] if recorded_best else args.reference_duration_s
    sector_composite = model_build_sector_composite(lap_rows)
    sector_target_time = sector_composite["lap_time_s"] if sector_composite else recorded_best_time

    reference = model_load_reference_lap(
        args.reference_csv,
        args.reference_start_row,
        args.reference_samples,
        args.reference_duration_s,
        args.grid_step_m,
        args.curvature_smoothing_m,
    )
    if args.propulsion_root and not run_ids:
        run_ids = [p.stem.removesuffix("_BINARY") for p in args.propulsion_root.rglob("*_BINARY.csv")]
    propulsion, propulsion_rows = model_fit_propulsion_envelope(args.propulsion_root, run_ids)

    parameters = tire_read_tir_parameters(args.tir)
    normal_load = args.normal_load_n or parameters["FNOMIN"]
    pressure_pa = (args.pressure_kpa or parameters["NOMPRES"] / 1000.0) * 1000.0
    mu_x, mu_y = tire_pure_slip_limits(parameters, normal_load, pressure_pa, args.camber_deg, 1.0)

    recorded_required_scale = (
        (np.abs(gaussian_filter1d(reference["actual_longitudinal_g"], sigma=2.0, mode="wrap")) / mu_x) ** args.ellipse_exponent
        + (np.abs(reference["curvature_1pm"] * reference["actual_speed_mps"] ** 2 / G_MPS2) / mu_y) ** args.ellipse_exponent
    ) ** (1.0 / args.ellipse_exponent)
    recorded_required_scale_p99 = float(np.percentile(recorded_required_scale, 99.0))

    solutions = {}
    summary_rows = []
    for scale in scales:
        solution = model_solve_minimum_lap(
            reference["curvature_1pm"],
            float(reference["ds_m"]),
            mu_y * scale,
            mu_x * scale,
            args.ellipse_exponent,
            propulsion,
            args.maximum_speed_mph / MPH_PER_MPS,
        )
        solutions[scale] = solution
        summary_rows.append({
            "grip_scale": scale,
            "lateral_limit_g": mu_y * scale,
            "longitudinal_limit_g": mu_x * scale,
            "lap_time_s": solution["lap_time_s"],
            "delta_vs_recorded_s": solution["lap_time_s"] - recorded_best_time,
            "delta_vs_sector_composite_s": solution["lap_time_s"] - sector_target_time,
            "time_potential_s": recorded_best_time - solution["lap_time_s"],
            "max_speed_mph": solution["max_speed_mph"],
            "mean_speed_mph": solution["mean_speed_mph"],
            "iterations": solution["iterations"],
            "speed_key": f"speed_mph_scale_{model_scale_key(scale)}",
            "recorded_floor_status": (
                "below recorded floor" if scale + 0.01 < recorded_required_scale_p99
                else "near recorded floor" if abs(scale - recorded_required_scale_p99) <= 0.02
                else "above recorded floor"
            ),
        })

    trace = pd.DataFrame({
        "distance_m": reference["distance_grid_m"],
        "x_m": reference["x_m"],
        "y_m": reference["y_m"],
        "curvature_1pm": reference["curvature_1pm"],
        "actual_speed_mph": reference["actual_speed_mps"] * MPH_PER_MPS,
        "actual_longitudinal_g": reference["actual_longitudinal_g"],
        "actual_lateral_g": reference["actual_lateral_g"],
    })
    for scale, solution in solutions.items():
        key = model_scale_key(scale)
        trace[f"speed_mph_scale_{key}"] = solution["speed_mps"] * MPH_PER_MPS
        trace[f"longitudinal_g_scale_{key}"] = solution["longitudinal_g"]
        trace[f"lateral_g_scale_{key}"] = solution["lateral_g"]
        trace[f"ellipse_utilization_scale_{key}"] = solution["utilization"]

    summary_fields = [
        "grip_scale", "lateral_limit_g", "longitudinal_limit_g", "lap_time_s",
        "delta_vs_recorded_s", "delta_vs_sector_composite_s", "time_potential_s", "max_speed_mph", "mean_speed_mph", "iterations", "speed_key",
        "recorded_floor_status",
    ]
    model_write_csv(args.out / "minimum_lap_summary.csv", summary_rows, summary_fields)
    trace.to_csv(args.out / "minimum_lap_trace.csv", index=False, float_format="%.7f")
    if lap_rows:
        model_write_csv(
            args.out / "recorded_lap_benchmark.csv",
            lap_rows,
            list(lap_rows[0].keys()),
        )
    if propulsion_rows:
        model_write_csv(
            args.out / "propulsion_envelope.csv",
            propulsion_rows,
            ["speed_center_mph", "samples", "p99_acceleration_g", "fitted_acceleration_g"],
        )

    model = {
        "model_name": "LC0 same-line quasi-steady-state minimum lap",
        "reference_csv": str(args.reference_csv.resolve()),
        "reference_start_row": args.reference_start_row,
        "reference_samples": args.reference_samples,
        "reference_duration_s": args.reference_duration_s,
        "reference_driver": args.reference_driver,
        "reference_lap": args.reference_lap,
        "course_distance_m": reference["distance_m"],
        "raw_integrated_heading_change_deg": reference["raw_integrated_heading_change_deg"],
        "integrated_heading_change_deg": reference["integrated_heading_change_deg"],
        "tir_file": str(args.tir.resolve()),
        "normal_load_n": normal_load,
        "pressure_kpa": pressure_pa / 1000.0,
        "camber_deg": args.camber_deg,
        "ellipse_exponent": args.ellipse_exponent,
        "base_lateral_limit_g": mu_y,
        "base_longitudinal_limit_g": mu_x,
        "grip_scales": scales,
        "recorded_best_time_s": recorded_best_time,
        "recorded_best": recorded_best,
        "observed_sector_composite": sector_composite,
        "recorded_required_scale_p99": recorded_required_scale_p99,
        "propulsion": propulsion,
        "curvature_smoothing_m": args.curvature_smoothing_m,
        "grid_step_m": args.grid_step_m,
        "limitations": [
            "Same recorded line; no racing-line or cone-boundary optimization",
            "Homothetic surface scaling of TTC-belt LC0 peaks",
            "First-order vehicle ellipse; no individual tire load transfer",
            "No transient, temperature, aero, differential, brake-balance, or grade model",
            "Propulsion cap is inferred from recorded straight-line acceleration",
        ],
    }
    (args.out / "minimum_lap_model.json").write_text(json.dumps(model, indent=2), encoding="utf-8")
    trace_rows = json.loads(trace.round(7).to_json(orient="records"))
    model_write_report(args.out / "lc0_minimum_lap.html", model, summary_rows, lap_rows, trace_rows)
    model_write_preview(args.out / "lc0_minimum_lap_preview.png", trace, summary_rows)

    print(f"Reference line: {reference['distance_m']:.1f} m, recorded {args.reference_duration_s:.3f} s")
    print(f"LC0 TTC limits: lateral={mu_y:.4f} g, longitudinal={mu_x:.4f} g")
    print(f"Propulsion fit: launch={propulsion['launch_limit_g']:.3f} g, specific power={propulsion['specific_power_w_per_kg']:.1f} W/kg")
    for row in summary_rows:
        print(f"  scale={row['grip_scale']:.2f}: {row['lap_time_s']:.3f} s ({row['delta_vs_recorded_s']:+.3f} s vs recorded)")
    print(f"Wrote {args.out / 'lc0_minimum_lap.html'}")


def model_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Predict a same-line minimum lap using an LC0 combined-slip constraint.\n\nThe reference course comes from one recorded VN-300 lap. Course distance is\nintegrated from measured speed and curvature is derived from yaw rate / speed,\nwhich avoids differentiating a noisy GPS trace. A cyclic forward/backward pass\nthen finds the power-, braking-, and tire-limited speed profile.\n\nThis is a quasi-steady-state lower bound, not a transient vehicle simulation or\nan optimized racing-line calculation.')
    parser.add_argument("--reference-csv", type=Path, required=True)
    parser.add_argument("--reference-start-row", type=int, required=True, help="zero-based first CSV data row of the reference lap")
    parser.add_argument("--reference-samples", type=int, required=True)
    parser.add_argument("--reference-duration-s", type=float, required=True)
    parser.add_argument("--reference-driver", default="Reference driver")
    parser.add_argument("--reference-lap", type=int, default=1)
    parser.add_argument("--lap-summary", type=Path, help="lap_sector_splits.csv used for recorded benchmarks")
    parser.add_argument("--propulsion-root", type=Path, help="folder containing BINARY CSV files used to fit acceleration")
    parser.add_argument("--tir", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("lc0_minimum_lap_output"))
    parser.add_argument("--normal-load-n", type=float, default=750.0)
    parser.add_argument("--pressure-kpa", type=float, default=82.7371, help="12 psi by default")
    parser.add_argument("--camber-deg", type=float, default=0.0)
    parser.add_argument("--ellipse-exponent", type=float, default=2.0)
    parser.add_argument("--grip-scales", default="0.50,0.55,0.60,0.65,0.70,0.80,1.00")
    parser.add_argument("--grid-step-m", type=float, default=0.5)
    parser.add_argument("--curvature-smoothing-m", type=float, default=2.5)
    parser.add_argument("--maximum-speed-mph", type=float, default=80.0)
    return parser


def model_main():
    model_run(model_parser().parse_args())


# Tire Ellipse Comparison
#!/usr/bin/env python3






TIRES = {
    "LC0": {
        "color": "#d1495b",
        "round": "TTC Round 6 / B1654",
        "runs": "cornering 30-31; drive-brake 44-45",
        "surface": "120-grit 3Mite, stoned once",
        "tested_tire_weights_lb": [7.25, 7.36],
        "fit": {"fx_r2": 0.6492, "fy_r2": 0.9856, "fx_rmse_n": 579.73, "fy_rmse_n": 132.81},
    },
    "R20": {
        "color": "#287271",
        "round": "TTC Round 9 / B2356",
        "runs": "cornering 27-29; drive-brake 68-70",
        "surface": "120-grit 3Mite, stoned",
        "tested_tire_weights_lb": [7.665, 7.68],
        "fit": {"fx_r2": 0.5958, "fy_r2": 0.9512, "fx_rmse_n": 588.03, "fy_rmse_n": 195.43},
    },
}


def comparison_write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def comparison_ellipse_points(lateral_g: float, longitudinal_g: float, exponent: float) -> list[dict]:
    rows = []
    for angle in range(361):
        radius = tire_theoretical_radius(angle, lateral_g, longitudinal_g, exponent)
        radians = math.radians(angle)
        rows.append({
            "angle_deg": angle,
            "lateral_g": radius * math.cos(radians),
            "longitudinal_g": radius * math.sin(radians),
            "combined_g": radius,
        })
    return rows


def comparison_fmt_delta(value: float) -> str:
    return f"{value:+.1f}%"


def comparison_write_html(path: Path, model: dict, load_rows: list[dict], plot_data: list[dict]) -> None:
    baseline = model["baseline"]
    lc0 = baseline["LC0"]
    r20 = baseline["R20"]
    lat_delta = 100.0 * (r20["lateral_limit_g"] / lc0["lateral_limit_g"] - 1.0)
    long_delta = 100.0 * (r20["longitudinal_limit_g"] / lc0["longitudinal_limit_g"] - 1.0)
    area_delta = 100.0 * (r20["ellipse_area_g2"] / lc0["ellipse_area_g2"] - 1.0)
    lc0_weight = sum(TIRES["LC0"]["tested_tire_weights_lb"]) / len(TIRES["LC0"]["tested_tire_weights_lb"])
    r20_weight = sum(TIRES["R20"]["tested_tire_weights_lb"]) / len(TIRES["R20"]["tested_tire_weights_lb"])
    weight_delta = r20_weight - lc0_weight
    rows_html = "".join(
        f"<tr><td>{row['normal_load_n']:.0f}</td><td>{row['LC0_lateral_g']:.3f}</td>"
        f"<td>{row['R20_lateral_g']:.3f}</td><td>{row['R20_vs_LC0_lateral_pct']:+.1f}%</td>"
        f"<td>{row['LC0_longitudinal_g']:.3f}</td><td>{row['R20_longitudinal_g']:.3f}</td>"
        f"<td>{row['R20_vs_LC0_longitudinal_pct']:+.1f}%</td></tr>"
        for row in load_rows
    )
    body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hoosier 18x6-10 LC0 vs R20 friction ellipse</title>
<style>
body{{margin:0;background:#f4f6f8;color:#19232c;font-family:Arial,Helvetica,sans-serif}}header{{background:#17202a;color:#f5f7f8;padding:18px 22px;border-bottom:3px solid #d1495b}}
main{{padding:16px;display:grid;gap:16px}}section{{background:#fff;border:1px solid #cbd5de;padding:14px;overflow:auto}}h1{{font-size:25px;margin:0 0 5px;letter-spacing:0}}h2{{font-size:18px;margin:0 0 10px;letter-spacing:0}}
.metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:1px;background:#cbd5de;border:1px solid #cbd5de}}.metric{{background:#fff;padding:12px}}.metric strong{{font-size:22px;display:block;margin-top:4px}}
.grid{{display:grid;grid-template-columns:minmax(420px,1.15fr) minmax(360px,.85fr);gap:16px}}canvas{{width:100%;height:520px;border:1px solid #cbd5de;background:#fbfcfd}}
table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{border-bottom:1px solid #dde4ea;padding:7px;text-align:right}}th:first-child,td:first-child{{text-align:left}}.note{{color:#52616d;font-size:13px;line-height:1.45}}.decision{{font-size:15px;line-height:1.5}}
.legend{{display:flex;gap:18px;flex-wrap:wrap;margin-top:8px;color:#52616d;font-size:13px}}.line{{display:inline-block;width:24px;height:3px;margin-right:6px;vertical-align:middle}}
@media(max-width:900px){{.grid{{grid-template-columns:1fr}}}}@media(max-width:520px){{main{{padding:8px}}canvas{{height:360px}}}}
</style></head><body>
<header><h1>Hoosier 18x6-10: LC0 vs R20</h1><div>Matched-condition basic combined-slip friction ellipses</div></header><main>
<div class="metrics"><div class="metric">R20 lateral change<strong>{comparison_fmt_delta(lat_delta)}</strong></div><div class="metric">R20 longitudinal change<strong>{comparison_fmt_delta(long_delta)}</strong></div><div class="metric">R20 ellipse-area change<strong>{comparison_fmt_delta(area_delta)}</strong></div><div class="metric">R20 tire mass change<strong>+{weight_delta:.2f} lb</strong></div><div class="metric">Baseline<strong>{model['normal_load_n']:.0f} N / {model['pressure_kpa']:.1f} kPa</strong></div></div>
<div class="grid"><section><h2>Friction Ellipse</h2><canvas id="plot" width="850" height="620"></canvas><div class="legend"><span><span class="line" style="background:#d1495b"></span>LC0</span><span><span class="line" style="background:#287271"></span>R20</span></div></section>
<section><h2>Decision Readout</h2><div class="decision"><p><strong>LC0 retains the lateral-grip advantage.</strong> At the matched baseline it produces {lc0['lateral_limit_g']:.3f} g versus {r20['lateral_limit_g']:.3f} g for R20.</p><p>Longitudinal peak is effectively tied: {lc0['longitudinal_limit_g']:.3f} g versus {r20['longitudinal_limit_g']:.3f} g. Treat that result cautiously because both longitudinal fits are only moderate.</p><p>The tested R20 tires averaged {r20_weight:.2f} lb versus {lc0_weight:.2f} lb for LC0, adding about {weight_delta:.2f} lb per corner or {4 * weight_delta:.2f} lb per set.</p><p>An exploratory within-condition check found no LC0 force fade as tread temperature rose: all 35 evaluable bins retained or gained normalized lateral force, with a median hot-versus-cool change of +12.8%. R20 was flatter across 66 bins, with a median change of -0.6%. This is consistent with R20 being more stable, but different rounds and test sequences prevent treating it as a controlled thermal A/B result.</p><p><strong>Do not switch solely to solve an LC0 overheating problem until on-car performance fade is demonstrated.</strong> R20 is a reasonable back-to-back test candidate if endurance consistency is the priority, but the present evidence does not justify accepting its lower lateral ceiling as a production choice.</p></div></section></div>
<section><h2>Load Sensitivity at {model['pressure_kpa']:.1f} kPa and {model['camber_deg']:.1f} Degrees Camber</h2><table><thead><tr><th>Load (N/tire)</th><th>LC0 lateral</th><th>R20 lateral</th><th>R20 delta</th><th>LC0 longitudinal</th><th>R20 longitudinal</th><th>R20 delta</th></tr></thead><tbody>{rows_html}</tbody></table></section>
<section class="note"><h2>Evidence and Limits</h2>LC0 source: TTC Round 6 B1654 runs 30-31 and 44-45, roadway recorded as 120-grit 3Mite, stoned once. R20 source: TTC Round 9 B2356 runs 27-29 and 68-70, roadway recorded as 120-grit 3Mite, stoned. The comparison evaluates both MF6.1 files at the same per-tire load, pressure, camber, grip scale, and ellipse exponent. Lateral fit quality: LC0 R2 0.986, R20 R2 0.951. Longitudinal fit quality: LC0 R2 0.649, R20 R2 0.596. This is a steady-state tire comparison; it does not predict dusty-asphalt scaling, vehicle load transfer, transient response, tire life, warm-up time, or lap time.</section>
</main><script>
const data={json.dumps(plot_data)};const ctx=document.getElementById('plot').getContext('2d'),w=ctx.canvas.width,h=ctx.canvas.height,pad=55,lim=2.75;
const sx=x=>pad+(x+lim)/(2*lim)*(w-2*pad),sy=y=>h-pad-(y+lim)/(2*lim)*(h-2*pad);ctx.fillStyle='#fbfcfd';ctx.fillRect(0,0,w,h);ctx.font='12px Arial';ctx.fillStyle='#667581';ctx.strokeStyle='#e0e6eb';
for(let g=-2.5;g<=2.5;g+=.5){{ctx.beginPath();ctx.moveTo(sx(g),sy(-lim));ctx.lineTo(sx(g),sy(lim));ctx.moveTo(sx(-lim),sy(g));ctx.lineTo(sx(lim),sy(g));ctx.stroke();if(Math.abs(g)>.01){{ctx.fillText(g.toFixed(1),sx(g)-10,sy(0)+16);ctx.fillText(g.toFixed(1),sx(0)+7,sy(g)+4)}}}}
ctx.strokeStyle='#7b8790';ctx.lineWidth=1.3;ctx.beginPath();ctx.moveTo(sx(-lim),sy(0));ctx.lineTo(sx(lim),sy(0));ctx.moveTo(sx(0),sy(-lim));ctx.lineTo(sx(0),sy(lim));ctx.stroke();
for(const tire of data){{ctx.strokeStyle=tire.color;ctx.lineWidth=3;ctx.beginPath();tire.points.forEach((p,i)=>i?ctx.lineTo(sx(p[0]),sy(p[1])):ctx.moveTo(sx(p[0]),sy(p[1])));ctx.closePath();ctx.stroke()}}
ctx.fillStyle='#19232c';ctx.font='13px Arial';ctx.fillText('Lateral G',w/2-28,h-13);ctx.save();ctx.translate(16,h/2+43);ctx.rotate(-Math.PI/2);ctx.fillText('Longitudinal G',0,0);ctx.restore();ctx.fillStyle='#667581';ctx.fillText('accel',sx(-lim)+4,sy(lim)+15);ctx.fillText('braking',sx(-lim)+4,sy(-lim)+15);
</script></body></html>"""
    path.write_text(body, encoding="utf-8")


def comparison_run(args: argparse.Namespace) -> None:
    if args.normal_load_n <= 0 or args.pressure_kpa <= 0:
        raise SystemExit("Load and pressure must be positive.")
    if args.ellipse_exponent < 1.0:
        raise SystemExit("--ellipse-exponent must be at least 1.0")
    parameters = {"LC0": tire_read_tir_parameters(args.lc0_tir), "R20": tire_read_tir_parameters(args.r20_tir)}
    paths = {"LC0": args.lc0_tir, "R20": args.r20_tir}
    loads = sorted(set(args.load_sweep_n + [args.normal_load_n]))
    baseline: dict[str, dict] = {}
    load_rows = []
    for load in loads:
        limits = {}
        for name in TIRES:
            mu_x, mu_y = tire_pure_slip_limits(
                parameters[name], load, args.pressure_kpa * 1000.0, args.camber_deg, args.grip_scale
            )
            limits[name] = {"longitudinal_g": mu_x, "lateral_g": mu_y}
            if load == args.normal_load_n:
                baseline[name] = {
                    "longitudinal_limit_g": mu_x,
                    "lateral_limit_g": mu_y,
                    "ellipse_area_g2": math.pi * mu_x * mu_y,
                }
        load_rows.append({
            "normal_load_n": load,
            "LC0_lateral_g": limits["LC0"]["lateral_g"],
            "R20_lateral_g": limits["R20"]["lateral_g"],
            "R20_vs_LC0_lateral_pct": 100.0 * (limits["R20"]["lateral_g"] / limits["LC0"]["lateral_g"] - 1.0),
            "LC0_longitudinal_g": limits["LC0"]["longitudinal_g"],
            "R20_longitudinal_g": limits["R20"]["longitudinal_g"],
            "R20_vs_LC0_longitudinal_pct": 100.0 * (limits["R20"]["longitudinal_g"] / limits["LC0"]["longitudinal_g"] - 1.0),
        })

    model = {
        "normal_load_n": args.normal_load_n,
        "pressure_kpa": args.pressure_kpa,
        "camber_deg": args.camber_deg,
        "grip_scale": args.grip_scale,
        "ellipse_exponent": args.ellipse_exponent,
        "baseline": baseline,
        "tires": {name: {**TIRES[name], "tir_file": str(paths[name].resolve())} for name in TIRES},
    }
    ellipse_rows = []
    plot_data = []
    for name in TIRES:
        rows = comparison_ellipse_points(
            baseline[name]["lateral_limit_g"], baseline[name]["longitudinal_limit_g"], args.ellipse_exponent
        )
        ellipse_rows.extend({"tire": name, **row} for row in rows)
        plot_data.append({
            "name": name,
            "color": TIRES[name]["color"],
            "points": [[row["lateral_g"], row["longitudinal_g"]] for row in rows],
        })

    args.out.mkdir(parents=True, exist_ok=True)
    comparison_write_csv(
        args.out / "lc0_vs_r20_load_sweep.csv", load_rows,
        ["normal_load_n", "LC0_lateral_g", "R20_lateral_g", "R20_vs_LC0_lateral_pct", "LC0_longitudinal_g", "R20_longitudinal_g", "R20_vs_LC0_longitudinal_pct"],
    )
    comparison_write_csv(
        args.out / "lc0_vs_r20_ellipse_points.csv", ellipse_rows,
        ["tire", "angle_deg", "lateral_g", "longitudinal_g", "combined_g"],
    )
    (args.out / "lc0_vs_r20_model.json").write_text(json.dumps(model, indent=2), encoding="utf-8")
    comparison_write_html(args.out / "lc0_vs_r20_friction_ellipse.html", model, load_rows, plot_data)

    lc0, r20 = baseline["LC0"], baseline["R20"]
    print(f"LC0: lateral={lc0['lateral_limit_g']:.4f} g, longitudinal={lc0['longitudinal_limit_g']:.4f} g")
    print(f"R20: lateral={r20['lateral_limit_g']:.4f} g, longitudinal={r20['longitudinal_limit_g']:.4f} g")
    print(f"R20 lateral delta: {100 * (r20['lateral_limit_g'] / lc0['lateral_limit_g'] - 1):+.2f}%")
    print(f"Wrote {args.out / 'lc0_vs_r20_friction_ellipse.html'}")


def comparison_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Compare LC0 and R20 tire-limited friction ellipses at matched conditions.')
    parser.add_argument("--lc0-tir", type=Path, required=True)
    parser.add_argument("--r20-tir", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("tire_ellipse_comparison_output"))
    parser.add_argument("--normal-load-n", type=float, default=750.0, help="matched per-tire comparison load")
    parser.add_argument("--pressure-kpa", type=float, default=82.737, help="matched inflation pressure; 82.737 kPa = 12 psi")
    parser.add_argument("--camber-deg", type=float, default=0.0)
    parser.add_argument("--grip-scale", type=float, default=1.0, help="common surface multiplier; does not affect relative result")
    parser.add_argument("--ellipse-exponent", type=float, default=2.0)
    parser.add_argument("--load-sweep-n", type=float, nargs="+", default=[300, 500, 750, 1000, 1250, 1500])
    return parser


def comparison_main():
    comparison_run(comparison_parser().parse_args())


COMMANDS = {
    "laps": laps_main,
    "gg": gg_main,
    "tire-model": tire_main,
    "minimum-lap": model_main,
    "tire-comparison": comparison_main,
}


COMMAND_PARSERS = {"laps": laps_parser, "gg": gg_parser, "tire-model": tire_parser,
                   "minimum-lap": model_parser, "tire-comparison": comparison_parser}


def main():
    import sys
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=COMMANDS)
    args = parser.parse_args(sys.argv[1:2])
    # Existing parsers keep their full argument handling and help text.
    original_argv = sys.argv[:]
    sys.argv = [f"{original_argv[0]} {args.command}", *original_argv[2:]]
    try:
        COMMANDS[args.command]()
    finally:
        sys.argv = original_argv


if __name__ == "__main__":
    main()
