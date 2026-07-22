#!/usr/bin/env python3
"""Measured G-G-envelope minimum-lap solver used by the VN300 analyzer."""

from __future__ import annotations

import bisect
import math
import statistics


G_MPS2 = 9.80665
MPH_PER_MPS = 2.2369362921


def percentile(values: list[float], fraction: float) -> float | None:
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
            radius = percentile(radii, envelope_percentile) if len(radii) >= min_bin_points else None
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
            "p_acceleration_g": percentile(values, envelope_percentile),
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
