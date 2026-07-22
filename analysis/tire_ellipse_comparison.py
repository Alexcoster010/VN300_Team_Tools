#!/usr/bin/env python3
"""Compare LC0 and R20 tire-limited friction ellipses at matched conditions."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

from lc0_theoretical_gg import pure_slip_limits, read_tir_parameters, theoretical_radius


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


def write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def ellipse_points(lateral_g: float, longitudinal_g: float, exponent: float) -> list[dict]:
    rows = []
    for angle in range(361):
        radius = theoretical_radius(angle, lateral_g, longitudinal_g, exponent)
        radians = math.radians(angle)
        rows.append({
            "angle_deg": angle,
            "lateral_g": radius * math.cos(radians),
            "longitudinal_g": radius * math.sin(radians),
            "combined_g": radius,
        })
    return rows


def fmt_delta(value: float) -> str:
    return f"{value:+.1f}%"


def write_html(path: Path, model: dict, load_rows: list[dict], plot_data: list[dict]) -> None:
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
<div class="metrics"><div class="metric">R20 lateral change<strong>{fmt_delta(lat_delta)}</strong></div><div class="metric">R20 longitudinal change<strong>{fmt_delta(long_delta)}</strong></div><div class="metric">R20 ellipse-area change<strong>{fmt_delta(area_delta)}</strong></div><div class="metric">R20 tire mass change<strong>+{weight_delta:.2f} lb</strong></div><div class="metric">Baseline<strong>{model['normal_load_n']:.0f} N / {model['pressure_kpa']:.1f} kPa</strong></div></div>
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


def run(args: argparse.Namespace) -> None:
    if args.normal_load_n <= 0 or args.pressure_kpa <= 0:
        raise SystemExit("Load and pressure must be positive.")
    if args.ellipse_exponent < 1.0:
        raise SystemExit("--ellipse-exponent must be at least 1.0")
    parameters = {"LC0": read_tir_parameters(args.lc0_tir), "R20": read_tir_parameters(args.r20_tir)}
    paths = {"LC0": args.lc0_tir, "R20": args.r20_tir}
    loads = sorted(set(args.load_sweep_n + [args.normal_load_n]))
    baseline: dict[str, dict] = {}
    load_rows = []
    for load in loads:
        limits = {}
        for name in TIRES:
            mu_x, mu_y = pure_slip_limits(
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
        rows = ellipse_points(
            baseline[name]["lateral_limit_g"], baseline[name]["longitudinal_limit_g"], args.ellipse_exponent
        )
        ellipse_rows.extend({"tire": name, **row} for row in rows)
        plot_data.append({
            "name": name,
            "color": TIRES[name]["color"],
            "points": [[row["lateral_g"], row["longitudinal_g"]] for row in rows],
        })

    args.out.mkdir(parents=True, exist_ok=True)
    write_csv(
        args.out / "lc0_vs_r20_load_sweep.csv", load_rows,
        ["normal_load_n", "LC0_lateral_g", "R20_lateral_g", "R20_vs_LC0_lateral_pct", "LC0_longitudinal_g", "R20_longitudinal_g", "R20_vs_LC0_longitudinal_pct"],
    )
    write_csv(
        args.out / "lc0_vs_r20_ellipse_points.csv", ellipse_rows,
        ["tire", "angle_deg", "lateral_g", "longitudinal_g", "combined_g"],
    )
    (args.out / "lc0_vs_r20_model.json").write_text(json.dumps(model, indent=2), encoding="utf-8")
    write_html(args.out / "lc0_vs_r20_friction_ellipse.html", model, load_rows, plot_data)

    lc0, r20 = baseline["LC0"], baseline["R20"]
    print(f"LC0: lateral={lc0['lateral_limit_g']:.4f} g, longitudinal={lc0['longitudinal_limit_g']:.4f} g")
    print(f"R20: lateral={r20['lateral_limit_g']:.4f} g, longitudinal={r20['longitudinal_limit_g']:.4f} g")
    print(f"R20 lateral delta: {100 * (r20['lateral_limit_g'] / lc0['lateral_limit_g'] - 1):+.2f}%")
    print(f"Wrote {args.out / 'lc0_vs_r20_friction_ellipse.html'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lc0-tir", type=Path, required=True)
    parser.add_argument("--r20-tir", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("tire_ellipse_comparison_output"))
    parser.add_argument("--normal-load-n", type=float, default=750.0, help="matched per-tire comparison load")
    parser.add_argument("--pressure-kpa", type=float, default=82.737, help="matched inflation pressure; 82.737 kPa = 12 psi")
    parser.add_argument("--camber-deg", type=float, default=0.0)
    parser.add_argument("--grip-scale", type=float, default=1.0, help="common surface multiplier; does not affect relative result")
    parser.add_argument("--ellipse-exponent", type=float, default=2.0)
    parser.add_argument("--load-sweep-n", type=float, nargs="+", default=[300, 500, 750, 1000, 1250, 1500])
    run(parser.parse_args())


if __name__ == "__main__":
    main()
