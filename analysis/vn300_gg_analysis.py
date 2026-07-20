#!/usr/bin/env python3
"""
Offline VN-300 G-G diagram analysis.

Generates corrected lateral-vs-longitudinal G-G diagrams by driver, including
GPS/data-quality filtering and a directional percentile envelope.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
from pathlib import Path

from vn300_lap_analysis import (
    expand_input_paths,
    latest_run_metadata_path,
    load_metadata,
    metadata_for_path,
    session_stem_from_name,
    write_summary,
)


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


def parse_float(row: dict, name: str) -> float | None:
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


def percentile(values: list[float], p: float) -> float | None:
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


def decimate(points: list[tuple], max_points: int) -> tuple[list[tuple], int]:
    if len(points) <= max_points:
        return points, 1
    step = math.ceil(len(points) / max_points)
    return points[::step], step


def load_driver_map(path: Path | None) -> dict[str, str]:
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


def driver_from_sources(
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


def point_allowed(row: dict, args: argparse.Namespace) -> tuple[bool, str, tuple | None]:
    lateral_g = parse_float(row, "Lateral_G")
    longitudinal_g = parse_float(row, "Longitudinal_G")
    speed_mph = parse_float(row, "Speed_mph")
    pos_uncertainty_m = parse_float(row, "PosUncertainty_m")
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


def build_envelope(
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
            radius = percentile([point[3] for point in bin_points], args.envelope_percentile)
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


def html_table(rows: list[dict], columns: list[str]) -> str:
    head = "".join(f"<th>{html.escape(column)}</th>" for column in columns)
    body = []
    for row in rows:
        cells = "".join(f"<td>{html.escape(str(row.get(column, '')))}</td>" for column in columns)
        body.append(f"<tr>{cells}</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def write_gg_html(path: Path, data: list[dict], summary_rows: list[dict], args: argparse.Namespace):
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
<section><h2>Summary</h2>{html_table(summary_rows, summary_columns)}</section>
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


def run_analysis(args: argparse.Namespace):
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

    grouped = {}
    excluded_by_driver = {}
    colors = ["#48cae4", "#f72585", "#80ed99", "#ffd166", "#bdb2ff", "#ff9f1c", "#06d6a0", "#3a86ff", "#ef476f"]

    for index, path in enumerate(csv_paths):
        driver = driver_from_sources(path, index, metadata, driver_map, driver_order, args.driver_order_offset)
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
                allowed, reason, point = point_allowed(row, args)
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
        envelope, driver_envelope_rows = build_envelope(points, driver, run_ids, args)
        envelope_rows.extend(driver_envelope_rows)
        plot_points, step = decimate(points, args.max_plot_points)
        peak = max(points, key=lambda point: point[3])
        envelope_peak = max((point[3] for point in envelope), default=0.0)
        max_accel = max((point[1] for point in points), default=0.0)
        max_brake = min((point[1] for point in points), default=0.0)
        max_left = min((point[0] for point in points), default=0.0)
        max_right = max((point[0] for point in points), default=0.0)
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

    write_summary(args.out / "gg_summary_by_driver.csv", summary_rows)
    write_summary(args.out / "gg_envelope_by_driver.csv", envelope_rows)
    write_gg_html(args.out / "gg_diagrams_by_driver.html", html_data, summary_rows, args)
    print(f"Wrote {args.out / 'gg_diagrams_by_driver.html'}")
    print(f"Wrote {args.out / 'gg_summary_by_driver.csv'}")
    print(f"Wrote {args.out / 'gg_envelope_by_driver.csv'}")


def main():
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
    args = parser.parse_args()

    if not (0.0 < args.envelope_percentile <= 1.0):
        raise SystemExit("--envelope-percentile must be in the range (0, 1].")
    if 360 % args.angle_bin_deg != 0:
        raise SystemExit("--angle-bin-deg must divide evenly into 360.")
    run_analysis(args)


if __name__ == "__main__":
    main()
