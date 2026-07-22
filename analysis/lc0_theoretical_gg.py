#!/usr/bin/env python3
"""Compare recorded G-G envelopes with an LC0 tire-limited friction ellipse.

This is deliberately a first-order vehicle model. Pure-slip friction
coefficients come from an MF6.1 TIR file and combined slip is represented by
a superellipse. It does not model power, brake balance, load transfer,
downforce, temperature, or the difference between the TTC belt and pavement.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import re
from collections import defaultdict
from pathlib import Path


MODEL_NAME = "Hoosier 18x6-10 LC0"
TTC_RUNS = "TTC Round 6 / B1654: cornering 30-31, drive-brake 44-45"
TTC_SURFACE = "120-grit 3Mite abrasive flat belt, stoned once"


def parse_number(value: object) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def read_tir_parameters(path: Path) -> dict[str, float]:
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


def pure_slip_limits(
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


def theoretical_radius(angle_deg: float, lateral_limit_g: float, longitudinal_limit_g: float, exponent: float) -> float:
    angle = math.radians(angle_deg)
    lateral_part = abs(math.cos(angle)) / lateral_limit_g
    longitudinal_part = abs(math.sin(angle)) / longitudinal_limit_g
    return (lateral_part**exponent + longitudinal_part**exponent) ** (-1.0 / exponent)


def load_envelopes(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(f"Driver envelope CSV not found: {path}")
    rows = []
    with path.open(newline="", encoding="utf-8-sig") as file:
        for source in csv.DictReader(file):
            if not str(source.get("used", "yes")).lower().startswith("yes"):
                continue
            angle = parse_number(source.get("angle_center_deg"))
            lateral = parse_number(source.get("lateral_g"))
            longitudinal = parse_number(source.get("longitudinal_g"))
            radius = parse_number(source.get("p98_combined_g") or source.get("p_combined_g"))
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


def nearest_usage(rows: list[dict], target_deg: float) -> float | None:
    if not rows:
        return None
    def angular_distance(row: dict) -> float:
        return abs((row["angle_deg"] - target_deg + 180.0) % 360.0 - 180.0)
    return min(rows, key=angular_distance)["usage_pct"]


def summarize(name: str, rows: list[dict]) -> dict:
    usages = [row["usage_pct"] for row in rows]
    return {
        "driver": name,
        "directions": len(rows),
        "mean_directional_usage_pct": sum(usages) / len(usages),
        "peak_directional_usage_pct": max(usages),
        "right_lateral_usage_pct": nearest_usage(rows, 180.0),
        "acceleration_usage_pct": nearest_usage(rows, 90.0),
        "left_lateral_usage_pct": nearest_usage(rows, 0.0),
        "braking_usage_pct": nearest_usage(rows, 270.0),
    }


def write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def format_summary_rows(rows: list[dict]) -> str:
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


def write_html(path: Path, plot_data: list[dict], summaries: list[dict], model: dict) -> None:
    model_json = json.dumps(model)
    plots_json = json.dumps(plot_data)
    table_rows = format_summary_rows(summaries)
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


def run(args: argparse.Namespace) -> None:
    if args.ellipse_exponent < 1.0:
        raise SystemExit("--ellipse-exponent must be at least 1.0")
    if not (0.0 < args.track_grip_scale <= 2.0):
        raise SystemExit("--track-grip-scale must be in (0, 2]")
    parameters = read_tir_parameters(args.tir)
    normal_load_n = args.normal_load_n or parameters["FNOMIN"]
    pressure_pa = (args.pressure_kpa * 1000.0) if args.pressure_kpa is not None else parameters["NOMPRES"]
    mu_x, mu_y = pure_slip_limits(
        parameters, normal_load_n, pressure_pa, args.camber_deg, args.track_grip_scale
    )

    source_rows = load_envelopes(args.driver_envelope)
    directional_rows = []
    grouped: dict[str, list[dict]] = defaultdict(list)
    for source in source_rows:
        theory_g = theoretical_radius(source["angle_deg"], mu_y, mu_x, args.ellipse_exponent)
        row = {**source, "theoretical_g": theory_g, "usage_pct": 100.0 * source["recorded_g"] / theory_g}
        directional_rows.append(row)
        grouped[row["driver"]].append(row)

    car_by_angle: dict[float, dict] = {}
    for row in directional_rows:
        angle = row["angle_deg"]
        if angle not in car_by_angle or row["recorded_g"] > car_by_angle[angle]["recorded_g"]:
            car_by_angle[angle] = {**row, "source_driver": row["driver"], "driver": "Recorded car (best driver per direction)"}
    car_rows = sorted(car_by_angle.values(), key=lambda row: row["angle_deg"])

    driver_summaries = [summarize(driver, sorted(rows, key=lambda row: row["angle_deg"])) for driver, rows in grouped.items()]
    driver_summaries.sort(key=lambda row: row["mean_directional_usage_pct"], reverse=True)
    car_summary = summarize("Recorded car (best driver per direction)", car_rows)
    summaries = [car_summary, *driver_summaries]

    args.out.mkdir(parents=True, exist_ok=True)
    directional_columns = [
        "driver", "run_id", "angle_deg", "lateral_g", "longitudinal_g", "recorded_g", "theoretical_g", "usage_pct"
    ]
    write_csv(args.out / "lc0_theoretical_directional_usage.csv", directional_rows, directional_columns)
    write_csv(
        args.out / "lc0_recorded_car_vs_theory.csv", car_rows,
        [*directional_columns, "source_driver"],
    )
    write_csv(
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
    write_html(args.out / "lc0_theoretical_gg.html", plot_data, summaries, model)

    print(f"LC0 pure-slip limits: lateral={mu_y:.4f} g, longitudinal={mu_x:.4f} g")
    print(f"Recorded car mean directional utilization: {car_summary['mean_directional_usage_pct']:.2f}%")
    print(f"Wrote {args.out / 'lc0_theoretical_gg.html'}")
    print(f"Wrote {args.out / 'lc0_theoretical_usage_by_driver.csv'}")
    print(f"Wrote {args.out / 'lc0_theoretical_directional_usage.csv'}")
    print(f"Wrote {args.out / 'lc0_recorded_car_vs_theory.csv'}")
    print(f"Wrote {args.out / 'lc0_theoretical_model.json'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("driver_envelope", type=Path, help="gg_envelope_by_driver CSV from vn300_gg_analysis.py")
    parser.add_argument("--tir", type=Path, required=True, help="fitted Hoosier 18x6-10 LC0 MF6.1 TIR file")
    parser.add_argument("--out", type=Path, default=Path("lc0_theoretical_gg_output"))
    parser.add_argument("--normal-load-n", type=float, help="per-tire normal load; default is TIR FNOMIN")
    parser.add_argument("--pressure-kpa", type=float, help="inflation pressure; default is TIR NOMPRES")
    parser.add_argument("--camber-deg", type=float, default=0.0)
    parser.add_argument("--track-grip-scale", type=float, default=1.0, help="multiply TTC peak friction by a track correlation factor")
    parser.add_argument("--ellipse-exponent", type=float, default=2.0, help="2.0 is a basic friction ellipse")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
