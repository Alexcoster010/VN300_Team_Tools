#!/usr/bin/env python3
"""Generic, offline CSV analysis workspace for VN300 Team Tools."""

from __future__ import annotations

import ast
import csv
import html
import json
import math
import operator
import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


MAX_SOURCE_FILES = 400
MAX_SOURCE_ROWS = 1_000_000
CHANNEL_REFERENCE = re.compile(r"\[([^\[\]]+)\]")
TIME_CHANNEL_CANDIDATES = (
    "segment_time_s",
    "Pi_Logger_Elapsed_Time_s",
    "Pi_Elapsed_Time_s",
    "time_s",
    "Time_s",
    "Sample_Index",
)
X_CHANNEL_CANDIDATES = (
    "segment_distance_m",
    "distance_m",
    "Distance_m",
    *TIME_CHANNEL_CANDIDATES,
)
STANDARD_DERIVED_UNITS = {
    "Vehicle_Speed_mps": "m/s",
    "Vehicle_Speed_mph": "mph",
    "Distance_m": "m",
    "Yaw_Rate_dps": "deg/s",
    "Longitudinal_G": "g",
    "Lateral_G": "g",
    "Vertical_G": "g",
}
G_MPS2 = 9.80665
EARTH_RADIUS_M = 6_371_000.0


class WorkspaceError(ValueError):
    """Raised when a custom workspace cannot be evaluated safely."""


@dataclass
class TableData:
    source: Path
    columns: dict[str, list[float | None]]
    units: dict[str, str]

    @property
    def row_count(self) -> int:
        return len(next(iter(self.columns.values()), []))


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def guess_unit(channel: str) -> str:
    """Return a display unit from common logger/channel naming conventions."""
    normalized = channel.lower().replace(" ", "_")
    suffixes = (
        ("_deg_per_s", "deg/s"),
        ("_dps", "deg/s"),
        ("_mps2", "m/s^2"),
        ("_mps", "m/s"),
        ("_mph", "mph"),
        ("_kmh", "km/h"),
        ("_psi", "psi"),
        ("_deg", "deg"),
        ("_mm", "mm"),
        ("_cm", "cm"),
        ("_hz", "Hz"),
        ("_rpm", "rpm"),
        ("_volts", "V"),
        ("_volt", "V"),
        ("_amps", "A"),
        ("_pct", "%"),
        ("_percent", "%"),
        ("_seconds", "s"),
        ("_time_s", "s"),
        ("_s", "s"),
        ("_g", "g"),
        ("_m", "m"),
    )
    for suffix, unit in suffixes:
        if normalized.endswith(suffix):
            return unit
    return ""


def discover_csv_sources(root: Path, limit: int = MAX_SOURCE_FILES) -> list[Path]:
    root = root.expanduser().resolve()
    if root.is_file():
        if root.suffix.lower() != ".csv":
            raise WorkspaceError("The selected file is not a CSV file.")
        return [root]
    if not root.is_dir():
        raise WorkspaceError("The selected telemetry folder does not exist.")
    paths = sorted(path for path in root.rglob("*.csv") if path.is_file())
    if len(paths) > limit:
        raise WorkspaceError(f"The folder contains more than {limit} CSV files. Select a smaller drive-day folder.")
    return paths


def _inspect_wide_csv(path: Path, sample_rows: int) -> tuple[list[dict[str, Any]], int]:
    with path.open(newline="", encoding="utf-8-sig", errors="replace") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        counts = {name: [0, 0] for name in fields}
        rows_seen = 0
        for row in reader:
            rows_seen += 1
            if rows_seen > sample_rows:
                break
            for name in fields:
                raw = row.get(name)
                if raw not in (None, ""):
                    counts[name][1] += 1
                    if _number(raw) is not None:
                        counts[name][0] += 1
    channels = []
    for name in fields:
        numeric, populated = counts[name]
        if numeric and numeric / max(populated, 1) >= 0.8:
            channels.append({"name": name, "unit": guess_unit(name), "numeric_ratio": numeric / max(populated, 1)})
    available = {channel["name"] for channel in channels}
    for name in standard_derived_channels(available):
        channels.append({"name": name, "unit": STANDARD_DERIVED_UNITS[name], "numeric_ratio": 1.0, "derived": True})
    return channels, rows_seen


def inspect_csv_source(path: Path, sample_rows: int = 500) -> dict[str, Any]:
    path = path.expanduser().resolve()
    try:
        with path.open(newline="", encoding="utf-8-sig", errors="replace") as handle:
            fields = list(csv.DictReader(handle).fieldnames or [])
    except OSError as exc:
        raise WorkspaceError(f"Could not read {path.name}: {exc}") from exc
    is_long = {"Channel", "Value"}.issubset(fields) and any(name in fields for name in TIME_CHANNEL_CANDIDATES)
    if not is_long:
        channels, sampled_rows = _inspect_wide_csv(path, sample_rows)
        return {
            "path": str(path),
            "name": path.name,
            "format": "wide",
            "channels": channels,
            "sampled_rows": sampled_rows,
        }

    time_name = next(name for name in TIME_CHANNEL_CANDIDATES if name in fields)
    discovered: dict[str, str] = {}
    sampled_rows = 0
    with path.open(newline="", encoding="utf-8-sig", errors="replace") as handle:
        for row in csv.DictReader(handle):
            sampled_rows += 1
            name = str(row.get("Channel") or "").strip()
            if name and _number(row.get("Value")) is not None:
                discovered.setdefault(name, str(row.get("Unit") or guess_unit(name)).strip())
            if sampled_rows >= sample_rows:
                break
    channels = [{"name": time_name, "unit": guess_unit(time_name), "numeric_ratio": 1.0}]
    channels.extend({"name": name, "unit": unit, "numeric_ratio": 1.0} for name, unit in discovered.items())
    return {
        "path": str(path),
        "name": path.name,
        "format": "long",
        "channels": channels,
        "sampled_rows": sampled_rows,
    }


def scan_workspace_sources(root: Path) -> dict[str, Any]:
    sources = []
    catalog: dict[str, dict[str, Any]] = {}
    warnings = []
    for path in discover_csv_sources(root):
        try:
            source = inspect_csv_source(path)
        except WorkspaceError as exc:
            warnings.append(str(exc))
            continue
        if len(source["channels"]) < 2:
            continue
        sources.append(source)
        for channel in source["channels"]:
            entry = catalog.setdefault(
                channel["name"],
                {"name": channel["name"], "unit": channel["unit"], "sources": 0},
            )
            entry["sources"] += 1
            if not entry["unit"] and channel["unit"]:
                entry["unit"] = channel["unit"]
    return {
        "root": str(root.expanduser().resolve()),
        "sources": sources,
        "channels": sorted(catalog.values(), key=lambda item: item["name"].lower()),
        "warnings": warnings,
    }


def standard_derived_channels(channels: set[str]) -> list[str]:
    derived = []
    velocity = {"Vel_N_mps", "Vel_E_mps"}.issubset(channels)
    time_available = any(name in channels for name in TIME_CHANNEL_CANDIDATES)
    if velocity:
        derived.extend(("Vehicle_Speed_mps", "Vehicle_Speed_mph"))
    if {"Latitude_deg", "Longitude_deg"}.issubset(channels):
        derived.append("Distance_m")
    if "Gyro_Z_rps" in channels or ({"Yaw_deg"}.issubset(channels) and time_available):
        derived.append("Yaw_Rate_dps")
    if "Accel_X_mps2" in channels or (velocity and time_available):
        derived.append("Longitudinal_G")
    if "Accel_Y_mps2" in channels or (velocity and ("Yaw_Rate_dps" in derived or "Gyro_Z_rps" in channels)):
        derived.append("Lateral_G")
    if "Accel_Z_mps2" in channels:
        derived.append("Vertical_G")
    return [name for name in derived if name not in channels]


def _vector_magnitude(*vectors: list[float | None]) -> list[float | None]:
    return [
        math.sqrt(sum(float(value) ** 2 for value in values)) if all(value is not None for value in values) else None
        for values in zip(*vectors)
    ]


def _gps_distance(latitude: list[float | None], longitude: list[float | None]) -> list[float | None]:
    if not latitude:
        return []
    output: list[float | None] = [0.0]
    total = 0.0
    for previous_lat, previous_lon, current_lat, current_lon in zip(
        latitude, longitude, latitude[1:], longitude[1:]
    ):
        if None in (previous_lat, previous_lon, current_lat, current_lon):
            output.append(None)
            continue
        phi1 = math.radians(float(previous_lat))
        phi2 = math.radians(float(current_lat))
        dphi = phi2 - phi1
        dlambda = math.radians(float(current_lon) - float(previous_lon))
        value = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
        step = 2 * EARTH_RADIUS_M * math.atan2(math.sqrt(value), math.sqrt(max(0.0, 1 - value)))
        if math.isfinite(step) and step < 1000.0:
            total += step
        output.append(total)
    return output


def add_standard_derived_channels(table: TableData) -> TableData:
    columns = table.columns
    units = table.units
    size = table.row_count
    time_name = next((name for name in TIME_CHANNEL_CANDIDATES if name in columns), "")
    time_values = columns.get(time_name, [float(index) for index in range(size)])

    if {"Vel_N_mps", "Vel_E_mps"}.issubset(columns):
        velocity_parts = [columns["Vel_N_mps"], columns["Vel_E_mps"]]
        if "Vel_D_mps" in columns:
            velocity_parts.append(columns["Vel_D_mps"])
        speed = _vector_magnitude(*velocity_parts)
        columns.setdefault("Vehicle_Speed_mps", speed)
        columns.setdefault(
            "Vehicle_Speed_mph",
            [value * 2.2369362921 if value is not None else None for value in speed],
        )
    if {"Latitude_deg", "Longitude_deg"}.issubset(columns):
        columns.setdefault("Distance_m", _gps_distance(columns["Latitude_deg"], columns["Longitude_deg"]))

    if "Gyro_Z_rps" in columns:
        yaw_rate_rps = list(columns["Gyro_Z_rps"])
    elif "Yaw_deg" in columns and time_name:
        yaw_rate_rps = [None] * size
        for index in range(1, size):
            yaw, previous_yaw = columns["Yaw_deg"][index], columns["Yaw_deg"][index - 1]
            timestamp, previous_timestamp = time_values[index], time_values[index - 1]
            if None in (yaw, previous_yaw, timestamp, previous_timestamp):
                continue
            dt = float(timestamp) - float(previous_timestamp)
            delta = (float(yaw) - float(previous_yaw) + 180.0) % 360.0 - 180.0
            if dt > 0:
                yaw_rate_rps[index] = math.radians(delta / dt)
    else:
        yaw_rate_rps = []
    if yaw_rate_rps:
        columns.setdefault(
            "Yaw_Rate_dps",
            [math.degrees(value) if value is not None else None for value in yaw_rate_rps],
        )

    if "Accel_X_mps2" in columns:
        longitudinal_g = [value / G_MPS2 if value is not None else None for value in columns["Accel_X_mps2"]]
    elif "Vehicle_Speed_mps" in columns and time_name:
        longitudinal_g = [
            value / G_MPS2 if value is not None else None
            for value in derivative(columns["Vehicle_Speed_mps"], time_values)
        ]
    else:
        longitudinal_g = []
    if longitudinal_g:
        columns.setdefault("Longitudinal_G", longitudinal_g)
    if "Accel_Y_mps2" in columns:
        lateral_g = [value / G_MPS2 if value is not None else None for value in columns["Accel_Y_mps2"]]
    elif "Vehicle_Speed_mps" in columns and yaw_rate_rps:
        lateral_g = [
            float(speed) * float(yaw_rate) / G_MPS2 if speed is not None and yaw_rate is not None else None
            for speed, yaw_rate in zip(columns["Vehicle_Speed_mps"], yaw_rate_rps)
        ]
    else:
        lateral_g = []
    if lateral_g:
        columns.setdefault("Lateral_G", lateral_g)
    if "Accel_Z_mps2" in columns:
        columns.setdefault(
            "Vertical_G",
            [value / G_MPS2 if value is not None else None for value in columns["Accel_Z_mps2"]],
        )
    for name in STANDARD_DERIVED_UNITS:
        if name in columns:
            units[name] = STANDARD_DERIVED_UNITS[name]
    return table


def _load_wide_csv(path: Path) -> TableData:
    with path.open(newline="", encoding="utf-8-sig", errors="replace") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        columns: dict[str, list[float | None]] = {name: [] for name in fields}
        columns["Sample_Index"] = []
        for index, row in enumerate(reader):
            if index >= MAX_SOURCE_ROWS:
                raise WorkspaceError(f"{path.name} exceeds the {MAX_SOURCE_ROWS:,}-row workspace limit.")
            columns["Sample_Index"].append(float(index))
            for name in fields:
                columns[name].append(_number(row.get(name)))
    numeric = {name: values for name, values in columns.items() if any(value is not None for value in values)}
    return add_standard_derived_channels(TableData(path, numeric, {name: guess_unit(name) for name in numeric}))


def _load_long_csv(path: Path) -> TableData:
    with path.open(newline="", encoding="utf-8-sig", errors="replace") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        time_name = next((name for name in TIME_CHANNEL_CANDIDATES if name in fields), None)
        if not time_name:
            raise WorkspaceError(f"{path.name} has long-form channels but no supported time column.")
        records = []
        names: dict[str, str] = {}
        for index, row in enumerate(reader):
            if index >= MAX_SOURCE_ROWS:
                raise WorkspaceError(f"{path.name} exceeds the {MAX_SOURCE_ROWS:,}-row workspace limit.")
            channel = str(row.get("Channel") or "").strip()
            value = _number(row.get("Value"))
            timestamp = _number(row.get(time_name))
            if not channel or value is None or timestamp is None:
                continue
            records.append((timestamp, channel, value))
            names.setdefault(channel, str(row.get("Unit") or guess_unit(channel)).strip())
    columns: dict[str, list[float | None]] = {
        time_name: [],
        "Sample_Index": [],
        **{name: [] for name in names},
    }
    for index, (timestamp, channel, value) in enumerate(records):
        columns[time_name].append(timestamp)
        columns["Sample_Index"].append(float(index))
        for name in names:
            columns[name].append(value if name == channel else None)
    units = {time_name: guess_unit(time_name), "Sample_Index": ""}
    units.update(names)
    return TableData(path, columns, units)


def load_csv_source(path: Path) -> TableData:
    source = inspect_csv_source(path, sample_rows=50)
    return _load_long_csv(path) if source["format"] == "long" else _load_wide_csv(path)


def _broadcast(value: Any, size: int) -> list[Any]:
    if isinstance(value, list):
        if len(value) != size:
            raise WorkspaceError("Calculated channel lengths do not match.")
        return value
    return [value] * size


def _pointwise(function: Callable[..., Any], size: int, *values: Any) -> list[Any]:
    vectors = [_broadcast(value, size) for value in values]
    output = []
    for items in zip(*vectors):
        if any(item is None for item in items):
            output.append(None)
            continue
        try:
            result = function(*items)
            output.append(result if not isinstance(result, float) or math.isfinite(result) else None)
        except (ArithmeticError, TypeError, ValueError):
            output.append(None)
    return output


def moving_average(values: list[Any], points: int) -> list[float | None]:
    points = max(1, int(points))
    if points == 1:
        return list(values)
    output: list[float | None] = []
    window: list[float] = []
    for value in values:
        if value is not None:
            window.append(float(value))
        if len(window) > points:
            window.pop(0)
        output.append(sum(window) / len(window) if window else None)
    return output


def hold_last(values: list[Any]) -> list[Any]:
    output = []
    last = None
    for value in values:
        if value is not None:
            last = value
        output.append(last)
    return output


def derivative(values: list[Any], x_values: list[Any]) -> list[float | None]:
    output: list[float | None] = [None] * len(values)
    for index in range(1, len(values)):
        value, previous = values[index], values[index - 1]
        x_value, previous_x = x_values[index], x_values[index - 1]
        if None in (value, previous, x_value, previous_x):
            continue
        dx = float(x_value) - float(previous_x)
        if dx:
            output[index] = (float(value) - float(previous)) / dx
    return output


def integral(values: list[Any], x_values: list[Any]) -> list[float | None]:
    output: list[float | None] = [0.0] if values else []
    total = 0.0
    for index in range(1, len(values)):
        value, previous = values[index], values[index - 1]
        x_value, previous_x = x_values[index], x_values[index - 1]
        if None not in (value, previous, x_value, previous_x):
            total += (float(value) + float(previous)) * 0.5 * (float(x_value) - float(previous_x))
        output.append(total)
    return output


class FormulaEvaluator:
    """Evaluate a small vector math language without Python eval()."""

    binary_operators = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Pow: operator.pow,
        ast.Mod: operator.mod,
    }
    comparisons = {
        ast.Eq: operator.eq,
        ast.NotEq: operator.ne,
        ast.Lt: operator.lt,
        ast.LtE: operator.le,
        ast.Gt: operator.gt,
        ast.GtE: operator.ge,
    }
    scalar_functions = {
        "abs": abs,
        "sqrt": math.sqrt,
        "sin": math.sin,
        "cos": math.cos,
        "tan": math.tan,
        "asin": math.asin,
        "acos": math.acos,
        "atan": math.atan,
        "atan2": math.atan2,
        "hypot": math.hypot,
        "degrees": math.degrees,
        "radians": math.radians,
        "min": min,
        "max": max,
    }

    def __init__(self, columns: dict[str, list[Any]]):
        self.columns = columns
        self.size = len(next(iter(columns.values()), []))

    @staticmethod
    def normalize(expression: str) -> str:
        return CHANNEL_REFERENCE.sub(lambda match: f"channel({match.group(1)!r})", expression)

    def evaluate(self, expression: str) -> list[Any]:
        if not expression.strip():
            raise WorkspaceError("A calculated channel or filter expression is empty.")
        try:
            tree = ast.parse(self.normalize(expression), mode="eval")
        except SyntaxError as exc:
            raise WorkspaceError(f"Invalid expression: {exc.msg}.") from exc
        return _broadcast(self._node(tree.body), self.size)

    def _node(self, node: ast.AST) -> Any:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, bool, str)):
            return node.value
        if isinstance(node, ast.Name) and node.id in ("pi", "e"):
            return getattr(math, node.id)
        if isinstance(node, ast.BinOp) and type(node.op) in self.binary_operators:
            return _pointwise(self.binary_operators[type(node.op)], self.size, self._node(node.left), self._node(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd, ast.Not)):
            function = operator.neg if isinstance(node.op, ast.USub) else operator.pos
            if isinstance(node.op, ast.Not):
                function = operator.not_
            return _pointwise(function, self.size, self._node(node.operand))
        if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
            values = [self._node(value) for value in node.values]
            function = (lambda *items: all(items)) if isinstance(node.op, ast.And) else (lambda *items: any(items))
            return _pointwise(function, self.size, *values)
        if isinstance(node, ast.Compare) and len(node.ops) == len(node.comparators) == 1:
            function = self.comparisons.get(type(node.ops[0]))
            if function:
                return _pointwise(function, self.size, self._node(node.left), self._node(node.comparators[0]))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            return self._call(node.func.id, [self._node(argument) for argument in node.args])
        raise WorkspaceError(f"Unsupported expression element: {type(node).__name__}.")

    def _call(self, name: str, args: list[Any]) -> Any:
        if name == "channel":
            if len(args) != 1 or not isinstance(args[0], str):
                raise WorkspaceError("Channel references must use [Channel Name].")
            if args[0] not in self.columns:
                raise WorkspaceError(f"Channel '{args[0]}' is not available in this source.")
            return self.columns[args[0]]
        if name in self.scalar_functions:
            return _pointwise(self.scalar_functions[name], self.size, *args)
        if name in ("smooth", "rolling_mean") and len(args) == 2:
            return moving_average(_broadcast(args[0], self.size), int(args[1]))
        if name == "hold" and len(args) == 1:
            return hold_last(_broadcast(args[0], self.size))
        if name == "derivative" and len(args) == 2:
            return derivative(_broadcast(args[0], self.size), _broadcast(args[1], self.size))
        if name == "integral" and len(args) == 2:
            return integral(_broadcast(args[0], self.size), _broadcast(args[1], self.size))
        if name == "diff" and len(args) == 1:
            values = _broadcast(args[0], self.size)
            return [None] + _pointwise(operator.sub, max(0, self.size - 1), values[1:], values[:-1])
        if name == "clip" and len(args) == 3:
            return _pointwise(lambda value, low, high: min(max(value, low), high), self.size, *args)
        if name == "where" and len(args) == 3:
            return _pointwise(lambda condition, yes, no: yes if condition else no, self.size, *args)
        raise WorkspaceError(f"Function '{name}' is not supported or has the wrong number of arguments.")


def formula_channel_references(expression: str) -> list[str]:
    return [match.group(1).strip() for match in CHANNEL_REFERENCE.finditer(expression)]


def apply_formulas(table: TableData, formulas: list[dict[str, Any]]) -> TableData:
    columns = {name: list(values) for name, values in table.columns.items()}
    units = dict(table.units)
    for formula in formulas:
        name = str(formula.get("name") or "").strip()
        expression = str(formula.get("expression") or "").strip()
        if not name:
            raise WorkspaceError("Every calculated channel needs a name.")
        if name in columns:
            raise WorkspaceError(f"Calculated channel '{name}' conflicts with an existing channel.")
        missing = [reference for reference in formula_channel_references(expression) if reference not in columns]
        columns[name] = [None] * table.row_count if missing else FormulaEvaluator(columns).evaluate(expression)
        units[name] = str(formula.get("unit") or "").strip()
    return TableData(table.source, columns, units)


def choose_default_x(channels: list[str]) -> str:
    return next((name for name in X_CHANNEL_CANDIDATES if name in channels), channels[0] if channels else "")


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def series_statistics(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {key: None for key in ("count", "min", "max", "mean", "std_dev", "p05", "median", "p95")}
    return {
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "mean": statistics.fmean(values),
        "std_dev": statistics.pstdev(values) if len(values) > 1 else 0.0,
        "p05": _percentile(values, 0.05),
        "median": _percentile(values, 0.5),
        "p95": _percentile(values, 0.95),
    }


def decimate_xy(points: list[tuple[float, float]], maximum: int) -> list[tuple[float, float]]:
    if maximum <= 0 or len(points) <= maximum:
        return points
    bucket_size = max(1, math.ceil(len(points) / max(1, maximum // 2)))
    output: list[tuple[float, float]] = []
    for start in range(0, len(points), bucket_size):
        bucket = points[start : start + bucket_size]
        low = min(enumerate(bucket), key=lambda item: item[1][1])
        high = max(enumerate(bucket), key=lambda item: item[1][1])
        for _, point in sorted((low, high), key=lambda item: item[0]):
            if not output or point != output[-1]:
                output.append(point)
    return output[:maximum]


def _html_report(path: Path, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    title = html.escape(str(payload.get("title") or "Custom Analysis Workspace"))
    path.write_text(
        f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{title}</title><style>
:root{{--paper:#fff;--ink:#142229;--muted:#65767d;--line:#d6e0e3;--teal:#00877f;--plot:#101d22}}
*{{box-sizing:border-box}}body{{margin:0;background:#edf2f3;color:var(--ink);font:14px Segoe UI,Arial,sans-serif}}
header{{padding:18px 22px 14px;background:var(--paper);border-bottom:1px solid var(--line)}}h1{{font-size:22px;margin:0 0 5px}}header p{{margin:0;color:var(--muted)}}
main{{padding:16px 22px 24px}}.plot-shell{{background:var(--plot);border:1px solid #25373d;min-height:480px;position:relative}}
canvas{{display:block;width:100%;height:480px}}#tooltip{{position:absolute;display:none;pointer-events:none;background:#fff;color:#142229;border:1px solid #b9c7cc;padding:6px 8px;font-size:12px;white-space:nowrap}}
.legend{{display:flex;flex-wrap:wrap;gap:8px 18px;padding:10px 12px;background:#17262c;color:#d9e5e8;border-top:1px solid #2c4148}}
.legend button{{border:0;background:transparent;color:inherit;padding:2px;cursor:pointer;font:12px Segoe UI,Arial,sans-serif}}.legend i{{display:inline-block;width:13px;height:3px;margin-right:6px;vertical-align:middle}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px;margin-top:14px}}section{{background:#fff;border:1px solid var(--line);padding:13px 15px}}
h2{{font-size:13px;margin:0 0 9px;color:#466069}}table{{border-collapse:collapse;width:100%;font-size:12px}}th,td{{border-bottom:1px solid #e3e9eb;padding:6px;text-align:right}}th:first-child,td:first-child{{text-align:left}}.muted{{color:var(--muted)}}
</style></head><body><header><h1>{title}</h1><p id="summary"></p></header><main>
<div class="plot-shell"><canvas id="plot"></canvas><div id="tooltip"></div><div class="legend" id="legend"></div></div>
<div class="cards"><section><h2>CHANNEL STATISTICS</h2><table id="stats"></table></section><section><h2>WORKSPACE CONFIGURATION</h2><table id="config"></table></section></div>
</main><script>
const D={data}; const canvas=document.getElementById('plot'), ctx=canvas.getContext('2d'), tip=document.getElementById('tooltip');
const colors=['#25b8ae','#f0b44d','#ef6f61','#74a8e8','#d68de3','#a7ca62','#f28ac0','#a7b8bf'];
let hidden=new Set(), base=null, view=null, drag=null;
function bounds(){{let xs=[],ys=[]; D.traces.forEach((t,i)=>{{if(hidden.has(i))return;t.points.forEach(p=>{{xs.push(p[0]);ys.push(p[1])}})}}); if(!xs.length)return {{xmin:0,xmax:1,ymin:0,ymax:1}};let xmin=Math.min(...xs),xmax=Math.max(...xs),ymin=Math.min(...ys),ymax=Math.max(...ys);if(xmin===xmax)xmax=xmin+1;if(ymin===ymax)ymax=ymin+1;let yp=(ymax-ymin)*.07;return {{xmin,xmax,ymin:ymin-yp,ymax:ymax+yp}}}}
function reset(){{base=bounds();view={{...base}};draw()}}
function resize(){{let r=canvas.getBoundingClientRect(),d=devicePixelRatio||1;canvas.width=Math.round(r.width*d);canvas.height=Math.round(r.height*d);ctx.setTransform(d,0,0,d,0,0);draw()}}
const box=()=>{{let r=canvas.getBoundingClientRect();return {{w:r.width,h:r.height,l:64,r:18,t:20,b:42}}}};
function sx(x,b){{return b.l+(x-view.xmin)/(view.xmax-view.xmin)*(b.w-b.l-b.r)}} function sy(y,b){{return b.h-b.b-(y-view.ymin)/(view.ymax-view.ymin)*(b.h-b.t-b.b)}}
function draw(){{if(!view)return;let b=box();ctx.clearRect(0,0,b.w,b.h);ctx.fillStyle='#101d22';ctx.fillRect(0,0,b.w,b.h);ctx.font='11px Segoe UI';ctx.strokeStyle='#294047';ctx.fillStyle='#8fa7af';ctx.lineWidth=1;for(let i=0;i<=5;i++){{let x=b.l+i*(b.w-b.l-b.r)/5,y=b.t+i*(b.h-b.t-b.b)/5;ctx.beginPath();ctx.moveTo(x,b.t);ctx.lineTo(x,b.h-b.b);ctx.stroke();ctx.fillText((view.xmin+i*(view.xmax-view.xmin)/5).toPrecision(5),x-18,b.h-16);ctx.beginPath();ctx.moveTo(b.l,y);ctx.lineTo(b.w-b.r,y);ctx.stroke();ctx.fillText((view.ymax-i*(view.ymax-view.ymin)/5).toPrecision(4),5,y+4)}}ctx.fillStyle='#b8cbd1';ctx.fillText(D.x_channel+(D.x_unit?' ('+D.x_unit+')':''),b.l,b.h-3);D.traces.forEach((t,i)=>{{if(hidden.has(i))return;ctx.strokeStyle=colors[i%colors.length];ctx.fillStyle=ctx.strokeStyle;ctx.lineWidth=1.5;ctx.beginPath();let started=false;t.points.forEach(p=>{{if(p[0]<view.xmin||p[0]>view.xmax)return;let x=sx(p[0],b),y=sy(p[1],b);if(D.plot_style==='scatter'){{ctx.fillRect(x-1.5,y-1.5,3,3)}}else{{started?ctx.lineTo(x,y):ctx.moveTo(x,y);started=true}}}});if(D.plot_style!=='scatter')ctx.stroke()}})}}
function esc(v){{let el=document.createElement('div');el.textContent=String(v);return el.innerHTML}}
function legend(){{let el=document.getElementById('legend');el.innerHTML='';D.traces.forEach((t,i)=>{{let b=document.createElement('button');b.innerHTML=`<i style="background:${{colors[i%colors.length]}};opacity:${{hidden.has(i)?.25:1}}"></i>${{esc(t.label)}}`;b.onclick=()=>{{hidden.has(i)?hidden.delete(i):hidden.add(i);legend();reset()}};el.appendChild(b)}})}}
canvas.onwheel=e=>{{e.preventDefault();let b=box(),f=e.deltaY>0?1.18:.84,mx=view.xmin+(e.offsetX-b.l)/(b.w-b.l-b.r)*(view.xmax-view.xmin);view.xmin=mx-(mx-view.xmin)*f;view.xmax=mx+(view.xmax-mx)*f;draw()}};
canvas.onmousedown=e=>drag={{x:e.clientX,min:view.xmin,max:view.xmax}};window.onmouseup=()=>drag=null;window.onmousemove=e=>{{if(!drag)return;let b=box(),dx=(e.clientX-drag.x)/(b.w-b.l-b.r)*(drag.max-drag.min);view.xmin=drag.min-dx;view.xmax=drag.max-dx;draw()}};canvas.ondblclick=reset;
canvas.onmousemove=e=>{{if(drag)return;let b=box(),target=view.xmin+(e.offsetX-b.l)/(b.w-b.l-b.r)*(view.xmax-view.xmin),best=null;D.traces.forEach((t,i)=>{{if(hidden.has(i))return;t.points.forEach(p=>{{let d=Math.abs(p[0]-target);if(!best||d<best.d)best={{d,p,t,i}}}})}});if(!best){{tip.style.display='none';return}}tip.style.display='block';tip.style.left=Math.min(e.offsetX+12,b.w-220)+'px';tip.style.top=Math.max(8,e.offsetY-30)+'px';tip.textContent=best.t.label+': '+best.p[1].toPrecision(6)+' '+best.t.unit+' at '+best.p[0].toPrecision(6)}};canvas.onmouseleave=()=>tip.style.display='none';
function fmt(v){{return v==null?'--':typeof v==='number'?Number(v).toPrecision(6):v}}document.getElementById('summary').textContent=D.source_count+' source file(s), '+D.filtered_points.toLocaleString()+' plotted samples';
document.getElementById('stats').innerHTML='<tr><th>Trace</th><th>Mean</th><th>Min</th><th>Max</th><th>P95</th></tr>'+D.traces.map(t=>`<tr><td>${{esc(t.label)}}</td><td>${{fmt(t.stats.mean)}}</td><td>${{fmt(t.stats.min)}}</td><td>${{fmt(t.stats.max)}}</td><td>${{fmt(t.stats.p95)}}</td></tr>`).join('');
let c=[['X channel',D.x_channel],['Y channels',D.y_channels.join(', ')],['Filter',D.filter||'None'],['Smoothing',D.smoothing_points+' points'],['Plot style',D.plot_style]];document.getElementById('config').innerHTML=c.map(r=>`<tr><td>${{esc(r[0])}}</td><td>${{esc(r[1])}}</td></tr>`).join('');
new ResizeObserver(resize).observe(canvas);legend();reset();resize();
</script></body></html>""",
        encoding="utf-8",
    )


def run_custom_analysis(config: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    paths = [Path(str(value)).expanduser().resolve() for value in config.get("source_paths", [])]
    if not paths:
        raise WorkspaceError("Select at least one telemetry CSV file.")
    if any(not path.is_file() or path.suffix.lower() != ".csv" for path in paths):
        raise WorkspaceError("One or more selected telemetry files are unavailable.")
    x_channel = str(config.get("x_channel") or "").strip()
    y_channels = [str(value).strip() for value in config.get("y_channels", []) if str(value).strip()]
    if not x_channel:
        raise WorkspaceError("Select an X-axis channel.")
    if not y_channels:
        raise WorkspaceError("Select at least one Y-axis channel.")
    formulas = [dict(value) for value in config.get("formulas", []) if isinstance(value, dict)]
    formula_names = [str(value.get("name") or "").strip() for value in formulas]
    if len(formula_names) != len(set(formula_names)):
        raise WorkspaceError("Calculated channel names must be unique.")
    filter_expression = str(config.get("filter") or "").strip()
    smoothing_points = max(1, min(1000, int(config.get("smoothing_points", 1))))
    max_plot_points = max(200, min(100_000, int(config.get("max_plot_points", 5000))))
    plot_style = str(config.get("plot_style") or "line").lower()
    if plot_style not in ("line", "scatter"):
        raise WorkspaceError("Plot style must be line or scatter.")

    traces = []
    export_rows = []
    warnings = []
    x_unit = ""
    for path in paths:
        table = apply_formulas(load_csv_source(path), formulas)
        if x_channel not in table.columns:
            warnings.append(f"{path.name}: missing X channel '{x_channel}'")
            continue
        x_unit = x_unit or table.units.get(x_channel, "")
        allowed = [True] * table.row_count
        if filter_expression:
            missing_filter_channels = [
                reference for reference in formula_channel_references(filter_expression) if reference not in table.columns
            ]
            if missing_filter_channels:
                warnings.append(
                    f"{path.name}: filter channels unavailable ({', '.join(missing_filter_channels)})"
                )
                continue
            try:
                mask = FormulaEvaluator(table.columns).evaluate(filter_expression)
                allowed = [bool(value) if value is not None else False for value in mask]
            except WorkspaceError as exc:
                raise WorkspaceError(f"Filter failed for {path.name}: {exc}") from exc
        for y_channel in y_channels:
            if y_channel not in table.columns:
                warnings.append(f"{path.name}: missing Y channel '{y_channel}'")
                continue
            y_values = moving_average(table.columns[y_channel], smoothing_points)
            points = [
                (float(x), float(y))
                for x, y, keep in zip(table.columns[x_channel], y_values, allowed)
                if keep and x is not None and y is not None
            ]
            if not points:
                warnings.append(f"{path.name}: no valid points for '{y_channel}'")
                continue
            unit = table.units.get(y_channel, "")
            stats = series_statistics([point[1] for point in points])
            label = f"{path.stem} | {y_channel}"
            traces.append({
                "label": label,
                "source": path.name,
                "channel": y_channel,
                "unit": unit,
                "points": [[x, y] for x, y in decimate_xy(points, max_plot_points)],
                "stats": stats,
            })
            export_rows.extend((path.name, x_channel, x, y_channel, y, unit) for x, y in points)
    if not traces:
        detail = f" Details: {'; '.join(warnings[:5])}" if warnings else ""
        raise WorkspaceError(f"No valid plot data was found.{detail}")

    output_dir.mkdir(parents=True, exist_ok=True)
    export_path = output_dir / "custom_workspace_data.csv"
    with export_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("source", "x_channel", "x", "y_channel", "y", "unit"))
        writer.writerows(export_rows)
    saved_config = {
        "source_paths": [str(path) for path in paths],
        "x_channel": x_channel,
        "y_channels": y_channels,
        "formulas": formulas,
        "filter": filter_expression,
        "smoothing_points": smoothing_points,
        "max_plot_points": max_plot_points,
        "plot_style": plot_style,
    }
    (output_dir / "custom_workspace_config.json").write_text(json.dumps(saved_config, indent=2), encoding="utf-8")
    payload = {
        "title": str(config.get("title") or "Custom Analysis Workspace").strip(),
        "source_count": len(paths),
        "filtered_points": len(export_rows),
        "x_channel": x_channel,
        "x_unit": x_unit,
        "y_channels": y_channels,
        "filter": filter_expression,
        "smoothing_points": smoothing_points,
        "plot_style": plot_style,
        "traces": traces,
        "warnings": warnings,
    }
    report_path = output_dir / "custom_workspace.html"
    _html_report(report_path, payload)
    return {
        "output_dir": str(output_dir.resolve()),
        "report_path": str(report_path.resolve()),
        "data_path": str(export_path.resolve()),
        "config_path": str((output_dir / "custom_workspace_config.json").resolve()),
        "trace_count": len(traces),
        "point_count": len(export_rows),
        "warnings": warnings,
        "traces": [{key: trace[key] for key in ("label", "source", "channel", "unit", "stats")} for trace in traces],
    }
