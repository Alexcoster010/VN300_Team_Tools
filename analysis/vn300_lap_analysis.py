#!/usr/bin/env python3
"""
Offline VN-300 VNINS CSV analysis.

Use cases:
- Summarize real runs.
- Split laps by a GPS start/finish line or autocross runs by separate
  start and finish lines.
- Compare multiple laps with a standalone HTML overlay.
- Export per-lap/run CSVs with live delta to the best previous reference.

If mode and line coordinates are not passed as arguments, the script prompts
for them interactively.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
from dataclasses import dataclass
from pathlib import Path


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
    while current - previous > math.pi:
        current -= 2 * math.pi
    while current - previous < -math.pi:
        current += 2 * math.pi
    return current


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


def write_lap_sector_outputs(
    out_dir: Path,
    rows: list[dict],
    excluded_rows: list[dict],
    sector_count: int = 3,
):
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
    cards.append(f"<div class=\"card\"><div class=\"note\">Overall Theoretical Best</div><div class=\"big\">{overall_theoretical:.3f} s</div></div>")
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
<header><h1>VN300 Lap Times And Sector Splits</h1><div class="note">Three sectors are split by thirds of each segment's GPS trace distance. Purple highlights the overall fastest sector in each sector column.</div></header>
<main>
<section class="cards">{''.join(cards)}</section>
<section><h2>Overall Best Sectors</h2>{best_sector_table()}</section>
<section><h2>Theoretical Best By Driver</h2>{theoretical_table()}</section>
<section><h2>All Lap Times And Sector Splits</h2>{all_laps_table()}</section>
</main>
</body>
</html>"""
    (out_dir / "lap_times_sector_splits.html").write_text(body, encoding="utf-8")


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


def write_report_html(path: Path, summaries: list[dict], quality_reports: list[dict], segments: list[tuple[str, list[Sample]]]):
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


def main():
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
    args = parser.parse_args()

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

    write_summary(args.out / "summary.csv", summaries)
    write_summary(args.out / "data_quality.csv", quality_reports)
    if sector_summaries:
        write_sector_summary(args.out / "sector_summary.csv", sector_summaries)
    write_lap_sector_outputs(args.out, lap_sector_rows, lap_sector_excluded_rows)
    write_overlay_html(args.out / "overlay.html", overlay_laps)
    write_report_html(args.out / "report.html", summaries, quality_reports, report_segments)
    print(f"Wrote {args.out / 'summary.csv'}")
    print(f"Wrote {args.out / 'data_quality.csv'}")
    if sector_summaries:
        print(f"Wrote {args.out / 'sector_summary.csv'}")
    if lap_sector_rows:
        print(f"Wrote {args.out / 'lap_times_sector_splits.html'}")
        print(f"Wrote {args.out / 'lap_sector_splits.csv'}")
        print(f"Wrote {args.out / 'theoretical_best_by_driver.csv'}")
        print(f"Wrote {args.out / 'overall_best_sectors.csv'}")
    print(f"Wrote {args.out / 'overlay.html'}")
    print(f"Wrote {args.out / 'report.html'}")


if __name__ == "__main__":
    main()
