#!/usr/bin/env python3
"""Predict a same-line minimum lap using an LC0 combined-slip constraint.

The reference course comes from one recorded VN-300 lap. Course distance is
integrated from measured speed and curvature is derived from yaw rate / speed,
which avoids differentiating a noisy GPS trace. A cyclic forward/backward pass
then finds the power-, braking-, and tire-limited speed profile.

This is a quasi-steady-state lower bound, not a transient vehicle simulation or
an optimized racing-line calculation.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.ndimage import gaussian_filter1d
from scipy.optimize import least_squares

from lc0_theoretical_gg import pure_slip_limits, read_tir_parameters


G_MPS2 = 9.80665
MPH_PER_MPS = 2.2369362921
EARTH_RADIUS_M = 6371000.0


def finite_interpolated(series: pd.Series, low: float, high: float) -> np.ndarray:
    values = pd.to_numeric(series, errors="coerce").astype(float)
    values = values.where(np.isfinite(values) & values.between(low, high))
    return values.interpolate(limit_direction="both").bfill().ffill().to_numpy(dtype=float)


def local_xy(lat: np.ndarray, lon: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    lat0 = float(np.nanmedian(lat))
    lon0 = float(np.nanmedian(lon))
    x = np.radians(lon - lon0) * EARTH_RADIUS_M * math.cos(math.radians(lat0))
    y = np.radians(lat - lat0) * EARTH_RADIUS_M
    return x, y


def load_reference_lap(
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

    speed = finite_interpolated(segment["Speed_mps"], 0.0, 45.0)
    long_g = finite_interpolated(segment["Longitudinal_G"], -3.0, 3.0)
    lat_g = finite_interpolated(segment["Lateral_G"], -3.0, 3.0)
    yaw_column = "Common_AngularRate_Z_rps" if "Common_AngularRate_Z_rps" in segment else "Imu_AngularRate_Z_rps"
    yaw_rate = finite_interpolated(segment[yaw_column], -6.0, 6.0)

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

    x, y = local_xy(lat, lon)
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


def propulsion_model(v_mps: np.ndarray, launch_limit_g: float, specific_power_w_per_kg: float) -> np.ndarray:
    return np.minimum(launch_limit_g, specific_power_w_per_kg / (G_MPS2 * np.maximum(v_mps, 2.0)))


def fit_propulsion_envelope(root: Path | None, run_ids: list[str]) -> tuple[dict, list[dict]]:
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
            return propulsion_model(x, parameters[0], parameters[1]) - y

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
        row["fitted_acceleration_g"] = float(propulsion_model(np.array([v]), launch_limit_g, specific_power)[0])
    return model, combined


def ellipse_longitudinal_capacity_g(
    speed_mps: float,
    curvature_1pm: float,
    lateral_limit_g: float,
    longitudinal_limit_g: float,
    exponent: float,
) -> float:
    lateral_g = abs(curvature_1pm) * speed_mps * speed_mps / G_MPS2
    fraction = min(1.0, lateral_g / lateral_limit_g)
    return longitudinal_limit_g * max(0.0, 1.0 - fraction**exponent) ** (1.0 / exponent)


def solve_minimum_lap(
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
            tire_g = ellipse_longitudinal_capacity_g(
                speed[source], curvature[source], lateral_limit_g, longitudinal_limit_g, exponent
            )
            engine_g = float(propulsion_model(
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
                    available_g = ellipse_longitudinal_capacity_g(
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


def load_lap_summary(path: Path | None) -> tuple[list[dict], list[str]]:
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


def build_sector_composite(lap_rows: list[dict]) -> dict | None:
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


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def scale_key(scale: float) -> str:
    return f"{scale:.2f}".replace(".", "p")


def format_delta(value: float) -> str:
    return f"{value:+.3f}"


def write_report(
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
    sector_composite = model.get("observed_sector_composite")
    sector_target = sector_composite["lap_time_s"] if sector_composite else model["recorded_best_time_s"]

    case_table = "".join(
        "<tr>"
        f"<td>{row['grip_scale']:.0%}</td><td>{row['lateral_limit_g']:.3f}</td>"
        f"<td>{row['longitudinal_limit_g']:.3f}</td><td>{row['lap_time_s']:.3f}</td>"
        f"<td>{format_delta(row['delta_vs_sector_composite_s'])}</td><td>{row['max_speed_mph']:.1f}</td><td>{row['recorded_floor_status']}</td>"
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
</style></head><body><header><h1>LC0 Same-Line Minimum Lap</h1><div>Duck Pond drive day, Alex C lap 3 reference line</div></header><main>
<div class="metrics"><div class="metric">Fastest recorded valid lap<strong>{model['recorded_best_time_s']:.3f} s</strong></div><div class="metric">Observed sector composite<strong>{sector_target:.3f} s</strong></div><div class="metric">Demonstrated execution potential<strong>{model['recorded_best_time_s'] - sector_target:.3f} s</strong></div><div class="metric">Recorded utilization floor<strong>{model['recorded_required_scale_p99']:.0%} TTC</strong></div></div>
<section><h2>Interpretation</h2><p class="warning"><strong>{sector_target:.3f} s is the primary actionable target.</strong> It combines sector times already demonstrated on the same car and dusty surface. Because those sectors came from separate laps, their entry and exit states may not stitch together perfectly, but this is much more credible as a clean-run target than the steady-state tire model.</p><p>The friction-ellipse cases below are mathematical lower bounds, not achievable-lap predictions. They apply steady-state tire force instantaneously and omit transient response, steering transitions, individual-tire load transfer, driven-axle and differential limits, brake balance, temperature, and cone-clearance constraints. The recorded trajectory required {model['recorded_required_scale_p99']:.0%} of the TTC ellipse at its robust 99th percentile; that is only a lower bound on grip available on the day. The 100% case is the Calspan-belt upper bound and should not be treated as attainable on dusty asphalt.</p></section>
<section><h2>Observed Sector Composite</h2><table><thead><tr><th>Sector</th><th>Time (s)</th><th>Driver</th><th>Run</th><th>Lap</th></tr></thead><tbody>{sector_table}</tbody></table></section>
<section><h2>Steady-State Lower-Bound Sensitivity</h2><table><thead><tr><th>TTC scale</th><th>Lateral limit (g)</th><th>Longitudinal limit (g)</th><th>Lower bound (s)</th><th>vs sector target (s)</th><th>Max speed (mph)</th><th>Observed-data check</th></tr></thead><tbody>{case_table}</tbody></table></section>
<section><h2>Speed Profile</h2><canvas id="speed"></canvas></section>
<div class="grid"><section><h2>65% Acceleration Profile</h2><canvas id="accel"></canvas></section><section><h2>Reference GPS Trace</h2><canvas id="track"></canvas></section></div>
<section><h2>Best Valid Lap By Driver</h2><table><thead><tr><th>Driver</th><th>Best lap (s)</th><th>Run</th><th>Lap</th><th>Gap to sector target (s)</th></tr></thead><tbody>{driver_table}</tbody></table></section>
<section class="note"><h2>Model Definition And Limits</h2><p>LC0 input: <code>{model['tir_file']}</code>. Per-tire condition: {model['normal_load_n']:.0f} N, {model['pressure_kpa']:.1f} kPa, {model['camber_deg']:.1f} deg camber. Base TTC limits: {model['base_lateral_limit_g']:.3f} g lateral and {model['base_longitudinal_limit_g']:.3f} g longitudinal. Propulsion fit: launch cap {model['propulsion']['launch_limit_g']:.3f} g, specific power {model['propulsion']['specific_power_w_per_kg']:.1f} W/kg, RMSE {model['propulsion']['fit_rmse_g']:.3f} g.</p><p>Excluded effects: racing-line optimization, individual tire load transfer, aero load and drag, banking/grade, tire temperature, transient relaxation, differential and driven-axle limits, brake balance, steering limits, cone clearance, driver reaction, and surface changes. Course distance comes from speed integration; curvature comes from smoothed yaw rate divided by speed. Gyro-derived heading accumulated {model['raw_integrated_heading_change_deg']:.1f} deg and was closed to {model['integrated_heading_change_deg']:.1f} deg for the periodic lap.</p></section>
</main><script>
const data={payload};const trace=data.trace;const colors=['#2563eb','#0f766e','#d97706','#dc2626','#7c3aed','#059669','#92400e'];
function chart(id,series,xKey,yMin,yMax,xLabel,yLabel){{const c=document.getElementById(id),dpr=window.devicePixelRatio||1,w=c.clientWidth,h=c.clientHeight;c.width=w*dpr;c.height=h*dpr;const g=c.getContext('2d');g.scale(dpr,dpr);g.clearRect(0,0,w,h);const m={{l:48,r:14,t:14,b:34}},pw=w-m.l-m.r,ph=h-m.t-m.b;const xs=trace.map(r=>r[xKey]);const xmin=Math.min(...xs),xmax=Math.max(...xs);g.strokeStyle='#dfe5e8';g.lineWidth=1;for(let i=0;i<=5;i++){{let y=m.t+ph*i/5;g.beginPath();g.moveTo(m.l,y);g.lineTo(w-m.r,y);g.stroke();g.fillStyle='#5b6870';g.font='11px Arial';g.fillText((yMax-(yMax-yMin)*i/5).toFixed(1),4,y+4)}}for(let i=0;i<=5;i++){{let x=m.l+pw*i/5;g.fillStyle='#5b6870';g.fillText((xmin+(xmax-xmin)*i/5).toFixed(0),x-8,h-12)}}for(const [j,s] of series.entries()){{g.strokeStyle=s.color||colors[j%colors.length];g.lineWidth=s.width||2;g.beginPath();let started=false;for(const r of trace){{const xv=r[xKey],yv=r[s.key];if(!Number.isFinite(yv))continue;const x=m.l+(xv-xmin)/(xmax-xmin)*pw,y=m.t+(yMax-yv)/(yMax-yMin)*ph;if(!started){{g.moveTo(x,y);started=true}}else g.lineTo(x,y)}}g.stroke()}}g.fillStyle='#172127';g.font='12px Arial';g.fillText(xLabel,m.l+pw/2-35,h-2);g.save();g.translate(12,m.t+ph/2+25);g.rotate(-Math.PI/2);g.fillText(yLabel,0,0);g.restore();let lx=m.l;for(const [j,s] of series.entries()){{g.fillStyle=s.color||colors[j%colors.length];g.fillRect(lx,m.t,14,3);g.fillStyle='#172127';g.fillText(s.name,lx+18,m.t+5);lx+=18+g.measureText(s.name).width+18}}}}
function track(){{const c=document.getElementById('track'),dpr=window.devicePixelRatio||1,w=c.clientWidth,h=c.clientHeight;c.width=w*dpr;c.height=h*dpr;const g=c.getContext('2d');g.scale(dpr,dpr);const xs=trace.map(r=>r.x_m),ys=trace.map(r=>r.y_m),xmin=Math.min(...xs),xmax=Math.max(...xs),ymin=Math.min(...ys),ymax=Math.max(...ys),pad=18,s=Math.min((w-2*pad)/(xmax-xmin),(h-2*pad)/(ymax-ymin));g.strokeStyle='#172127';g.lineWidth=3;g.beginPath();trace.forEach((r,i)=>{{const x=w/2+(r.x_m-(xmin+xmax)/2)*s,y=h/2-(r.y_m-(ymin+ymax)/2)*s;i?g.lineTo(x,y):g.moveTo(x,y)}});g.closePath();g.stroke();g.fillStyle='#dc2626';const a=trace[0];g.beginPath();g.arc(w/2+(a.x_m-(xmin+xmax)/2)*s,h/2-(a.y_m-(ymin+ymax)/2)*s,5,0,2*Math.PI);g.fill()}}
function draw(){{const speedSeries=[{{name:'Recorded',key:'actual_speed_mph',color:'#172127',width:2}}];data.cases.forEach((c,i)=>speedSeries.push({{name:(c.grip_scale*100).toFixed(0)+'%',key:c.speed_key,color:colors[i]}}));chart('speed',speedSeries,'distance_m',0,Math.max(...trace.flatMap(r=>speedSeries.map(s=>r[s.key]||0)))*1.08,'Distance (m)','Speed (mph)');chart('accel',[{{name:'Lateral',key:'lateral_g_scale_0p65',color:'#2563eb'}},{{name:'Longitudinal',key:'longitudinal_g_scale_0p65',color:'#dc2626'}}],'distance_m',-2,2,'Distance (m)','Acceleration (g)');track()}}window.addEventListener('resize',draw);draw();
</script></body></html>"""
    path.write_text(body, encoding="utf-8")


def write_preview(path: Path, trace: pd.DataFrame, summary_rows: list[dict]) -> None:
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
    axes[1, 0].plot(trace["distance_m"], trace["lateral_g_scale_0p65"], label="Lateral")
    axes[1, 0].plot(trace["distance_m"], trace["longitudinal_g_scale_0p65"], label="Longitudinal")
    axes[1, 0].set(xlabel="Distance (m)", ylabel="Acceleration (g)", title="65% TTC case")
    axes[1, 0].grid(alpha=0.25)
    axes[1, 0].legend()
    axes[1, 1].plot([row["grip_scale"] for row in summary_rows], [row["lap_time_s"] for row in summary_rows], marker="o")
    axes[1, 1].set(xlabel="TTC grip scale", ylabel="Predicted lap time (s)", title="Surface sensitivity")
    axes[1, 1].grid(alpha=0.25)
    fig.suptitle("LC0 same-line minimum lap", fontsize=16)
    fig.savefig(path, dpi=160)
    plt.close(fig)


def run(args: argparse.Namespace) -> None:
    args.out.mkdir(parents=True, exist_ok=True)
    scales = sorted(set(float(value) for value in args.grip_scales.split(",") if value.strip()))
    if not scales or any(value <= 0.0 or value > 1.5 for value in scales):
        raise SystemExit("--grip-scales must contain positive comma-separated values no greater than 1.5")
    if args.ellipse_exponent < 1.0:
        raise SystemExit("--ellipse-exponent must be at least 1.0")

    lap_rows, run_ids = load_lap_summary(args.lap_summary)
    valid_laps = [row for row in lap_rows if row["valid_for_benchmark"]]
    recorded_best = min(valid_laps, key=lambda row: row["lap_time_s"]) if valid_laps else None
    recorded_best_time = recorded_best["lap_time_s"] if recorded_best else args.reference_duration_s
    sector_composite = build_sector_composite(lap_rows)
    sector_target_time = sector_composite["lap_time_s"] if sector_composite else recorded_best_time

    reference = load_reference_lap(
        args.reference_csv,
        args.reference_start_row,
        args.reference_samples,
        args.reference_duration_s,
        args.grid_step_m,
        args.curvature_smoothing_m,
    )
    propulsion, propulsion_rows = fit_propulsion_envelope(args.propulsion_root, run_ids)

    parameters = read_tir_parameters(args.tir)
    normal_load = args.normal_load_n or parameters["FNOMIN"]
    pressure_pa = (args.pressure_kpa or parameters["NOMPRES"] / 1000.0) * 1000.0
    mu_x, mu_y = pure_slip_limits(parameters, normal_load, pressure_pa, args.camber_deg, 1.0)

    recorded_required_scale = (
        (np.abs(gaussian_filter1d(reference["actual_longitudinal_g"], sigma=2.0, mode="wrap")) / mu_x) ** args.ellipse_exponent
        + (np.abs(reference["curvature_1pm"] * reference["actual_speed_mps"] ** 2 / G_MPS2) / mu_y) ** args.ellipse_exponent
    ) ** (1.0 / args.ellipse_exponent)
    recorded_required_scale_p99 = float(np.percentile(recorded_required_scale, 99.0))

    solutions = {}
    summary_rows = []
    for scale in scales:
        solution = solve_minimum_lap(
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
            "speed_key": f"speed_mph_scale_{scale_key(scale)}",
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
        key = scale_key(scale)
        trace[f"speed_mph_scale_{key}"] = solution["speed_mps"] * MPH_PER_MPS
        trace[f"longitudinal_g_scale_{key}"] = solution["longitudinal_g"]
        trace[f"lateral_g_scale_{key}"] = solution["lateral_g"]
        trace[f"ellipse_utilization_scale_{key}"] = solution["utilization"]

    summary_fields = [
        "grip_scale", "lateral_limit_g", "longitudinal_limit_g", "lap_time_s",
        "delta_vs_recorded_s", "delta_vs_sector_composite_s", "time_potential_s", "max_speed_mph", "mean_speed_mph", "iterations", "speed_key",
        "recorded_floor_status",
    ]
    write_csv(args.out / "minimum_lap_summary.csv", summary_rows, summary_fields)
    trace.to_csv(args.out / "minimum_lap_trace.csv", index=False, float_format="%.7f")
    if lap_rows:
        write_csv(
            args.out / "recorded_lap_benchmark.csv",
            lap_rows,
            list(lap_rows[0].keys()),
        )
    if propulsion_rows:
        write_csv(
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
    write_report(args.out / "lc0_minimum_lap.html", model, summary_rows, lap_rows, trace_rows)
    write_preview(args.out / "lc0_minimum_lap_preview.png", trace, summary_rows)

    print(f"Reference line: {reference['distance_m']:.1f} m, recorded {args.reference_duration_s:.3f} s")
    print(f"LC0 TTC limits: lateral={mu_y:.4f} g, longitudinal={mu_x:.4f} g")
    print(f"Propulsion fit: launch={propulsion['launch_limit_g']:.3f} g, specific power={propulsion['specific_power_w_per_kg']:.1f} W/kg")
    for row in summary_rows:
        print(f"  scale={row['grip_scale']:.2f}: {row['lap_time_s']:.3f} s ({row['delta_vs_recorded_s']:+.3f} s vs recorded)")
    print(f"Wrote {args.out / 'lc0_minimum_lap.html'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
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
    run(parser.parse_args())


if __name__ == "__main__":
    main()
