#!/usr/bin/env python3
"""PySide6 desktop interface for VN300 trackside telemetry and analysis."""

from __future__ import annotations

import csv
import json
import math
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any, Callable

SOURCE_ANALYSIS_DIR = Path(__file__).resolve().parents[1] / "analysis"
if SOURCE_ANALYSIS_DIR.is_dir() and str(SOURCE_ANALYSIS_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_ANALYSIS_DIR))

from PySide6.QtCore import (
    QProcess,
    QRectF,
    QRunnable,
    QThreadPool,
    QTimer,
    QUrl,
    Qt,
    Signal,
    QObject,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QDesktopServices,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QTextCursor,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from vn300_desktop_app import (
    CURRENT_VERSION,
    DEFAULT_OUTPUT_ROOT,
    IS_FROZEN,
    PRODUCT_ICON,
    PRODUCT_LOGO,
    PRODUCT_NAME,
    REPO_ROOT,
    STATE_DIR,
    fetch_pi_snapshot,
    format_coordinate,
    format_lap_time,
    format_number,
    live_gps_position,
    load_state,
    normalize_pi_endpoint,
    pi_api_request,
    read_last_update_status,
    save_state,
)
from vn300_pi_updater import (
    fetch_latest_pi_logger_version,
    launch_pi_logger_update,
    logger_update_available,
    validate_ssh_username,
)
from vn300_team_app import build_analysis_command, result_files
from vn300_custom_analysis import choose_default_x, run_custom_analysis, scan_workspace_sources
from vn300_updater import (
    UPDATE_BRANCH,
    fetch_remote_version,
    has_git_checkout,
    launch_update_helper,
    stage_branch_archive,
    stage_release_installer,
    update_wait_pid,
    update_available,
)


RUN_METADATA_GROUPS = (
    (
        "Run details",
        (
            ("driver", "Driver"),
            ("test_location", "Test location"),
            ("test_type", "Test type"),
            ("course", "Course"),
        ),
    ),
    (
        "Tires and conditions",
        (
            ("tire_compound", "Tire compound"),
            ("cold_fl_psi", "Cold FL pressure (psi)"),
            ("cold_fr_psi", "Cold FR pressure (psi)"),
            ("cold_rl_psi", "Cold RL pressure (psi)"),
            ("cold_rr_psi", "Cold RR pressure (psi)"),
            ("hot_fl_psi", "Hot FL pressure (psi)"),
            ("hot_fr_psi", "Hot FR pressure (psi)"),
            ("hot_rl_psi", "Hot RL pressure (psi)"),
            ("hot_rr_psi", "Hot RR pressure (psi)"),
            ("ambient_temp_f", "Ambient temperature (F)"),
            ("track_temp_f", "Track temperature (F)"),
        ),
    ),
    (
        "Vehicle setup",
        (
            ("car_config", "Car configuration"),
            ("front_camber_deg", "Front camber (deg)"),
            ("rear_camber_deg", "Rear camber (deg)"),
            ("front_toe_deg", "Front toe (deg)"),
            ("rear_toe_deg", "Rear toe (deg)"),
            ("ride_height_front_mm", "Front ride height (mm)"),
            ("ride_height_rear_mm", "Rear ride height (mm)"),
            ("damper_front", "Front damper setting"),
            ("damper_rear", "Rear damper setting"),
            ("anti_roll_bar_front", "Front anti-roll bar"),
            ("anti_roll_bar_rear", "Rear anti-roll bar"),
            ("brake_bias", "Brake bias"),
            ("aero_config", "Aero configuration"),
            ("battery_or_fuel_state", "Battery or fuel state"),
        ),
    ),
)

EDITABLE_RUN_METADATA_FIELDS = tuple(
    key for _group, fields in RUN_METADATA_GROUPS for key, _label in fields
) + ("valid_run", "notes")

BUILTIN_WORKSPACE_PRESETS = {
    "Speed and acceleration": {
        "x": ("segment_distance_m", "distance_m", "Pi_Elapsed_Time_s", "time_s", "segment_time_s"),
        "y": (
            ("speed_mph", "Speed_mph", "Vehicle_Speed_mph"),
            ("lateral_g", "Lateral_G", "Common_Accel_Y_g"),
            ("longitudinal_g", "Longitudinal_G", "Common_Accel_X_g"),
        ),
        "style": "line",
    },
    "G-G diagram": {
        "x": ("lateral_g", "Lateral_G", "Common_Accel_Y_g"),
        "y": (("longitudinal_g", "Longitudinal_G", "Common_Accel_X_g"),),
        "style": "scatter",
    },
    "Driver inputs": {
        "x": ("segment_distance_m", "distance_m", "Pi_Logger_Elapsed_Time_s", "Pi_Elapsed_Time_s", "time_s"),
        "keywords": ("throttle", "brake_pressure", "steering"),
        "style": "line",
    },
    "Suspension": {
        "x": ("segment_distance_m", "distance_m", "Pi_Logger_Elapsed_Time_s", "Pi_Elapsed_Time_s", "time_s"),
        "keywords": ("damper", "suspension", "travel", "ride_height"),
        "style": "line",
    },
    "Tires": {
        "x": ("segment_distance_m", "distance_m", "Pi_Logger_Elapsed_Time_s", "Pi_Elapsed_Time_s", "time_s"),
        "keywords": ("tire_temp", "tyre_temp", "tire_pressure", "tpms"),
        "style": "line",
    },
}


APP_STYLE = """
* {
    font-family: "Segoe UI";
    font-size: 13px;
    color: #171717;
}
QMainWindow, QWidget#appRoot {
    background: #f2f2f2;
}
QMessageBox {
    background: #ffffff;
}
QMessageBox QLabel {
    min-width: 320px;
    background: transparent;
    color: #171717;
}
QMessageBox QPushButton {
    min-width: 80px;
}
QFrame#sidebar {
    background: #080808;
    border: 0;
}
QLabel#brandTitle {
    color: #ffffff;
    font-size: 13px;
    font-weight: 700;
}
QLabel#brandSub {
    color: #e31b23;
    font-size: 10px;
    font-weight: 700;
}
QLabel#railStatus {
    color: #a8a8a8;
    font-size: 10px;
    font-weight: 600;
}
QPushButton[nav="true"] {
    background: transparent;
    color: #d0d0d0;
    border: 0;
    border-left: 3px solid transparent;
    padding: 13px 16px;
    text-align: left;
    font-weight: 600;
}
QPushButton[nav="true"]:hover {
    background: #1b1b1b;
    color: #ffffff;
}
QPushButton[nav="true"]:checked {
    background: #281315;
    color: #ffffff;
    border-left-color: #d71920;
}
QFrame#topbar, QFrame#pageHeader, QFrame#footerBar {
    background: #ffffff;
    border: 0;
    border-bottom: 1px solid #d7d7d7;
}
QLabel#pageKicker {
    color: #c81018;
    font-size: 10px;
    font-weight: 700;
}
QLabel#pageTitle {
    color: #111111;
    font-size: 25px;
    font-weight: 700;
}
QLabel#clock {
    color: #5f5f5f;
    font-family: "Consolas";
    font-size: 12px;
}
QFrame[panel="true"], QFrame[metric="true"] {
    background: #ffffff;
    border: 1px solid #d2d2d2;
    border-radius: 4px;
}
QFrame[panel="dark"] {
    background: #0b0b0b;
    border: 1px solid #343434;
    border-radius: 4px;
}
QLabel[section="true"] {
    color: #595959;
    font-size: 10px;
    font-weight: 700;
}
QLabel[metricLabel="true"] {
    color: #666666;
    font-size: 10px;
    font-weight: 700;
}
QLabel[metricValue="true"] {
    color: #151515;
    font-size: 24px;
    font-weight: 700;
}
QLabel[metricUnit="true"] {
    color: #777777;
    font-size: 10px;
}
QLabel[status="online"] {
    background: #e5f4ed;
    color: #13704f;
    border: 1px solid #add7c5;
    border-radius: 3px;
    padding: 7px 12px;
    font-weight: 700;
}
QLabel[status="offline"] {
    background: #f8e9e7;
    color: #a43c35;
    border: 1px solid #e3bab5;
    border-radius: 3px;
    padding: 7px 12px;
    font-weight: 700;
}
QLabel[status="working"] {
    background: #fff2dd;
    color: #9a5c0c;
    border: 1px solid #e9c58a;
    border-radius: 3px;
    padding: 7px 12px;
    font-weight: 700;
}
QLabel[status="neutral"] {
    background: #f1f1f1;
    color: #5f5f5f;
    border: 1px solid #d0d0d0;
    border-radius: 3px;
    padding: 7px 12px;
    font-weight: 700;
}
QPushButton, QToolButton {
    min-height: 34px;
    background: #ffffff;
    color: #1c1c1c;
    border: 1px solid #bfbfbf;
    border-radius: 3px;
    padding: 0 13px;
    font-weight: 600;
}
QPushButton:hover, QToolButton:hover {
    background: #f7eeee;
    border-color: #b64a4e;
}
QPushButton[role="primary"] {
    background: #d71920;
    color: #ffffff;
    border-color: #d71920;
}
QPushButton[role="primary"]:hover {
    background: #ad1117;
}
QPushButton[role="danger"] {
    color: #ad3e37;
    border-color: #d5aaa5;
}
QPushButton:disabled, QToolButton:disabled {
    background: #eeeeee;
    color: #999999;
    border-color: #d8d8d8;
}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    min-height: 34px;
    background: #ffffff;
    border: 1px solid #bdbdbd;
    border-radius: 3px;
    padding: 0 9px;
    selection-background-color: #f3c8ca;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border: 1px solid #d71920;
}
QTabWidget::pane {
    border: 0;
    border-top: 1px solid #d0d0d0;
    background: #f2f2f2;
}
QTabBar::tab {
    min-width: 150px;
    min-height: 34px;
    padding: 0 14px;
    background: #e7e7e7;
    color: #595959;
    border: 1px solid #d0d0d0;
    border-bottom: 0;
    font-size: 10px;
    font-weight: 700;
}
QTabBar::tab:selected {
    background: #ffffff;
    color: #c81018;
    border-top: 2px solid #d71920;
}
QCheckBox {
    spacing: 8px;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
}
QTableWidget, QListWidget, QTextEdit {
    background: #ffffff;
    border: 1px solid #d2d2d2;
    border-radius: 3px;
    gridline-color: #e5e5e5;
    selection-background-color: #f5d9da;
    selection-color: #171717;
}
QTableWidget::item, QListWidget::item {
    padding: 7px;
}
QHeaderView::section {
    background: #e9e9e9;
    color: #4d4d4d;
    border: 0;
    border-right: 1px solid #d4d4d4;
    border-bottom: 1px solid #cccccc;
    padding: 7px;
    font-size: 10px;
    font-weight: 700;
}
QProgressBar {
    min-height: 4px;
    max-height: 4px;
    border: 0;
    background: #dedede;
}
QProgressBar::chunk {
    background: #d71920;
}
QSplitter::handle {
    background: #dddddd;
    width: 1px;
    height: 1px;
}
QScrollBar:vertical {
    background: #f2f2f2;
    width: 10px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #aaaaaa;
    min-height: 28px;
    border-radius: 4px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
"""


class TaskSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    finished = Signal(object)


class Worker(QRunnable):
    def __init__(self, function: Callable[..., Any], *args: Any):
        super().__init__()
        self.function = function
        self.args = args
        self.signals = TaskSignals()

    def run(self) -> None:
        try:
            result = self.function(*self.args)
        except Exception as exc:
            try:
                self.signals.error.emit(str(exc))
            except RuntimeError:
                return
        else:
            try:
                self.signals.result.emit(result)
            except RuntimeError:
                return
        try:
            self.signals.finished.emit(self)
        except RuntimeError:
            pass


class StatusPill(QLabel):
    def __init__(self, text: str = "OFFLINE", status: str = "offline"):
        super().__init__(text)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumWidth(110)
        self.set_status(text, status)

    def set_status(self, text: str, status: str) -> None:
        self.setText(text.upper())
        self.setProperty("status", status)
        self.style().unpolish(self)
        self.style().polish(self)


class MetricCard(QFrame):
    def __init__(self, label: str, unit: str = ""):
        super().__init__()
        self.setProperty("metric", True)
        self.setMinimumWidth(100)
        self.setFixedHeight(86)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 9)
        layout.setSpacing(2)
        name = QLabel(label.upper())
        name.setProperty("metricLabel", True)
        layout.addWidget(name)
        value_row = QHBoxLayout()
        value_row.setSpacing(5)
        value_row.setAlignment(Qt.AlignmentFlag.AlignBottom)
        self.value = QLabel("--")
        self.value.setProperty("metricValue", True)
        value_row.addWidget(self.value)
        self.unit = QLabel(unit)
        self.unit.setProperty("metricUnit", True)
        value_row.addWidget(self.unit)
        value_row.addStretch()
        layout.addLayout(value_row)

    def set_value(self, value: str, color: str = "") -> None:
        self.value.setText(value)
        self.value.setStyleSheet(f"color: {color};" if color else "")


class PlotWidget(QFrame):
    def __init__(self, title: str):
        super().__init__()
        self.title = title
        self.setProperty("panel", "dark")
        self.setMinimumHeight(190)

    def base_painter(self) -> tuple[QPainter, QRectF]:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#0b0b0b"))
        painter.setPen(QColor("#a0a0a0"))
        painter.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        painter.drawText(14, 21, self.title.upper())
        bounds = QRectF(18, 34, max(10, self.width() - 36), max(10, self.height() - 50))
        painter.setPen(QPen(QColor("#343434"), 1))
        for index in range(1, 4):
            y = bounds.top() + bounds.height() * index / 4
            painter.drawLine(bounds.left(), y, bounds.right(), y)
        return painter, bounds


class SpeedTraceWidget(PlotWidget):
    def __init__(self):
        super().__init__("Live speed trace")
        self.values: list[float] = []

    def set_values(self, values: list[float]) -> None:
        self.values = values[-300:]
        self.update()

    def paintEvent(self, _event: Any) -> None:
        painter, bounds = self.base_painter()
        if len(self.values) < 2:
            painter.setPen(QColor("#858585"))
            painter.drawText(bounds, Qt.AlignmentFlag.AlignCenter, "WAITING FOR SPEED DATA")
            painter.end()
            return
        maximum = max(50.0, max(self.values) * 1.15)
        path = QPainterPath()
        for index, value in enumerate(self.values):
            x = bounds.left() + bounds.width() * index / max(1, len(self.values) - 1)
            y = bounds.bottom() - bounds.height() * max(0.0, value) / maximum
            if index == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        painter.setPen(QPen(QColor("#f02b31"), 2))
        painter.drawPath(path)
        painter.setPen(QColor("#a0a0a0"))
        painter.drawText(bounds.adjusted(0, 2, -2, 0), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight, f"{maximum:.0f} mph")
        painter.end()


class TrackTraceWidget(PlotWidget):
    def __init__(self):
        super().__init__("Timing trace")
        self.current: list[tuple[float, float]] = []
        self.best: list[tuple[float, float]] = []

    @staticmethod
    def project(points: list[dict[str, Any]]) -> list[tuple[float, float]]:
        valid = [
            point
            for point in points
            if isinstance(point, dict)
            and isinstance(point.get("lat"), (int, float))
            and isinstance(point.get("lon"), (int, float))
            and math.isfinite(float(point["lat"]))
            and math.isfinite(float(point["lon"]))
        ]
        if not valid:
            return []
        origin_lat = float(valid[0]["lat"])
        origin_lon = float(valid[0]["lon"])
        scale = math.cos(math.radians(origin_lat))
        return [
            ((float(point["lon"]) - origin_lon) * scale * 111320.0, (float(point["lat"]) - origin_lat) * 110540.0)
            for point in valid
        ]

    def set_timing(self, timing: dict[str, Any]) -> None:
        self.current = self.project(timing.get("current_trace") or [])
        self.best = self.project(timing.get("best_trace") or [])
        self.update()

    def paintEvent(self, _event: Any) -> None:
        painter, bounds = self.base_painter()
        points = self.current + self.best
        if len(points) < 2:
            painter.setPen(QColor("#858585"))
            painter.drawText(bounds, Qt.AlignmentFlag.AlignCenter, "WAITING FOR GPS TRACE")
            painter.end()
            return
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        width = max(max_x - min_x, 1.0)
        height = max(max_y - min_y, 1.0)
        scale = min(bounds.width() / width, bounds.height() / height) * 0.9

        def draw_trace(trace: list[tuple[float, float]], color: str, line_width: float) -> None:
            if len(trace) < 2:
                return
            path = QPainterPath()
            for index, (x_value, y_value) in enumerate(trace):
                x = bounds.center().x() + (x_value - (min_x + max_x) / 2) * scale
                y = bounds.center().y() - (y_value - (min_y + max_y) / 2) * scale
                if index == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            painter.setPen(QPen(QColor(color), line_width))
            painter.drawPath(path)

        draw_trace(self.best, "#d0d0d0", 2)
        draw_trace(self.current, "#f02b31", 3)
        painter.end()


class PageHeader(QFrame):
    def __init__(self, kicker: str, title: str):
        super().__init__()
        self.setObjectName("pageHeader")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 15)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        kicker_label = QLabel(kicker.upper())
        kicker_label.setObjectName("pageKicker")
        title_label = QLabel(title)
        title_label.setObjectName("pageTitle")
        titles.addWidget(kicker_label)
        titles.addWidget(title_label)
        layout.addLayout(titles)
        layout.addStretch()
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        layout.addLayout(self.actions)


class VN300QtApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{PRODUCT_NAME} v{CURRENT_VERSION}")
        icon = REPO_ROOT / PRODUCT_ICON
        if not icon.is_file():
            icon = REPO_ROOT / "packaging" / "assets" / PRODUCT_ICON
        if icon.is_file():
            self.setWindowIcon(QIcon(str(icon)))
        self.setMinimumSize(1100, 700)
        self.resize(1440, 900)

        self.state_data = load_state()
        self.thread_pool = QThreadPool.globalInstance()
        self.active_workers: set[Worker] = set()
        self.analysis_process: QProcess | None = None
        self.analysis_job: dict[str, Any] | None = None
        self.analysis_cancelled = False
        self.custom_scan_result: dict[str, Any] = {}
        self.custom_task_active = False
        self.custom_save_requested = False
        self.pi_endpoint = str(self.state_data.get("pi_endpoint") or "")
        self.pi_generation = 0
        self.pi_request_active = False
        self.pi_connected = False
        self.pi_next_poll_at = 0.0
        self.pi_last_snapshot: dict[str, Any] = {}
        self.pi_latency_ms = 0.0
        self.pi_speed_history: list[float] = []
        self.run_metadata_inputs: dict[str, QWidget] = {}
        self.timing_gate_inputs: dict[str, QLineEdit] = {}
        self.timing_live_buttons: dict[str, QPushButton] = {}
        self.current_gps_position: tuple[float, float] | None = None
        self.run_metadata_dirty = False
        self.timing_setup_dirty = False
        self.last_run_metadata_snapshot: dict[str, Any] = {}
        self.last_timing_config_snapshot: dict[str, Any] = {}
        self.metadata_save_active = False
        self.timing_save_active = False
        self.timing_reset_active = False
        self.setup_stream_action_active = False
        self.installed_logger_version = "unknown"
        self.available_logger_version = ""
        self.logger_check_active = False
        self.logger_update_active = False
        self.logger_prompted_versions: set[str] = set()
        self.update_check_active = False
        self.available_app_version = ""
        self.selected_history: dict[str, Any] | None = None
        self.selected_result: dict[str, str] | None = None

        self.setStyleSheet(APP_STYLE)
        self.build_shell()
        self.restore_window_geometry()
        self.refresh_history()

        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(250)
        self.poll_timer.timeout.connect(self.maybe_poll_pi)
        self.poll_timer.start()

        self.clock_timer = QTimer(self)
        self.clock_timer.setInterval(1000)
        self.clock_timer.timeout.connect(self.update_clock)
        self.clock_timer.start()
        self.update_clock()

        QTimer.singleShot(250, self.initial_pi_connect)
        QTimer.singleShot(800, self.show_last_update_status)
        QTimer.singleShot(1400, partial(self.check_app_update, False))

    def build_shell(self) -> None:
        root = QWidget()
        root.setObjectName("appRoot")
        self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(205)
        rail = QVBoxLayout(sidebar)
        rail.setContentsMargins(0, 0, 0, 18)
        rail.setSpacing(0)

        brand = QWidget()
        brand_layout = QHBoxLayout(brand)
        brand_layout.setContentsMargins(16, 18, 12, 19)
        logo = QLabel()
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setFixedSize(52, 52)
        logo_path = REPO_ROOT / PRODUCT_LOGO
        if not logo_path.is_file():
            logo_path = REPO_ROOT / "packaging" / "assets" / PRODUCT_LOGO
        if logo_path.is_file():
            logo.setPixmap(
                QPixmap(str(logo_path)).scaled(
                    52,
                    52,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        brand_layout.addWidget(logo)
        brand_text = QVBoxLayout()
        title = QLabel("SOONER RACING")
        title.setObjectName("brandTitle")
        subtitle = QLabel("TELEMETRY")
        subtitle.setObjectName("brandSub")
        brand_text.addWidget(title)
        brand_text.addWidget(subtitle)
        brand_layout.addLayout(brand_text)
        rail.addWidget(brand)

        self.nav_buttons: dict[str, QPushButton] = {}
        for key, label in (("dashboard", "LIVE DASHBOARD"), ("analysis", "DATA ANALYSIS"), ("results", "REPORTS")):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setProperty("nav", True)
            button.clicked.connect(partial(self.show_page, key))
            rail.addWidget(button)
            self.nav_buttons[key] = button
        rail.addStretch()
        self.rail_pi_status = QLabel("PI OFFLINE")
        self.rail_pi_status.setObjectName("railStatus")
        self.rail_pi_status.setContentsMargins(20, 8, 12, 4)
        rail.addWidget(self.rail_pi_status)
        version = QLabel(f"DESKTOP v{CURRENT_VERSION}")
        version.setObjectName("railStatus")
        version.setContentsMargins(20, 2, 12, 4)
        rail.addWidget(version)
        shell.addWidget(sidebar)

        workspace = QWidget()
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)
        topbar = QFrame()
        topbar.setObjectName("topbar")
        topbar_layout = QHBoxLayout(topbar)
        topbar_layout.setContentsMargins(22, 9, 20, 9)
        self.context_label = QLabel("LIVE OPERATIONS")
        self.context_label.setProperty("section", True)
        topbar_layout.addWidget(self.context_label)
        topbar_layout.addStretch()
        self.clock_label = QLabel()
        self.clock_label.setObjectName("clock")
        topbar_layout.addWidget(self.clock_label)
        self.app_update_button = QPushButton(f"v{CURRENT_VERSION}  CHECK UPDATES")
        self.app_update_button.clicked.connect(self.app_update_action)
        topbar_layout.addWidget(self.app_update_button)
        workspace_layout.addWidget(topbar)

        self.pages = QStackedWidget()
        self.pages.addWidget(self.build_dashboard_page())
        self.pages.addWidget(self.build_analysis_page())
        self.pages.addWidget(self.build_results_page())
        workspace_layout.addWidget(self.pages, 1)
        shell.addWidget(workspace, 1)
        self.show_page("dashboard")

    def build_dashboard_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        header = PageHeader("Trackside telemetry", "Live dashboard")
        self.dashboard_header_status = StatusPill()
        header.actions.addWidget(self.dashboard_header_status)
        layout.addWidget(header)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(22, 16, 22, 15)
        content_layout.setSpacing(12)

        connection = QFrame()
        connection.setProperty("panel", True)
        connection_layout = QHBoxLayout(connection)
        connection_layout.setContentsMargins(12, 10, 12, 10)
        connection_layout.setSpacing(8)
        address_label = QLabel("PI ADDRESS")
        address_label.setProperty("section", True)
        connection_layout.addWidget(address_label)
        self.pi_address = QLineEdit(self.pi_endpoint)
        self.pi_address.setPlaceholderText("raspberrypi.local")
        self.pi_address.returnPressed.connect(self.connect_pi)
        connection_layout.addWidget(self.pi_address, 1)
        connect = QPushButton("CONNECT")
        connect.setProperty("role", "primary")
        connect.clicked.connect(self.connect_pi)
        connection_layout.addWidget(connect)
        self.logger_button = QPushButton("CHECK LOGGER")
        self.logger_button.setEnabled(False)
        self.logger_button.clicked.connect(self.logger_action)
        connection_layout.addWidget(self.logger_button)
        ssh_button = QToolButton()
        ssh_button.setText("SSH ACCOUNT")
        ssh_button.setToolTip("Change the saved Raspberry Pi SSH username")
        ssh_button.clicked.connect(self.change_ssh_user)
        connection_layout.addWidget(ssh_button)
        self.connection_status = StatusPill()
        connection_layout.addWidget(self.connection_status)
        content_layout.addWidget(connection)

        self.dashboard_tabs = QTabWidget()
        gps_corner = QWidget()
        gps_corner_layout = QHBoxLayout(gps_corner)
        gps_corner_layout.setContentsMargins(8, 0, 10, 0)
        gps_corner_layout.setSpacing(14)
        self.dashboard_live_latitude = QLabel("LAT --")
        self.dashboard_live_longitude = QLabel("LON --")
        for coordinate_label, minimum_width in (
            (self.dashboard_live_latitude, 135),
            (self.dashboard_live_longitude, 145),
        ):
            coordinate_label.setMinimumWidth(minimum_width)
            coordinate_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            coordinate_label.setStyleSheet("font-family:Consolas;font-size:12px;font-weight:700;color:#333333;")
            coordinate_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            gps_corner_layout.addWidget(coordinate_label)
        self.dashboard_tabs.setCornerWidget(gps_corner, Qt.Corner.TopRightCorner)
        live_tab = QWidget()
        live_layout = QVBoxLayout(live_tab)
        live_layout.setContentsMargins(0, 12, 0, 0)
        live_layout.setSpacing(12)

        metrics = QGridLayout()
        metrics.setHorizontalSpacing(8)
        metrics.setVerticalSpacing(8)
        self.metric_cards: dict[str, MetricCard] = {}
        metric_specs = (
            ("speed", "Speed", "mph"),
            ("lat_g", "Lateral", "g"),
            ("long_g", "Long accel", "g"),
            ("yaw", "Yaw", "deg"),
            ("current", "Current lap", ""),
            ("best", "Best lap", ""),
            ("delta", "Live delta", "s"),
            ("laps", "Laps", ""),
        )
        for column, (key, label, unit) in enumerate(metric_specs):
            card = MetricCard(label, unit)
            metrics.addWidget(card, 0, column)
            metrics.setColumnStretch(column, 1)
            self.metric_cards[key] = card
        live_layout.addLayout(metrics)

        trace_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.track_plot = TrackTraceWidget()
        self.speed_plot = SpeedTraceWidget()
        trace_splitter.addWidget(self.track_plot)
        trace_splitter.addWidget(self.speed_plot)
        trace_splitter.setSizes([650, 470])
        live_layout.addWidget(trace_splitter, 1)

        lower = QSplitter(Qt.Orientation.Horizontal)
        laps_panel = QFrame()
        laps_panel.setProperty("panel", True)
        laps_layout = QVBoxLayout(laps_panel)
        laps_layout.setContentsMargins(12, 10, 12, 12)
        laps_title = QLabel("COMPLETED LAPS / RUNS")
        laps_title.setProperty("section", True)
        laps_layout.addWidget(laps_title)
        self.lap_table = QTableWidget(0, 5)
        self.lap_table.setHorizontalHeaderLabels(["#", "TYPE", "TIME", "DELTA", "STATUS"])
        self.lap_table.verticalHeader().setVisible(False)
        self.lap_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.lap_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.lap_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        for column in range(4):
            self.lap_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        laps_layout.addWidget(self.lap_table)
        lower.addWidget(laps_panel)

        health = QFrame()
        health.setProperty("panel", True)
        health_layout = QVBoxLayout(health)
        health_layout.setContentsMargins(14, 11, 14, 12)
        health_title = QLabel("SYSTEM HEALTH")
        health_title.setProperty("section", True)
        health_layout.addWidget(health_title)
        self.health_labels: dict[str, QLabel] = {}
        for key, label in (
            ("session", "Session"),
            ("log", "Log destination"),
            ("storage", "Free storage"),
            ("logger", "Logger"),
            ("cpu", "Pi CPU"),
            ("latency", "Network"),
        ):
            row = QHBoxLayout()
            name = QLabel(label)
            name.setStyleSheet("color:#666666;")
            value = QLabel("--")
            value.setAlignment(Qt.AlignmentFlag.AlignRight)
            value.setStyleSheet("font-weight:600;")
            row.addWidget(name)
            row.addStretch()
            row.addWidget(value)
            health_layout.addLayout(row)
            self.health_labels[key] = value
        health_layout.addStretch()
        lower.addWidget(health)
        lower.setSizes([780, 330])
        live_layout.addWidget(lower, 1)
        self.dashboard_tabs.addTab(live_tab, "LIVE TELEMETRY")
        self.dashboard_tabs.addTab(self.build_drive_day_setup_tab(), "DRIVE DAY SETUP")
        content_layout.addWidget(self.dashboard_tabs, 1)
        layout.addWidget(content, 1)
        return page

    def setup_panel(self, title: str) -> tuple[QFrame, QVBoxLayout]:
        panel = QFrame()
        panel.setProperty("panel", True)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(14, 12, 14, 14)
        panel_layout.setSpacing(10)
        heading = QLabel(title.upper())
        heading.setProperty("section", True)
        panel_layout.addWidget(heading)
        return panel, panel_layout

    def metadata_input(self, key: str) -> QWidget:
        field = QLineEdit()
        field.setPlaceholderText("Optional")
        field.textEdited.connect(self.mark_run_metadata_dirty)
        self.run_metadata_inputs[key] = field
        return field

    def metadata_form(self, fields: tuple[tuple[str, str], ...]) -> QFormLayout:
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(7)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        for key, label in fields:
            form.addRow(label, self.metadata_input(key))
        return form

    def build_drive_day_setup_tab(self) -> QWidget:
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(0, 12, 0, 0)
        tab_layout.setSpacing(0)

        scroll = QScrollArea()
        self.drive_day_scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        host = QWidget()
        self.drive_day_host = host
        host.setMinimumWidth(0)
        host.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 4)
        host_layout.setSpacing(12)

        columns = QHBoxLayout()
        columns.setSpacing(12)

        run_column = QVBoxLayout()
        run_column.setSpacing(12)
        run_panel, run_layout = self.setup_panel("Run details")
        identity_form = QFormLayout()
        identity_form.setContentsMargins(0, 0, 0, 0)
        identity_form.setHorizontalSpacing(10)
        identity_form.setVerticalSpacing(7)
        self.setup_date_label = QLabel("--")
        self.setup_date_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.next_run_label = QLabel("--")
        self.next_run_label.setStyleSheet("font-weight:700;color:#c81018;")
        self.next_run_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        identity_form.addRow("Date", self.setup_date_label)
        identity_form.addRow("Next run", self.next_run_label)
        run_layout.addLayout(identity_form)
        run_layout.addLayout(self.metadata_form(RUN_METADATA_GROUPS[0][1]))
        run_column.addWidget(run_panel)

        review_panel, review_layout = self.setup_panel("Run review")
        review_form = QFormLayout()
        review_form.setContentsMargins(0, 0, 0, 0)
        review_form.setHorizontalSpacing(10)
        review_form.setVerticalSpacing(7)
        self.valid_run_input = QComboBox()
        self.valid_run_input.addItems(["yes", "no", "review"])
        self.valid_run_input.currentTextChanged.connect(self.mark_run_metadata_dirty)
        self.run_metadata_inputs["valid_run"] = self.valid_run_input
        review_form.addRow("Valid run", self.valid_run_input)
        review_layout.addLayout(review_form)
        notes_label = QLabel("Notes")
        notes_label.setStyleSheet("color:#5d5d5d;")
        review_layout.addWidget(notes_label)
        self.run_notes_input = QTextEdit()
        self.run_notes_input.setPlaceholderText("Driver comments, setup observations, incidents, or test notes")
        self.run_notes_input.setMinimumHeight(105)
        self.run_notes_input.textChanged.connect(self.mark_run_metadata_dirty)
        self.run_metadata_inputs["notes"] = self.run_notes_input
        review_layout.addWidget(self.run_notes_input)
        self.metadata_save_status = QLabel("Connect to the Pi to load setup")
        self.metadata_save_status.setWordWrap(True)
        self.metadata_save_status.setStyleSheet("color:#6d6d6d;")
        review_layout.addWidget(self.metadata_save_status)
        self.metadata_save_button = QPushButton("SAVE RUN INFO")
        self.metadata_save_button.setProperty("role", "primary")
        self.metadata_save_button.setEnabled(False)
        self.metadata_save_button.clicked.connect(self.save_run_metadata)
        review_layout.addWidget(self.metadata_save_button)

        tires_panel, tires_layout = self.setup_panel("Tires and conditions")
        tires_layout.addLayout(self.metadata_form(RUN_METADATA_GROUPS[1][1]))
        tires_layout.addStretch()

        vehicle_panel, vehicle_layout = self.setup_panel("Vehicle setup")
        vehicle_layout.addLayout(self.metadata_form(RUN_METADATA_GROUPS[2][1]))
        vehicle_layout.addStretch()
        run_column.addWidget(vehicle_panel)
        run_column.addStretch()

        conditions_column = QVBoxLayout()
        conditions_column.setSpacing(12)
        conditions_column.addWidget(tires_panel)
        conditions_column.addWidget(review_panel)
        conditions_column.addStretch()
        columns.addLayout(run_column, 1)
        columns.addLayout(conditions_column, 1)
        host_layout.addLayout(columns)

        timing_panel, timing_layout = self.setup_panel("Timing setup")
        timing_head = QHBoxLayout()
        timing_head.setSpacing(10)
        mode_label = QLabel("Track mode")
        mode_label.setStyleSheet("color:#5d5d5d;")
        timing_head.addWidget(mode_label)
        self.timing_mode_input = QComboBox()
        self.timing_mode_input.addItem("Lap", "lap")
        self.timing_mode_input.addItem("Autocross", "autocross")
        self.timing_mode_input.currentIndexChanged.connect(self.timing_mode_changed)
        timing_head.addWidget(self.timing_mode_input)
        timing_head.addStretch()
        self.timing_config_status = QLabel("Not configured")
        self.timing_config_status.setStyleSheet("color:#6d6d6d;font-weight:600;")
        timing_head.addWidget(self.timing_config_status)
        timing_layout.addLayout(timing_head)

        live_position = QHBoxLayout()
        live_position.setSpacing(16)
        live_position_label = QLabel("LIVE GPS POSITION")
        live_position_label.setProperty("section", True)
        live_position.addWidget(live_position_label)
        self.timing_live_latitude = QLabel("LAT --")
        self.timing_live_longitude = QLabel("LON --")
        for coordinate_label in (self.timing_live_latitude, self.timing_live_longitude):
            coordinate_label.setStyleSheet("font-family:Consolas;font-size:15px;font-weight:700;")
            coordinate_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            live_position.addWidget(coordinate_label)
        live_position.addStretch()
        timing_layout.addLayout(live_position)

        gates = QGridLayout()
        gates.setHorizontalSpacing(10)
        gates.setVerticalSpacing(7)
        for column, title in enumerate(("GATE POINT", "LATITUDE", "LONGITUDE", "LIVE POSITION")):
            label = QLabel(title)
            label.setProperty("section", True)
            gates.addWidget(label, 0, column)
        gate_rows = (
            ("Start point 1", "start_lat1", "start_lon1"),
            ("Start point 2", "start_lat2", "start_lon2"),
            ("Finish point 1", "finish_lat1", "finish_lon1"),
            ("Finish point 2", "finish_lat2", "finish_lon2"),
        )
        for row, (label_text, latitude_key, longitude_key) in enumerate(gate_rows, start=1):
            gates.addWidget(QLabel(label_text), row, 0)
            for column, key in ((1, latitude_key), (2, longitude_key)):
                field = QLineEdit()
                field.setPlaceholderText("0.00000000")
                field.textEdited.connect(self.mark_timing_setup_dirty)
                self.timing_gate_inputs[key] = field
                gates.addWidget(field, row, column)
            use_live = QPushButton("USE LIVE")
            use_live.setEnabled(False)
            use_live.clicked.connect(partial(self.use_live_gps_for_gate, latitude_key, longitude_key))
            self.timing_live_buttons[latitude_key] = use_live
            gates.addWidget(use_live, row, 3)
        gates.setColumnStretch(1, 1)
        gates.setColumnStretch(2, 1)
        timing_layout.addLayout(gates)

        thresholds = QHBoxLayout()
        thresholds.setSpacing(10)
        thresholds.addWidget(QLabel("Minimum speed"))
        self.timing_min_speed_input = QDoubleSpinBox()
        self.timing_min_speed_input.setRange(0.0, 200.0)
        self.timing_min_speed_input.setDecimals(1)
        self.timing_min_speed_input.setSingleStep(0.5)
        self.timing_min_speed_input.setSuffix(" mph")
        self.timing_min_speed_input.setValue(5.0)
        self.timing_min_speed_input.valueChanged.connect(self.mark_timing_setup_dirty)
        thresholds.addWidget(self.timing_min_speed_input)
        thresholds.addWidget(QLabel("Minimum crossing gap"))
        self.timing_min_gap_input = QDoubleSpinBox()
        self.timing_min_gap_input.setRange(0.5, 600.0)
        self.timing_min_gap_input.setDecimals(1)
        self.timing_min_gap_input.setSingleStep(0.5)
        self.timing_min_gap_input.setSuffix(" s")
        self.timing_min_gap_input.setValue(8.0)
        self.timing_min_gap_input.valueChanged.connect(self.mark_timing_setup_dirty)
        thresholds.addWidget(self.timing_min_gap_input)
        thresholds.addStretch()
        timing_layout.addLayout(thresholds)

        timing_actions = QHBoxLayout()
        timing_actions.setSpacing(10)
        self.timing_save_status = QLabel("Connect to the Pi to load timing setup")
        self.timing_save_status.setStyleSheet("color:#6d6d6d;")
        timing_actions.addWidget(self.timing_save_status)
        timing_actions.addStretch()
        self.setup_stream_button = QPushButton("START SETUP STREAM")
        self.setup_stream_button.setToolTip(
            "Show live VN-300 data for timing setup without recording a run or creating log files."
        )
        self.setup_stream_button.setEnabled(False)
        self.setup_stream_button.clicked.connect(self.toggle_setup_stream)
        timing_actions.addWidget(self.setup_stream_button)
        self.timing_reset_button = QPushButton("RESET TIMING")
        self.timing_reset_button.setProperty("role", "danger")
        self.timing_reset_button.setEnabled(False)
        self.timing_reset_button.clicked.connect(self.reset_timing)
        timing_actions.addWidget(self.timing_reset_button)
        self.timing_save_button = QPushButton("SAVE TIMING")
        self.timing_save_button.setProperty("role", "primary")
        self.timing_save_button.setEnabled(False)
        self.timing_save_button.clicked.connect(self.save_timing_setup)
        timing_actions.addWidget(self.timing_save_button)
        timing_layout.addLayout(timing_actions)
        host_layout.addWidget(timing_panel)
        host_layout.addStretch()

        scroll.setWidget(host)
        tab_layout.addWidget(scroll)
        self.timing_mode_changed()
        return tab

    def build_analysis_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        header = PageHeader("Offline processing", "Data analysis")
        self.analysis_status = StatusPill("READY", "neutral")
        header.actions.addWidget(self.analysis_status)
        layout.addWidget(header)

        self.analysis_tabs = QTabWidget()
        quick_tab = QWidget()
        quick_layout = QVBoxLayout(quick_tab)
        quick_layout.setContentsMargins(0, 0, 0, 0)
        quick_layout.setSpacing(0)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setContentsMargins(22, 16, 22, 16)
        setup_scroll = QScrollArea()
        setup_scroll.setWidgetResizable(True)
        setup_scroll.setFrameShape(QFrame.Shape.NoFrame)
        setup_host = QWidget()
        setup_layout = QVBoxLayout(setup_host)
        setup_layout.setContentsMargins(0, 0, 12, 0)
        setup_layout.setSpacing(12)

        source_panel = QFrame()
        source_panel.setProperty("panel", True)
        source_layout = QVBoxLayout(source_panel)
        source_layout.setContentsMargins(16, 14, 16, 16)
        source_title = QLabel("DATA SOURCE")
        source_title.setProperty("section", True)
        source_layout.addWidget(source_title)
        self.analysis_input = QLineEdit(str(self.state_data["analysis"].get("input_path") or ""))
        self.analysis_output = QLineEdit(str(self.state_data["analysis"].get("output_root") or DEFAULT_OUTPUT_ROOT))
        self.analysis_input.setCursorPosition(0)
        self.analysis_output.setCursorPosition(0)
        for label_text, field, handler in (
            ("Telemetry folder", self.analysis_input, self.browse_analysis_input),
            ("Output location", self.analysis_output, self.browse_analysis_output),
        ):
            source_layout.addWidget(QLabel(label_text))
            row = QHBoxLayout()
            row.addWidget(field, 1)
            browse = QPushButton("BROWSE")
            browse.clicked.connect(handler)
            row.addWidget(browse)
            source_layout.addLayout(row)
        setup_layout.addWidget(source_panel)

        options_panel = QFrame()
        options_panel.setProperty("panel", True)
        options_layout = QVBoxLayout(options_panel)
        options_layout.setContentsMargins(16, 14, 16, 16)
        options_title = QLabel("ANALYSIS SETUP")
        options_title.setProperty("section", True)
        options_layout.addWidget(options_title)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(10)
        settings = self.state_data["analysis"]
        self.analysis_mode = QComboBox()
        self.analysis_mode.addItems(["Auto", "Lap", "Autocross"])
        self.analysis_mode.setCurrentText(str(settings.get("mode", "auto")).title())
        self.analysis_sectors = QSpinBox()
        self.analysis_sectors.setRange(0, 20)
        self.analysis_sectors.setValue(int(settings.get("auto_sectors", 3)))
        self.analysis_minimum = QDoubleSpinBox()
        self.analysis_minimum.setRange(0.0, 3600.0)
        self.analysis_minimum.setDecimals(1)
        self.analysis_minimum.setSuffix(" s")
        self.analysis_minimum.setValue(float(settings.get("sector_report_min_seconds", 20.0)))
        self.analysis_driver_order = QLineEdit(str(settings.get("driver_order", "")))
        self.analysis_driver_order.setPlaceholderText("Alex C, Jimmy, Mercer")
        self.analysis_driver_offset = QSpinBox()
        self.analysis_driver_offset.setRange(0, 10000)
        self.analysis_driver_offset.setValue(int(settings.get("driver_order_offset", 0)))
        form.addRow("Timing mode", self.analysis_mode)
        form.addRow("Automatic sectors", self.analysis_sectors)
        form.addRow("Sector minimum", self.analysis_minimum)
        form.addRow("Driver order", self.analysis_driver_order)
        form.addRow("Files before drivers", self.analysis_driver_offset)
        options_layout.addLayout(form)
        self.analysis_gg = QCheckBox("Measured G-G lap prediction")
        self.analysis_gg.setChecked(bool(settings.get("gg_enabled", True)))
        self.analysis_ascii = QCheckBox("Include matching ASCII CSV files")
        self.analysis_ascii.setChecked(bool(settings.get("include_ascii", False)))
        options_layout.addWidget(self.analysis_gg)
        options_layout.addWidget(self.analysis_ascii)
        setup_layout.addWidget(options_panel)
        setup_layout.addStretch()
        setup_scroll.setWidget(setup_host)
        splitter.addWidget(setup_scroll)

        activity = QFrame()
        activity.setProperty("panel", True)
        activity_layout = QVBoxLayout(activity)
        activity_layout.setContentsMargins(16, 14, 16, 16)
        activity_head = QHBoxLayout()
        activity_title = QLabel("ANALYZER ACTIVITY")
        activity_title.setProperty("section", True)
        activity_head.addWidget(activity_title)
        activity_head.addStretch()
        self.cancel_analysis_button = QPushButton("CANCEL")
        self.cancel_analysis_button.setProperty("role", "danger")
        self.cancel_analysis_button.setEnabled(False)
        self.cancel_analysis_button.clicked.connect(self.cancel_analysis)
        self.run_analysis_button = QPushButton("RUN ANALYSIS")
        self.run_analysis_button.setProperty("role", "primary")
        self.run_analysis_button.clicked.connect(self.start_analysis)
        activity_head.addWidget(self.cancel_analysis_button)
        activity_head.addWidget(self.run_analysis_button)
        activity_layout.addLayout(activity_head)
        self.analysis_source_label = QLabel("Waiting for telemetry data")
        self.analysis_source_label.setStyleSheet("color:#666666;")
        self.analysis_source_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        activity_layout.addWidget(self.analysis_source_label)
        self.analysis_progress = QProgressBar()
        self.analysis_progress.setRange(0, 1)
        self.analysis_progress.setValue(0)
        activity_layout.addWidget(self.analysis_progress)
        self.analysis_log = QTextEdit()
        self.analysis_log.setReadOnly(True)
        self.analysis_log.setStyleSheet(
            "background:#0b0b0b;color:#dedede;border-color:#343434;font-family:Consolas;font-size:11px;"
        )
        self.analysis_log.setPlainText("Analyzer ready.")
        activity_layout.addWidget(self.analysis_log, 1)
        splitter.addWidget(activity)
        splitter.setSizes([470, 760])
        quick_layout.addWidget(splitter, 1)
        self.analysis_tabs.addTab(quick_tab, "QUICK REPORT")
        self.analysis_tabs.addTab(self.build_custom_workspace_tab(), "CUSTOM WORKSPACE")
        layout.addWidget(self.analysis_tabs, 1)
        return page

    def build_custom_workspace_tab(self) -> QWidget:
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(22, 16, 22, 16)
        tab_layout.setSpacing(0)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        controls_scroll = QScrollArea()
        self.custom_controls_scroll = controls_scroll
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setFrameShape(QFrame.Shape.NoFrame)
        controls_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        controls_scroll.setMinimumWidth(470)
        controls_scroll.setMaximumWidth(560)
        controls_host = QWidget()
        controls = QVBoxLayout(controls_host)
        controls.setContentsMargins(0, 0, 12, 0)
        controls.setSpacing(12)
        settings = self.state_data["custom_analysis"]

        source_panel, source_layout = self.setup_panel("DATA SOURCES")
        self.custom_source_path = QLineEdit(str(settings.get("source_path") or self.analysis_input.text()))
        self.custom_source_path.setCursorPosition(0)
        source_path_row = QHBoxLayout()
        source_path_row.addWidget(self.custom_source_path, 1)
        source_browse = QPushButton("BROWSE")
        source_browse.clicked.connect(self.browse_custom_source)
        source_path_row.addWidget(source_browse)
        source_layout.addLayout(source_path_row)
        self.custom_scan_button = QPushButton("SCAN CHANNELS")
        self.custom_scan_button.setProperty("role", "primary")
        self.custom_scan_button.clicked.connect(self.scan_custom_sources)
        source_layout.addWidget(self.custom_scan_button)
        source_head = QHBoxLayout()
        self.custom_source_summary = QLabel("No telemetry folder scanned")
        self.custom_source_summary.setStyleSheet("color:#666666;")
        source_head.addWidget(self.custom_source_summary, 1)
        source_layout.addLayout(source_head)
        source_actions = QHBoxLayout()
        source_actions.addStretch()
        source_all = QPushButton("SELECT ALL")
        source_all.clicked.connect(lambda: self.set_all_custom_sources(True))
        source_none = QPushButton("CLEAR")
        source_none.clicked.connect(lambda: self.set_all_custom_sources(False))
        source_actions.addWidget(source_all)
        source_actions.addWidget(source_none)
        source_layout.addLayout(source_actions)
        self.custom_source_list = QListWidget()
        self.custom_source_list.setMinimumHeight(150)
        self.custom_source_list.itemChanged.connect(self.custom_source_selection_changed)
        source_layout.addWidget(self.custom_source_list)
        controls.addWidget(source_panel)

        channel_panel, channel_layout = self.setup_panel("CHANNELS AND PLOT")
        channel_form = QFormLayout()
        channel_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        channel_form.setHorizontalSpacing(18)
        channel_form.setVerticalSpacing(9)
        self.custom_title = QLineEdit(str(settings.get("title") or "Custom Analysis Workspace"))
        self.custom_x_channel = QComboBox()
        self.custom_x_channel.setMaxVisibleItems(24)
        self.custom_plot_style = QComboBox()
        self.custom_plot_style.addItem("Line", "line")
        self.custom_plot_style.addItem("Scatter", "scatter")
        style_index = self.custom_plot_style.findData(str(settings.get("plot_style") or "line"))
        self.custom_plot_style.setCurrentIndex(max(0, style_index))
        channel_form.addRow("Report name", self.custom_title)
        channel_form.addRow("X axis", self.custom_x_channel)
        channel_form.addRow("Plot style", self.custom_plot_style)
        channel_layout.addLayout(channel_form)
        self.custom_channel_search = QLineEdit()
        self.custom_channel_search.setPlaceholderText("Search available channels")
        self.custom_channel_search.textChanged.connect(self.filter_custom_channels)
        channel_layout.addWidget(self.custom_channel_search)
        self.custom_y_channels = QListWidget()
        self.custom_y_channels.setMinimumHeight(190)
        self.custom_y_channels.itemChanged.connect(self.custom_y_selection_changed)
        channel_layout.addWidget(self.custom_y_channels)
        selected_head = QHBoxLayout()
        self.custom_channel_summary = QLabel("No channels available")
        self.custom_channel_summary.setStyleSheet("color:#666666;")
        selected_head.addWidget(self.custom_channel_summary, 1)
        clear_channels = QPushButton("CLEAR Y")
        clear_channels.clicked.connect(self.clear_custom_y_channels)
        selected_head.addWidget(clear_channels)
        channel_layout.addLayout(selected_head)
        controls.addWidget(channel_panel)

        processing_panel, processing_layout = self.setup_panel("PROCESSING")
        processing_form = QFormLayout()
        processing_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        processing_form.setHorizontalSpacing(18)
        processing_form.setVerticalSpacing(9)
        self.custom_filter = QLineEdit(str(settings.get("filter") or ""))
        self.custom_filter.setPlaceholderText("Example: [speed_mph] > 10")
        self.custom_filter.setToolTip("Use [Channel Name] references with comparisons, and/or, and supported math functions.")
        self.custom_smoothing = QSpinBox()
        self.custom_smoothing.setRange(1, 1000)
        self.custom_smoothing.setValue(int(settings.get("smoothing_points", 1)))
        self.custom_smoothing.setSuffix(" points")
        self.custom_max_points = QSpinBox()
        self.custom_max_points.setRange(200, 100000)
        self.custom_max_points.setSingleStep(500)
        self.custom_max_points.setValue(int(settings.get("max_plot_points", 5000)))
        processing_form.addRow("Filter", self.custom_filter)
        processing_form.addRow("Smoothing", self.custom_smoothing)
        processing_form.addRow("Plot detail", self.custom_max_points)
        processing_layout.addLayout(processing_form)
        controls.addWidget(processing_panel)

        formula_panel, formula_layout = self.setup_panel("CALCULATED CHANNELS")
        self.custom_formula_table = QTableWidget(0, 3)
        self.custom_formula_table.setHorizontalHeaderLabels(["NAME", "UNIT", "EXPRESSION"])
        self.custom_formula_table.verticalHeader().setVisible(False)
        self.custom_formula_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.custom_formula_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.custom_formula_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.custom_formula_table.setMinimumHeight(140)
        self.custom_formula_table.setToolTip(
            "Reference channels as [Channel Name]. Functions include abs, sqrt, smooth, hold, derivative, integral, clip, and where."
        )
        self.custom_formula_table.cellChanged.connect(self.custom_formula_changed)
        formula_layout.addWidget(self.custom_formula_table)
        formula_buttons = QHBoxLayout()
        add_formula = QPushButton("ADD FORMULA")
        add_formula.clicked.connect(self.add_custom_formula)
        remove_formula = QPushButton("REMOVE")
        remove_formula.clicked.connect(self.remove_custom_formula)
        formula_buttons.addWidget(add_formula)
        formula_buttons.addWidget(remove_formula)
        formula_buttons.addStretch()
        formula_layout.addLayout(formula_buttons)
        controls.addWidget(formula_panel)

        preset_panel, preset_layout = self.setup_panel("WORKSPACE PRESETS")
        self.custom_preset = QComboBox()
        preset_layout.addWidget(self.custom_preset)
        preset_actions = QHBoxLayout()
        preset_actions.addStretch()
        load_preset = QPushButton("LOAD")
        load_preset.clicked.connect(self.load_custom_preset)
        save_preset = QPushButton("SAVE AS")
        save_preset.clicked.connect(self.save_custom_preset)
        delete_preset = QPushButton("DELETE")
        delete_preset.setProperty("role", "danger")
        delete_preset.clicked.connect(self.delete_custom_preset)
        preset_actions.addWidget(load_preset)
        preset_actions.addWidget(save_preset)
        preset_actions.addWidget(delete_preset)
        preset_layout.addLayout(preset_actions)
        controls.addWidget(preset_panel)

        output_panel, output_layout = self.setup_panel("OUTPUT")
        self.custom_output = QLineEdit(str(settings.get("output_root") or self.analysis_output.text()))
        output_row = QHBoxLayout()
        output_row.addWidget(self.custom_output, 1)
        output_browse = QPushButton("BROWSE")
        output_browse.clicked.connect(self.browse_custom_output)
        output_row.addWidget(output_browse)
        output_layout.addLayout(output_row)
        controls.addWidget(output_panel)
        controls.addStretch()
        controls_scroll.setWidget(controls_host)
        splitter.addWidget(controls_scroll)

        preview_panel = QFrame()
        preview_panel.setProperty("panel", True)
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_head = QHBoxLayout()
        preview_head.setContentsMargins(16, 10, 12, 10)
        self.custom_status = QLabel("Workspace ready")
        self.custom_status.setProperty("section", True)
        preview_head.addWidget(self.custom_status)
        preview_head.addStretch()
        self.custom_preview_button = QPushButton("PREVIEW")
        self.custom_preview_button.clicked.connect(lambda: self.start_custom_analysis(False))
        self.custom_save_button = QPushButton("SAVE REPORT")
        self.custom_save_button.setProperty("role", "primary")
        self.custom_save_button.clicked.connect(lambda: self.start_custom_analysis(True))
        preview_head.addWidget(self.custom_preview_button)
        preview_head.addWidget(self.custom_save_button)
        preview_layout.addLayout(preview_head)
        self.custom_preview = QWebEngineView()
        self.custom_preview.setHtml(
            "<html><body style='margin:0;background:#0b0b0b;color:#a8a8a8;font:15px Segoe UI;"
            "display:grid;place-items:center'><div>CUSTOM ANALYSIS WORKSPACE</div></body></html>"
        )
        preview_layout.addWidget(self.custom_preview, 1)
        splitter.addWidget(preview_panel)
        splitter.setSizes([500, 800])
        tab_layout.addWidget(splitter, 1)

        for formula in settings.get("formulas", []):
            if isinstance(formula, dict):
                self.add_custom_formula(formula)
        self.refresh_custom_presets()
        QTimer.singleShot(0, self.restore_custom_workspace)
        return tab

    def build_results_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        header = PageHeader("Analysis archive", "Reports")
        self.open_folder_button = QPushButton("OPEN FOLDER")
        self.open_folder_button.clicked.connect(self.open_result_folder)
        self.open_file_button = QPushButton("OPEN EXTERNAL")
        self.open_file_button.setProperty("role", "primary")
        self.open_file_button.clicked.connect(self.open_result_file)
        header.actions.addWidget(self.open_folder_button)
        header.actions.addWidget(self.open_file_button)
        layout.addWidget(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setContentsMargins(22, 16, 22, 16)
        archive = QFrame()
        archive.setProperty("panel", True)
        archive.setMinimumWidth(250)
        archive.setMaximumWidth(360)
        archive_layout = QVBoxLayout(archive)
        archive_layout.setContentsMargins(12, 12, 12, 12)
        history_title = QLabel("RECENT ANALYSES")
        history_title.setProperty("section", True)
        archive_layout.addWidget(history_title)
        self.history_list = QListWidget()
        self.history_list.currentItemChanged.connect(self.history_selected)
        archive_layout.addWidget(self.history_list, 1)
        files_title = QLabel("RESULT FILES")
        files_title.setProperty("section", True)
        archive_layout.addWidget(files_title)
        self.result_list = QListWidget()
        self.result_list.setMaximumHeight(230)
        self.result_list.currentItemChanged.connect(self.result_selected)
        self.result_list.itemDoubleClicked.connect(lambda _item: self.open_result_file())
        archive_layout.addWidget(self.result_list)
        splitter.addWidget(archive)

        self.result_preview = QStackedWidget()
        blank = QWidget()
        blank_layout = QVBoxLayout(blank)
        blank_label = QLabel("Select an analysis report")
        blank_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        blank_label.setStyleSheet("color:#808080;font-size:15px;")
        blank_layout.addWidget(blank_label)
        self.result_preview.addWidget(blank)
        self.web_view = QWebEngineView()
        self.result_preview.addWidget(self.web_view)
        self.csv_table = QTableWidget()
        self.csv_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.csv_table.setAlternatingRowColors(True)
        self.result_preview.addWidget(self.csv_table)
        splitter.addWidget(self.result_preview)
        splitter.setSizes([300, 930])
        layout.addWidget(splitter, 1)
        return page

    def show_page(self, name: str) -> None:
        indexes = {"dashboard": 0, "analysis": 1, "results": 2}
        labels = {"dashboard": "LIVE OPERATIONS", "analysis": "OFFLINE ANALYSIS", "results": "REPORT ARCHIVE"}
        index = indexes[name]
        self.pages.setCurrentIndex(index)
        self.context_label.setText(labels[name])
        for key, button in self.nav_buttons.items():
            button.setChecked(key == name)

    def update_clock(self) -> None:
        self.clock_label.setText(datetime.now().astimezone().strftime("%a %b %d  %H:%M:%S"))

    def restore_window_geometry(self) -> None:
        geometry = str(self.state_data.get("geometry") or "")
        try:
            size, *position = geometry.split("+")
            width, height = (int(value) for value in size.lower().split("x", 1))
            self.resize(max(width, 1100), max(height, 700))
            if len(position) == 2:
                self.move(int(position[0]), int(position[1]))
        except (TypeError, ValueError):
            pass

    def save_state_safely(self) -> None:
        try:
            save_state(self.state_data)
        except OSError as exc:
            QMessageBox.critical(self, "Settings", f"Could not save desktop settings:\n\n{exc}")

    def start_worker(self, worker: Worker) -> None:
        self.active_workers.add(worker)
        worker.signals.finished.connect(self.release_worker)
        self.thread_pool.start(worker)

    def release_worker(self, worker: Worker) -> None:
        self.active_workers.discard(worker)

    def mark_run_metadata_dirty(self, *_args: Any) -> None:
        self.run_metadata_dirty = True
        self.metadata_save_status.setText("Unsaved run information")
        self.metadata_save_status.setStyleSheet("color:#9a6400;")

    def mark_timing_setup_dirty(self, *_args: Any) -> None:
        self.timing_setup_dirty = True
        self.timing_save_status.setText("Unsaved timing changes")
        self.timing_save_status.setStyleSheet("color:#9a6400;")

    def timing_mode_changed(self, index: int = -1) -> None:
        autocross = self.timing_mode_input.currentData() == "autocross"
        for key, field in self.timing_gate_inputs.items():
            if key.startswith("finish_"):
                field.setEnabled(autocross)
        self.refresh_live_gps_buttons()
        if index >= 0:
            self.mark_timing_setup_dirty()

    def refresh_live_gps_buttons(self) -> None:
        timing_busy = self.timing_save_active or self.timing_reset_active
        autocross = self.timing_mode_input.currentData() == "autocross"
        for latitude_key, button in self.timing_live_buttons.items():
            gate_available = not latitude_key.startswith("finish_") or autocross
            button.setEnabled(
                self.pi_connected
                and self.current_gps_position is not None
                and not timing_busy
                and gate_available
            )

    def update_live_gps(self, fields: dict[str, Any]) -> None:
        self.current_gps_position = live_gps_position(fields)
        if self.current_gps_position is None:
            latitude_text = longitude_text = "--"
        else:
            latitude_text = format_coordinate(self.current_gps_position[0])
            longitude_text = format_coordinate(self.current_gps_position[1])
        self.dashboard_live_latitude.setText(f"LAT {latitude_text}")
        self.dashboard_live_longitude.setText(f"LON {longitude_text}")
        self.timing_live_latitude.setText(f"LAT {latitude_text}")
        self.timing_live_longitude.setText(f"LON {longitude_text}")
        self.refresh_live_gps_buttons()

    def use_live_gps_for_gate(self, latitude_key: str, longitude_key: str) -> None:
        if self.current_gps_position is None:
            return
        latitude, longitude = self.current_gps_position
        self.timing_gate_inputs[latitude_key].setText(format_coordinate(latitude))
        self.timing_gate_inputs[longitude_key].setText(format_coordinate(longitude))
        self.mark_timing_setup_dirty()

    def refresh_setup_stream_button(self) -> None:
        active = bool(self.pi_last_snapshot.get("setup_streaming"))
        self.setup_stream_button.setText("STOP SETUP STREAM" if active else "START SETUP STREAM")
        self.setup_stream_button.setProperty("role", "danger" if active else "primary")
        self.setup_stream_button.style().unpolish(self.setup_stream_button)
        self.setup_stream_button.style().polish(self.setup_stream_button)
        self.setup_stream_button.setEnabled(
            self.pi_connected
            and not self.setup_stream_action_active
            and not bool(self.pi_last_snapshot.get("logging"))
        )

    def toggle_setup_stream(self) -> None:
        if not self.pi_connected or self.setup_stream_action_active:
            return
        enable = not bool(self.pi_last_snapshot.get("setup_streaming"))
        if enable and self.pi_last_snapshot.get("logging"):
            QMessageBox.warning(
                self,
                "Track setup data",
                "Stop the active logging run before starting the setup stream.",
            )
            return
        self.setup_stream_action_active = True
        self.refresh_setup_stream_button()
        self.setup_stream_button.setText("STARTING..." if enable else "STOPPING...")
        generation = self.pi_generation
        worker = Worker(
            pi_api_request,
            self.pi_endpoint,
            "api/setup_stream",
            "POST",
            {"enabled": enable},
            5.0,
        )
        worker.signals.result.connect(partial(self.setup_stream_changed, generation, enable))
        worker.signals.error.connect(partial(self.setup_stream_change_failed, generation))
        self.start_worker(worker)

    def setup_stream_changed(self, generation: int, enabled: bool, result: object) -> None:
        self.setup_stream_action_active = False
        if generation != self.pi_generation or not isinstance(result, dict):
            return
        self.pi_last_snapshot["setup_streaming"] = bool(result.get("setup_streaming", enabled))
        self.refresh_setup_stream_button()
        self.pi_next_poll_at = 0.0
        self.maybe_poll_pi()

    def setup_stream_change_failed(self, generation: int, error: str) -> None:
        self.setup_stream_action_active = False
        self.refresh_setup_stream_button()
        if generation != self.pi_generation:
            return
        QMessageBox.critical(
            self,
            "Track setup data",
            "Could not change the non-recording setup stream. Update the Pi logger and try again."
            f"\n\n{error}",
        )

    def set_drive_day_controls_enabled(self, enabled: bool) -> None:
        self.metadata_save_button.setEnabled(enabled and not self.metadata_save_active)
        timing_busy = self.timing_save_active or self.timing_reset_active
        self.timing_save_button.setEnabled(enabled and not timing_busy)
        self.timing_reset_button.setEnabled(enabled and not timing_busy)
        self.refresh_live_gps_buttons()
        self.refresh_setup_stream_button()

    @staticmethod
    def metadata_widget_value(widget: QWidget) -> str:
        if isinstance(widget, QLineEdit):
            return widget.text().strip()
        if isinstance(widget, QComboBox):
            return widget.currentText().strip()
        if isinstance(widget, QTextEdit):
            return widget.toPlainText().strip()
        return ""

    @staticmethod
    def set_metadata_widget_value(widget: QWidget, value: Any) -> None:
        text = "" if value is None else str(value)
        widget.blockSignals(True)
        try:
            if isinstance(widget, QLineEdit):
                widget.setText(text)
            elif isinstance(widget, QComboBox):
                index = widget.findText(text)
                widget.setCurrentIndex(index if index >= 0 else 0)
            elif isinstance(widget, QTextEdit):
                widget.setPlainText(text)
        finally:
            widget.blockSignals(False)

    def run_metadata_payload(self) -> dict[str, str]:
        return {
            key: self.metadata_widget_value(self.run_metadata_inputs[key])
            for key in EDITABLE_RUN_METADATA_FIELDS
        }

    def populate_run_metadata(self, payload: dict[str, Any]) -> None:
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else payload
        for key in EDITABLE_RUN_METADATA_FIELDS:
            self.set_metadata_widget_value(self.run_metadata_inputs[key], metadata.get(key, ""))
        self.setup_date_label.setText(str(payload.get("date") or "--"))
        self.next_run_label.setText(str(payload.get("next_run_id") or "--"))
        self.last_run_metadata_snapshot = {
            "metadata": {key: metadata.get(key, "") for key in EDITABLE_RUN_METADATA_FIELDS},
            "date": payload.get("date"),
            "next_run_id": payload.get("next_run_id"),
        }
        self.run_metadata_dirty = False
        self.metadata_save_status.setText("Loaded from Pi")
        self.metadata_save_status.setStyleSheet("color:#187557;")

    def populate_timing_setup(self, payload: dict[str, Any]) -> None:
        timing = payload.get("timing") if isinstance(payload.get("timing"), dict) else payload
        config = timing.get("config") if isinstance(timing.get("config"), dict) else payload.get("config")
        config = config if isinstance(config, dict) else {}
        mode = str(config.get("mode") or "lap")
        mode_index = self.timing_mode_input.findData(mode)
        self.timing_mode_input.blockSignals(True)
        self.timing_mode_input.setCurrentIndex(mode_index if mode_index >= 0 else 0)
        self.timing_mode_input.blockSignals(False)
        for prefix in ("start", "finish"):
            line = config.get(f"{prefix}_line")
            line = line if isinstance(line, dict) else {}
            for coordinate in ("lat1", "lon1", "lat2", "lon2"):
                field = self.timing_gate_inputs[f"{prefix}_{coordinate}"]
                value = line.get(coordinate)
                field.blockSignals(True)
                field.setText("" if value is None else str(value))
                field.blockSignals(False)
        self.timing_min_speed_input.blockSignals(True)
        self.timing_min_gap_input.blockSignals(True)
        try:
            self.timing_min_speed_input.setValue(float(config.get("min_speed_mph", 5.0)))
            self.timing_min_gap_input.setValue(float(config.get("min_gap_s", 8.0)))
        except (TypeError, ValueError):
            self.timing_min_speed_input.setValue(5.0)
            self.timing_min_gap_input.setValue(8.0)
        finally:
            self.timing_min_speed_input.blockSignals(False)
            self.timing_min_gap_input.blockSignals(False)
        self.timing_mode_changed()
        self.last_timing_config_snapshot = json.loads(json.dumps(config))
        configured = bool(timing.get("configured"))
        self.timing_config_status.setText(str(timing.get("status") or ("Configured" if configured else "Not configured")))
        self.timing_setup_dirty = False
        self.timing_save_status.setText("Loaded from Pi" if configured else "Enter gate coordinates")
        self.timing_save_status.setStyleSheet("color:#187557;" if configured else "color:#6d6d6d;")

    def update_drive_day_setup(self, snapshot: dict[str, Any], force: bool = False) -> None:
        run_metadata = snapshot.get("run_metadata")
        if isinstance(run_metadata, dict):
            self.setup_date_label.setText(str(run_metadata.get("date") or "--"))
            self.next_run_label.setText(str(run_metadata.get("next_run_id") or "--"))
            metadata = run_metadata.get("metadata") if isinstance(run_metadata.get("metadata"), dict) else run_metadata
            run_signature = {
                "metadata": {key: metadata.get(key, "") for key in EDITABLE_RUN_METADATA_FIELDS},
                "date": run_metadata.get("date"),
                "next_run_id": run_metadata.get("next_run_id"),
            }
            if force or (not self.run_metadata_dirty and run_signature != self.last_run_metadata_snapshot):
                self.populate_run_metadata(run_metadata)
        timing = snapshot.get("timing")
        if isinstance(timing, dict):
            self.timing_config_status.setText(str(timing.get("status") or "Not configured"))
            config = timing.get("config") if isinstance(timing.get("config"), dict) else {}
            if force or (not self.timing_setup_dirty and config != self.last_timing_config_snapshot):
                self.populate_timing_setup(timing)

    def save_run_metadata(self) -> None:
        if not self.pi_connected:
            QMessageBox.warning(self, "Drive day setup", "Connect to the Raspberry Pi before saving run information.")
            return
        if self.metadata_save_active:
            return
        self.metadata_save_active = True
        self.set_drive_day_controls_enabled(True)
        self.metadata_save_status.setText("Saving run information...")
        self.metadata_save_status.setStyleSheet("color:#6d6d6d;")
        generation = self.pi_generation
        worker = Worker(
            pi_api_request,
            self.pi_endpoint,
            "api/run_metadata",
            "POST",
            self.run_metadata_payload(),
            5.0,
        )
        worker.signals.result.connect(partial(self.run_metadata_saved, generation))
        worker.signals.error.connect(partial(self.run_metadata_save_failed, generation))
        self.start_worker(worker)

    def run_metadata_saved(self, generation: int, result: object) -> None:
        self.metadata_save_active = False
        self.set_drive_day_controls_enabled(self.pi_connected)
        if generation != self.pi_generation or not isinstance(result, dict):
            return
        self.populate_run_metadata(result)
        next_run = str(result.get("next_run_id") or "next run")
        self.metadata_save_status.setText(f"Saved for {next_run}")
        self.metadata_save_status.setStyleSheet("color:#187557;font-weight:600;")

    def run_metadata_save_failed(self, generation: int, error: str) -> None:
        self.metadata_save_active = False
        self.set_drive_day_controls_enabled(self.pi_connected)
        if generation != self.pi_generation:
            return
        self.metadata_save_status.setText("Run information save failed")
        self.metadata_save_status.setStyleSheet("color:#ad3e37;")
        QMessageBox.critical(self, "Drive day setup", f"Could not save run information:\n\n{error}")

    def timing_setup_payload(self) -> dict[str, Any]:
        mode = str(self.timing_mode_input.currentData())

        def coordinate(key: str, minimum: float, maximum: float) -> float:
            text = self.timing_gate_inputs[key].text().strip()
            if not text:
                raise ValueError(f"Enter {key.replace('_', ' ')}.")
            value = float(text)
            if not minimum <= value <= maximum:
                raise ValueError(f"{key.replace('_', ' ').title()} must be between {minimum:g} and {maximum:g}.")
            return value

        payload: dict[str, Any] = {
            "mode": mode,
            "start_lat1": coordinate("start_lat1", -90.0, 90.0),
            "start_lon1": coordinate("start_lon1", -180.0, 180.0),
            "start_lat2": coordinate("start_lat2", -90.0, 90.0),
            "start_lon2": coordinate("start_lon2", -180.0, 180.0),
            "min_speed_mph": self.timing_min_speed_input.value(),
            "min_gap_s": self.timing_min_gap_input.value(),
        }
        if payload["start_lat1"] == payload["start_lat2"] and payload["start_lon1"] == payload["start_lon2"]:
            raise ValueError("Start gate points must be different.")
        if mode == "autocross":
            payload.update({
                "finish_lat1": coordinate("finish_lat1", -90.0, 90.0),
                "finish_lon1": coordinate("finish_lon1", -180.0, 180.0),
                "finish_lat2": coordinate("finish_lat2", -90.0, 90.0),
                "finish_lon2": coordinate("finish_lon2", -180.0, 180.0),
            })
            if payload["finish_lat1"] == payload["finish_lat2"] and payload["finish_lon1"] == payload["finish_lon2"]:
                raise ValueError("Finish gate points must be different.")
        return payload

    def save_timing_setup(self) -> None:
        if not self.pi_connected:
            QMessageBox.warning(self, "Timing setup", "Connect to the Raspberry Pi before saving timing setup.")
            return
        if self.timing_save_active:
            return
        try:
            payload = self.timing_setup_payload()
        except ValueError as exc:
            QMessageBox.warning(self, "Timing setup", str(exc))
            return
        self.timing_save_active = True
        self.set_drive_day_controls_enabled(True)
        self.timing_save_status.setText("Saving timing setup...")
        self.timing_save_status.setStyleSheet("color:#6d6d6d;")
        generation = self.pi_generation
        worker = Worker(pi_api_request, self.pi_endpoint, "api/config", "POST", payload, 5.0)
        worker.signals.result.connect(partial(self.timing_setup_saved, generation))
        worker.signals.error.connect(partial(self.timing_setup_save_failed, generation))
        self.start_worker(worker)

    def timing_setup_saved(self, generation: int, result: object) -> None:
        self.timing_save_active = False
        self.set_drive_day_controls_enabled(self.pi_connected)
        if generation != self.pi_generation or not isinstance(result, dict):
            return
        self.populate_timing_setup(result)
        self.timing_save_status.setText("Timing setup saved")
        self.timing_save_status.setStyleSheet("color:#187557;font-weight:600;")

    def timing_setup_save_failed(self, generation: int, error: str) -> None:
        self.timing_save_active = False
        self.set_drive_day_controls_enabled(self.pi_connected)
        if generation != self.pi_generation:
            return
        self.timing_save_status.setText("Timing setup save failed")
        self.timing_save_status.setStyleSheet("color:#ad3e37;")
        QMessageBox.critical(self, "Timing setup", f"Could not save timing setup:\n\n{error}")

    def reset_timing(self) -> None:
        if not self.pi_connected or self.timing_reset_active:
            return
        answer = QMessageBox.question(
            self,
            "Reset timing",
            "Reset the current lap count, best lap, and timing traces?\n\nThe saved start and finish gates will remain configured.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.timing_reset_active = True
        self.set_drive_day_controls_enabled(True)
        self.timing_save_status.setText("Resetting timing data...")
        generation = self.pi_generation
        worker = Worker(pi_api_request, self.pi_endpoint, "api/reset_timing", "POST", {}, 5.0)
        worker.signals.result.connect(partial(self.timing_reset_finished, generation))
        worker.signals.error.connect(partial(self.timing_reset_failed, generation))
        self.start_worker(worker)

    def timing_reset_finished(self, generation: int, result: object) -> None:
        self.timing_reset_active = False
        self.set_drive_day_controls_enabled(self.pi_connected)
        if generation != self.pi_generation or not isinstance(result, dict):
            return
        self.populate_timing_setup(result)
        self.timing_save_status.setText("Timing laps and traces reset")
        self.timing_save_status.setStyleSheet("color:#187557;font-weight:600;")

    def timing_reset_failed(self, generation: int, error: str) -> None:
        self.timing_reset_active = False
        self.set_drive_day_controls_enabled(self.pi_connected)
        if generation != self.pi_generation:
            return
        self.timing_save_status.setText("Timing reset failed")
        self.timing_save_status.setStyleSheet("color:#ad3e37;")
        QMessageBox.critical(self, "Timing setup", f"Could not reset timing data:\n\n{error}")

    def initial_pi_connect(self) -> None:
        if self.pi_endpoint:
            self.pi_address.setText(self.pi_endpoint)
            self.connect_pi()
            return
        value, accepted = QInputDialog.getText(
            self,
            "Connect to VN300 Pi",
            "Raspberry Pi IP address or hostname:",
            text="raspberrypi.local",
        )
        if accepted and value.strip():
            self.pi_address.setText(value.strip())
            self.connect_pi()

    def set_pi_status(self, text: str, status: str) -> None:
        self.connection_status.set_status(text, status)
        self.dashboard_header_status.set_status(text, status)
        self.rail_pi_status.setText(f"PI {text.upper()}")

    def connect_pi(self) -> None:
        try:
            endpoint = normalize_pi_endpoint(self.pi_address.text())
        except ValueError as exc:
            QMessageBox.warning(self, "Pi address", str(exc))
            return
        self.pi_endpoint = endpoint
        self.pi_address.setText(endpoint)
        self.pi_generation += 1
        self.pi_request_active = False
        self.pi_connected = False
        self.pi_next_poll_at = 0.0
        self.set_pi_status("Connecting", "working")
        self.set_drive_day_controls_enabled(False)
        self.maybe_poll_pi()

    def maybe_poll_pi(self) -> None:
        if not self.pi_endpoint or self.pi_request_active or time.monotonic() < self.pi_next_poll_at:
            return
        self.pi_request_active = True
        generation = self.pi_generation
        endpoint = self.pi_endpoint
        started = time.monotonic()
        worker = Worker(fetch_pi_snapshot, endpoint)
        worker.signals.result.connect(
            lambda snapshot: self.handle_pi_snapshot(generation, endpoint, started, snapshot)
        )
        worker.signals.error.connect(lambda error: self.handle_pi_error(generation, error))
        self.start_worker(worker)

    def handle_pi_snapshot(self, generation: int, endpoint: str, started: float, snapshot: object) -> None:
        if generation != self.pi_generation or not isinstance(snapshot, dict):
            return
        self.pi_request_active = False
        newly_connected = not self.pi_connected
        self.pi_connected = True
        self.pi_next_poll_at = time.monotonic() + 0.45
        self.pi_latency_ms = (time.monotonic() - started) * 1000.0
        if endpoint != self.state_data.get("pi_endpoint"):
            self.state_data["pi_endpoint"] = endpoint
            self.save_state_safely()
        self.pi_last_snapshot = snapshot
        if snapshot.get("logging"):
            pi_status = "Logging"
        elif snapshot.get("setup_streaming"):
            pi_status = "Setup stream"
        else:
            pi_status = "Online"
        self.set_pi_status(pi_status, "online")
        self.logger_button.setEnabled(not self.logger_update_active)
        self.update_dashboard(snapshot)
        self.update_drive_day_setup(snapshot, force=newly_connected)
        self.set_drive_day_controls_enabled(True)
        if newly_connected and not self.logger_update_active:
            QTimer.singleShot(350, partial(self.check_logger_update, False))

    def handle_pi_error(self, generation: int, error: str) -> None:
        if generation != self.pi_generation:
            return
        self.pi_request_active = False
        self.pi_connected = False
        self.pi_next_poll_at = time.monotonic() + 2.5
        self.set_pi_status("Offline", "offline")
        self.update_live_gps({})
        self.set_drive_day_controls_enabled(False)
        if not self.logger_update_active:
            self.logger_button.setEnabled(False)
            self.logger_button.setText("CHECK LOGGER")
        self.health_labels["latency"].setText(error[:55])

    def update_dashboard(self, snapshot: dict[str, Any]) -> None:
        fields = snapshot.get("fields") if isinstance(snapshot.get("fields"), dict) else {}
        timing = snapshot.get("timing") if isinstance(snapshot.get("timing"), dict) else {}
        speed = fields.get("Speed_mph")
        self.update_live_gps(fields)
        self.metric_cards["speed"].set_value(format_number(speed, 1))
        self.metric_cards["lat_g"].set_value(format_number(fields.get("Lateral_G"), 2))
        self.metric_cards["long_g"].set_value(format_number(fields.get("Longitudinal_G"), 2))
        self.metric_cards["yaw"].set_value(format_number(fields.get("Yaw_deg"), 1))
        self.metric_cards["current"].set_value(format_lap_time(timing.get("current_elapsed_s")))
        self.metric_cards["best"].set_value(format_lap_time(timing.get("best_lap_s")))
        delta = timing.get("live_delta_s") if timing.get("live_delta_available") else None
        delta_color = "#16845b" if isinstance(delta, (int, float)) and delta < 0 else "#b43c36" if isinstance(delta, (int, float)) else ""
        self.metric_cards["delta"].set_value(format_number(delta, 3), delta_color)
        self.metric_cards["laps"].set_value(str(timing.get("lap_count", 0)))
        if isinstance(speed, (int, float)) and math.isfinite(speed):
            self.pi_speed_history.append(float(speed))
            self.pi_speed_history = self.pi_speed_history[-300:]
        self.speed_plot.set_values(self.pi_speed_history)
        self.track_plot.set_timing(timing)
        self.update_lap_table(timing.get("completed") or [])
        self.installed_logger_version = str(snapshot.get("logger_version") or "unknown")
        self.health_labels["session"].setText(str(snapshot.get("session") or "--"))
        destination = str(snapshot.get("log_destination") or "--")
        log_health = str(snapshot.get("log_health") or "--")
        self.health_labels["log"].setText(f"{destination} / {log_health}")
        self.health_labels["storage"].setText(f"{format_number(snapshot.get('free_space_mb'), 0)} MB")
        self.health_labels["logger"].setText(
            f"v{self.installed_logger_version}" if self.installed_logger_version != "unknown" else "unknown"
        )
        self.health_labels["cpu"].setText(f"{format_number(snapshot.get('cpu_temp_c'), 1)} C")
        self.health_labels["latency"].setText(f"{self.pi_latency_ms:.0f} ms")

    def update_lap_table(self, rows: list[Any]) -> None:
        valid = [row for row in rows[-30:] if isinstance(row, dict)]
        self.lap_table.setRowCount(len(valid))
        for row_index, row in enumerate(reversed(valid)):
            delta = row.get("delta_to_best_s")
            values = (
                str(row.get("number", "")),
                str(row.get("type", "")),
                format_lap_time(row.get("duration_s")),
                "--" if delta is None else f"{float(delta):+.3f}",
                str(row.get("warning", "") or "Valid"),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in (0, 2, 3):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.lap_table.setItem(row_index, column, item)

    def change_ssh_user(self) -> None:
        current = str(self.state_data.get("pi_ssh_user") or "vectornav")
        value, accepted = QInputDialog.getText(self, "Pi SSH account", "Raspberry Pi SSH username:", text=current)
        if not accepted or not value.strip():
            return
        try:
            username = validate_ssh_username(value)
        except ValueError as exc:
            QMessageBox.warning(self, "Pi SSH account", str(exc))
            return
        self.state_data["pi_ssh_user"] = username
        self.save_state_safely()

    def logger_action(self) -> None:
        if (
            self.available_logger_version
            and logger_update_available(self.installed_logger_version, self.available_logger_version)
        ):
            self.offer_logger_update(self.available_logger_version)
        else:
            self.check_logger_update(True)

    def check_logger_update(self, manual: bool = True) -> None:
        if not self.pi_connected:
            if manual:
                QMessageBox.warning(self, "Pi logger update", "Connect to the Raspberry Pi before checking its logger.")
            return
        if self.logger_check_active or self.logger_update_active:
            return
        self.logger_check_active = True
        self.logger_button.setText("CHECKING...")
        self.logger_button.setEnabled(False)
        worker = Worker(fetch_latest_pi_logger_version)
        worker.signals.result.connect(partial(self.handle_logger_version, manual))
        worker.signals.error.connect(partial(self.handle_logger_version_error, manual))
        self.start_worker(worker)

    def handle_logger_version(self, manual: bool, version: object) -> None:
        self.logger_check_active = False
        available = str(version)
        self.available_logger_version = available
        if logger_update_available(self.installed_logger_version, available):
            self.logger_button.setText(f"UPDATE LOGGER v{available}")
            self.logger_button.setEnabled(True)
            if manual or available not in self.logger_prompted_versions:
                self.logger_prompted_versions.add(available)
                self.offer_logger_update(available)
            return
        self.logger_button.setText(f"LOGGER v{self.installed_logger_version}")
        self.logger_button.setEnabled(True)
        if manual:
            QMessageBox.information(
                self,
                "Pi logger update",
                f"The Pi logger is current at v{self.installed_logger_version}.",
            )

    def handle_logger_version_error(self, manual: bool, error: str) -> None:
        self.logger_check_active = False
        self.logger_button.setText("CHECK LOGGER")
        self.logger_button.setEnabled(self.pi_connected)
        if manual:
            QMessageBox.critical(self, "Pi logger update", f"Could not check the public logger version:\n\n{error}")

    def offer_logger_update(self, available: str) -> None:
        answer = QMessageBox.question(
            self,
            "Pi logger update available",
            f"Installed Pi logger: {self.installed_logger_version}\n"
            f"Available Pi logger: {available}\n\n"
            "Update the Raspberry Pi now?\n\n"
            "Stop logging first. A secure SSH terminal will request the Pi password.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.start_logger_update(available)

    def start_logger_update(self, available: str) -> None:
        if self.pi_last_snapshot.get("logging"):
            QMessageBox.warning(self, "Pi logger update", "Stop the active logging session before updating the Pi.")
            return
        username = str(self.state_data.get("pi_ssh_user") or "vectornav")
        try:
            process = launch_pi_logger_update(self.pi_endpoint, username, STATE_DIR, available)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Pi logger update", f"Could not start the SSH updater:\n\n{exc}")
            return
        self.logger_update_active = True
        self.logger_button.setText("UPDATING LOGGER...")
        self.logger_button.setEnabled(False)
        worker = Worker(process.wait)
        worker.signals.result.connect(partial(self.logger_update_finished, available))
        worker.signals.error.connect(lambda error: self.logger_update_failed(available, error))
        self.start_worker(worker)

    def logger_update_finished(self, available: str, exit_code: object) -> None:
        if int(exit_code) != 0:
            self.logger_update_failed(available, "Review the SSH update terminal output.")
            return
        self.logger_button.setText("VERIFYING LOGGER...")
        worker = Worker(self.verify_logger_update, available)
        worker.signals.result.connect(self.logger_verify_finished)
        worker.signals.error.connect(lambda error: self.logger_update_failed(available, error))
        self.start_worker(worker)

    def verify_logger_update(self, available: str) -> tuple[bool, str, str]:
        deadline = time.monotonic() + 60.0
        observed = "unknown"
        while time.monotonic() < deadline:
            try:
                snapshot = fetch_pi_snapshot(self.pi_endpoint, timeout=3.0)
                observed = str(snapshot.get("logger_version") or "unknown")
                if not logger_update_available(observed, available):
                    return True, observed, available
            except Exception:
                pass
            time.sleep(2.0)
        return False, observed, available

    def logger_verify_finished(self, result: object) -> None:
        ok, observed, available = result  # type: ignore[misc]
        self.logger_update_active = False
        if ok:
            self.installed_logger_version = observed
            self.available_logger_version = ""
            self.logger_button.setText(f"LOGGER v{observed}")
            self.logger_button.setEnabled(True)
            QMessageBox.information(self, "Pi logger update", f"The Pi logger was updated successfully to v{observed}.")
            return
        self.logger_button.setText("CHECK LOGGER")
        self.logger_button.setEnabled(self.pi_connected)
        QMessageBox.critical(
            self,
            "Pi logger update",
            f"The installer finished, but logger v{available} could not be verified.\n"
            f"Last reported version: {observed}",
        )

    def logger_update_failed(self, available: str, error: str) -> None:
        self.logger_update_active = False
        self.logger_button.setText(f"UPDATE LOGGER v{available}")
        self.logger_button.setEnabled(self.pi_connected)
        QMessageBox.critical(self, "Pi logger update", f"The SSH update did not complete.\n\n{error}")

    def browse_custom_source(self) -> None:
        initial = self.custom_source_path.text() or self.analysis_input.text() or str(REPO_ROOT)
        selected = QFileDialog.getExistingDirectory(self, "Select telemetry folder", initial)
        if selected:
            self.custom_source_path.setText(selected)
            self.scan_custom_sources()

    def browse_custom_output(self) -> None:
        initial = self.custom_output.text() or self.analysis_output.text() or str(DEFAULT_OUTPUT_ROOT)
        selected = QFileDialog.getExistingDirectory(self, "Select workspace output folder", initial)
        if selected:
            self.custom_output.setText(selected)

    def set_custom_busy(self, busy: bool, status: str = "") -> None:
        self.custom_task_active = busy
        self.custom_scan_button.setEnabled(not busy)
        self.custom_preview_button.setEnabled(not busy)
        self.custom_save_button.setEnabled(not busy)
        if status:
            self.custom_status.setText(status)

    def restore_custom_workspace(self) -> None:
        source_path = self.custom_source_path.text().strip()
        if source_path and Path(source_path).expanduser().exists():
            self.scan_custom_sources()

    def scan_custom_sources(self) -> None:
        if self.custom_task_active:
            return
        source_path = self.custom_source_path.text().strip()
        if not source_path:
            QMessageBox.warning(self, "Custom workspace", "Select a telemetry folder.")
            return
        self.set_custom_busy(True, "Scanning channels...")
        worker = Worker(scan_workspace_sources, Path(source_path))
        worker.signals.result.connect(partial(self.custom_sources_scanned, source_path))
        worker.signals.error.connect(self.custom_task_failed)
        self.start_worker(worker)

    def custom_sources_scanned(self, source_path: str, result: object) -> None:
        self.set_custom_busy(False)
        if not isinstance(result, dict):
            self.custom_task_failed("The channel scan returned an invalid result.")
            return
        self.custom_scan_result = result
        sources = [source for source in result.get("sources", []) if isinstance(source, dict)]
        saved = self.state_data["custom_analysis"]
        available_paths = {str(source.get("path") or "") for source in sources}
        selected_paths = {str(path) for path in saved.get("source_paths", [])} & available_paths
        automatic_paths = {
            str(source.get("path") or "")
            for source in sources
            if "_VNINS" in str(source.get("name") or "").upper()
            or "MOTEC_CHANNELS" in str(source.get("name") or "").upper()
        }
        if not selected_paths:
            selected_paths = automatic_paths or {str(source.get("path") or "") for source in sources[:1]}
        self.custom_source_list.blockSignals(True)
        self.custom_source_list.clear()
        for source in sources:
            path = str(source.get("path") or "")
            channel_count = len(source.get("channels") or [])
            item = QListWidgetItem(
                f"{source.get('name', Path(path).name)}  |  {channel_count} channels  |  {str(source.get('format') or '').upper()}"
            )
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if path in selected_paths else Qt.CheckState.Unchecked)
            self.custom_source_list.addItem(item)
        self.custom_source_list.blockSignals(False)
        self.state_data["custom_analysis"]["source_path"] = str(Path(source_path).expanduser().resolve())
        warnings = len(result.get("warnings") or [])
        summary = f"{len(sources)} data files"
        if warnings:
            summary += f" | {warnings} skipped"
        self.custom_source_summary.setText(summary)
        self.custom_source_selection_changed()
        self.set_custom_busy(False, "Channels ready")
        self.save_state_safely()

    def custom_task_failed(self, error: str) -> None:
        self.set_custom_busy(False, "Workspace error")
        QMessageBox.critical(self, "Custom workspace", error)

    def checked_custom_source_paths(self) -> list[str]:
        return [
            str(item.data(Qt.ItemDataRole.UserRole) or "")
            for index in range(self.custom_source_list.count())
            if (item := self.custom_source_list.item(index)).checkState() == Qt.CheckState.Checked
        ]

    def set_all_custom_sources(self, selected: bool) -> None:
        self.custom_source_list.blockSignals(True)
        state = Qt.CheckState.Checked if selected else Qt.CheckState.Unchecked
        for index in range(self.custom_source_list.count()):
            self.custom_source_list.item(index).setCheckState(state)
        self.custom_source_list.blockSignals(False)
        self.custom_source_selection_changed()

    def available_custom_channels(self) -> dict[str, str]:
        selected = set(self.checked_custom_source_paths())
        available: dict[str, str] = {}
        for source in self.custom_scan_result.get("sources", []):
            if str(source.get("path") or "") not in selected:
                continue
            for channel in source.get("channels", []):
                name = str(channel.get("name") or "").strip()
                if name:
                    available.setdefault(name, str(channel.get("unit") or ""))
        for formula in self.custom_formula_rows(require_complete=False):
            name = str(formula.get("name") or "").strip()
            if name:
                available[name] = str(formula.get("unit") or "")
        return available

    def custom_source_selection_changed(self, *_args: Any) -> None:
        available = self.available_custom_channels()
        previous_x = str(self.custom_x_channel.currentData() or "")
        previous_y = set(self.checked_custom_y_channels())
        saved = self.state_data["custom_analysis"]
        if not previous_y:
            previous_y = {str(value) for value in saved.get("y_channels", [])}

        self.custom_x_channel.blockSignals(True)
        self.custom_x_channel.clear()
        for name, unit in sorted(available.items(), key=lambda item: item[0].lower()):
            self.custom_x_channel.addItem(f"{name} ({unit})" if unit else name, name)
        x_choice = previous_x if previous_x in available else str(saved.get("x_channel") or "")
        if x_choice not in available:
            x_choice = choose_default_x(list(available))
        x_index = self.custom_x_channel.findData(x_choice)
        self.custom_x_channel.setCurrentIndex(max(0, x_index))
        self.custom_x_channel.blockSignals(False)

        self.custom_y_channels.blockSignals(True)
        self.custom_y_channels.clear()
        for name, unit in sorted(available.items(), key=lambda item: item[0].lower()):
            item = QListWidgetItem(f"{name}  [{unit}]" if unit else name)
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if name in previous_y else Qt.CheckState.Unchecked)
            self.custom_y_channels.addItem(item)
        self.custom_y_channels.blockSignals(False)
        if not self.checked_custom_y_channels() and available:
            defaults = self.resolve_custom_channel_groups(
                (
                    ("speed_mph", "Speed_mph", "Vehicle_Speed_mph"),
                    ("lateral_g", "Lateral_G", "Common_Accel_Y_g"),
                    ("longitudinal_g", "Longitudinal_G", "Common_Accel_X_g"),
                )
            )
            self.set_custom_y_channels(defaults)
        self.filter_custom_channels(self.custom_channel_search.text())
        self.custom_y_selection_changed()

    def checked_custom_y_channels(self) -> list[str]:
        return [
            str(item.data(Qt.ItemDataRole.UserRole) or "")
            for index in range(self.custom_y_channels.count())
            if (item := self.custom_y_channels.item(index)).checkState() == Qt.CheckState.Checked
        ]

    def set_custom_y_channels(self, names: list[str]) -> None:
        selected = set(names)
        self.custom_y_channels.blockSignals(True)
        for index in range(self.custom_y_channels.count()):
            item = self.custom_y_channels.item(index)
            item.setCheckState(
                Qt.CheckState.Checked
                if str(item.data(Qt.ItemDataRole.UserRole) or "") in selected
                else Qt.CheckState.Unchecked
            )
        self.custom_y_channels.blockSignals(False)
        self.custom_y_selection_changed()

    def clear_custom_y_channels(self) -> None:
        self.set_custom_y_channels([])

    def custom_y_selection_changed(self, *_args: Any) -> None:
        selected = len(self.checked_custom_y_channels())
        total = self.custom_y_channels.count()
        self.custom_channel_summary.setText(f"{selected} selected | {total} available")

    def filter_custom_channels(self, search: str) -> None:
        needle = search.strip().lower()
        for index in range(self.custom_y_channels.count()):
            item = self.custom_y_channels.item(index)
            item.setHidden(bool(needle and needle not in item.text().lower()))

    def add_custom_formula(self, formula: dict[str, Any] | None = None) -> None:
        formula = formula or {}
        self.custom_formula_table.blockSignals(True)
        row = self.custom_formula_table.rowCount()
        self.custom_formula_table.insertRow(row)
        for column, key in enumerate(("name", "unit", "expression")):
            self.custom_formula_table.setItem(row, column, QTableWidgetItem(str(formula.get(key) or "")))
        self.custom_formula_table.blockSignals(False)
        if not formula:
            self.custom_formula_table.setCurrentCell(row, 0)
            self.custom_formula_table.editItem(self.custom_formula_table.item(row, 0))

    def remove_custom_formula(self) -> None:
        rows = sorted({index.row() for index in self.custom_formula_table.selectedIndexes()}, reverse=True)
        if not rows and self.custom_formula_table.currentRow() >= 0:
            rows = [self.custom_formula_table.currentRow()]
        self.custom_formula_table.blockSignals(True)
        for row in rows:
            self.custom_formula_table.removeRow(row)
        self.custom_formula_table.blockSignals(False)
        self.custom_source_selection_changed()

    def custom_formula_rows(self, require_complete: bool = True) -> list[dict[str, str]]:
        rows = []
        for row in range(self.custom_formula_table.rowCount()):
            values = [
                self.custom_formula_table.item(row, column).text().strip()
                if self.custom_formula_table.item(row, column)
                else ""
                for column in range(3)
            ]
            if not any(values):
                continue
            if require_complete and (not values[0] or not values[2]):
                raise ValueError(f"Calculated channel row {row + 1} needs a name and expression.")
            rows.append({"name": values[0], "unit": values[1], "expression": values[2]})
        return rows

    def custom_formula_changed(self, _row: int, column: int) -> None:
        if column in (0, 1):
            QTimer.singleShot(0, self.custom_source_selection_changed)

    def resolve_custom_channel_groups(self, groups: tuple[tuple[str, ...], ...]) -> list[str]:
        available = self.available_custom_channels()
        lowered = {name.lower(): name for name in available}
        resolved = []
        for candidates in groups:
            match = next((lowered[value.lower()] for value in candidates if value.lower() in lowered), None)
            if match:
                resolved.append(match)
        return resolved

    def current_custom_definition(self) -> dict[str, Any]:
        return {
            "title": self.custom_title.text().strip() or "Custom Analysis Workspace",
            "x_channel": str(self.custom_x_channel.currentData() or ""),
            "y_channels": self.checked_custom_y_channels(),
            "filter": self.custom_filter.text().strip(),
            "smoothing_points": self.custom_smoothing.value(),
            "max_plot_points": self.custom_max_points.value(),
            "plot_style": str(self.custom_plot_style.currentData() or "line"),
            "formulas": self.custom_formula_rows(),
        }

    def custom_analysis_payload(self) -> dict[str, Any]:
        payload = self.current_custom_definition()
        payload["source_paths"] = self.checked_custom_source_paths()
        if not payload["source_paths"]:
            raise ValueError("Select at least one telemetry file.")
        if not payload["x_channel"]:
            raise ValueError("Select an X-axis channel.")
        if not payload["y_channels"]:
            raise ValueError("Select at least one Y-axis channel.")
        return payload

    def persist_custom_definition(self, payload: dict[str, Any]) -> None:
        settings = self.state_data["custom_analysis"]
        settings.update(payload)
        settings["source_path"] = self.custom_source_path.text().strip()
        settings["output_root"] = self.custom_output.text().strip()
        self.save_state_safely()

    def start_custom_analysis(self, save_report: bool) -> None:
        if self.custom_task_active or self.analysis_process is not None:
            return
        try:
            payload = self.custom_analysis_payload()
            output_root_text = self.custom_output.text().strip()
            if save_report and not output_root_text:
                raise ValueError("Select an output folder.")
            if save_report:
                output_root = Path(output_root_text).expanduser()
                output_root.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now().strftime("custom_%Y%m%d_%H%M%S")
                output_dir = output_root / stamp
                suffix = 2
                while output_dir.exists():
                    output_dir = output_root / f"{stamp}_{suffix}"
                    suffix += 1
            else:
                output_dir = STATE_DIR / "workspace_preview"
            self.persist_custom_definition(payload)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Custom workspace", str(exc))
            return
        self.custom_save_requested = save_report
        self.set_custom_busy(True, "Building workspace...")
        worker = Worker(run_custom_analysis, payload, output_dir)
        worker.signals.result.connect(partial(self.custom_analysis_finished, save_report, output_dir, payload))
        worker.signals.error.connect(self.custom_task_failed)
        self.start_worker(worker)

    def custom_analysis_finished(
        self,
        save_report: bool,
        output_dir: Path,
        payload: dict[str, Any],
        result: object,
    ) -> None:
        self.set_custom_busy(False)
        if not isinstance(result, dict):
            self.custom_task_failed("The custom analysis returned an invalid result.")
            return
        report_path = Path(str(result.get("report_path") or ""))
        if report_path.is_file():
            self.custom_preview.setUrl(QUrl.fromLocalFile(str(report_path.resolve())))
        trace_count = int(result.get("trace_count") or 0)
        point_count = int(result.get("point_count") or 0)
        warnings = len(result.get("warnings") or [])
        status = f"{trace_count} traces | {point_count:,} points"
        if warnings:
            status += f" | {warnings} warnings"
        self.custom_status.setText(status)
        if not save_report:
            return
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        entry = {
            "id": uuid.uuid4().hex[:12],
            "status": "completed",
            "created_at": now,
            "completed_at": now,
            "input_path": self.custom_source_path.text().strip(),
            "output_dir": str(output_dir.resolve()),
            "mode": "custom",
            "title": payload.get("title"),
            "results": result_files(output_dir),
            "exit_code": 0,
        }
        self.state_data["history"] = [entry, *self.state_data.get("history", [])][:20]
        self.save_state_safely()
        self.refresh_history(entry["id"])
        self.show_page("results")

    def refresh_custom_presets(self, selection: str = "") -> None:
        self.custom_preset.clear()
        for name in BUILTIN_WORKSPACE_PRESETS:
            self.custom_preset.addItem(name, ("builtin", name))
        for name in sorted(self.state_data.get("custom_presets", {}), key=str.lower):
            self.custom_preset.addItem(name, ("user", name))
        if selection:
            for index in range(self.custom_preset.count()):
                data = self.custom_preset.itemData(index)
                if data and data[1] == selection:
                    self.custom_preset.setCurrentIndex(index)
                    break

    def load_custom_preset(self) -> None:
        data = self.custom_preset.currentData()
        if not data:
            return
        kind, name = data
        if kind == "user":
            definition = self.state_data.get("custom_presets", {}).get(name)
            if not isinstance(definition, dict):
                return
            self.apply_custom_definition(definition)
            self.custom_status.setText(f"Preset loaded: {name}")
            return
        preset = BUILTIN_WORKSPACE_PRESETS[name]
        x_channels = self.resolve_custom_channel_groups((tuple(preset.get("x") or ()),))
        y_channels = self.resolve_custom_channel_groups(tuple(preset.get("y") or ()))
        keywords = tuple(str(value).lower() for value in preset.get("keywords", ()))
        if keywords:
            y_channels.extend(
                channel
                for channel in self.available_custom_channels()
                if any(keyword in channel.lower() for keyword in keywords) and channel not in y_channels
            )
        if x_channels:
            index = self.custom_x_channel.findData(x_channels[0])
            self.custom_x_channel.setCurrentIndex(index)
        self.set_custom_y_channels(y_channels)
        style_index = self.custom_plot_style.findData(str(preset.get("style") or "line"))
        self.custom_plot_style.setCurrentIndex(max(0, style_index))
        if not y_channels:
            QMessageBox.warning(self, "Workspace preset", "None of this preset's channels are available in the selected files.")
        else:
            self.custom_status.setText(f"Preset loaded: {name}")

    def apply_custom_definition(self, definition: dict[str, Any]) -> None:
        self.custom_title.setText(str(definition.get("title") or "Custom Analysis Workspace"))
        self.custom_filter.setText(str(definition.get("filter") or ""))
        self.custom_smoothing.setValue(int(definition.get("smoothing_points", 1)))
        self.custom_max_points.setValue(int(definition.get("max_plot_points", 5000)))
        style_index = self.custom_plot_style.findData(str(definition.get("plot_style") or "line"))
        self.custom_plot_style.setCurrentIndex(max(0, style_index))
        self.custom_formula_table.blockSignals(True)
        self.custom_formula_table.setRowCount(0)
        self.custom_formula_table.blockSignals(False)
        for formula in definition.get("formulas", []):
            if isinstance(formula, dict):
                self.add_custom_formula(formula)
        self.custom_source_selection_changed()
        x_index = self.custom_x_channel.findData(str(definition.get("x_channel") or ""))
        if x_index >= 0:
            self.custom_x_channel.setCurrentIndex(x_index)
        self.set_custom_y_channels([str(value) for value in definition.get("y_channels", [])])

    def save_custom_preset(self) -> None:
        try:
            definition = self.current_custom_definition()
        except ValueError as exc:
            QMessageBox.critical(self, "Workspace preset", str(exc))
            return
        name, accepted = QInputDialog.getText(self, "Save workspace preset", "Preset name")
        name = name.strip()
        if not accepted or not name:
            return
        if name in BUILTIN_WORKSPACE_PRESETS:
            QMessageBox.warning(self, "Workspace preset", "Choose a different name than the built-in presets.")
            return
        self.state_data.setdefault("custom_presets", {})[name] = definition
        self.save_state_safely()
        self.refresh_custom_presets(name)
        self.custom_status.setText(f"Preset saved: {name}")

    def delete_custom_preset(self) -> None:
        data = self.custom_preset.currentData()
        if not data:
            return
        kind, name = data
        if kind != "user":
            QMessageBox.information(self, "Workspace preset", "Built-in presets cannot be deleted.")
            return
        answer = QMessageBox.question(self, "Delete preset", f"Delete '{name}'?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.state_data.get("custom_presets", {}).pop(name, None)
        self.save_state_safely()
        self.refresh_custom_presets()
        self.custom_status.setText("Preset deleted")

    def browse_analysis_input(self) -> None:
        initial = self.analysis_input.text() or str(REPO_ROOT)
        selected = QFileDialog.getExistingDirectory(self, "Select VN300 telemetry folder", initial)
        if selected:
            self.analysis_input.setText(selected)

    def browse_analysis_output(self) -> None:
        initial = self.analysis_output.text() or str(DEFAULT_OUTPUT_ROOT)
        selected = QFileDialog.getExistingDirectory(self, "Select analysis output folder", initial)
        if selected:
            self.analysis_output.setText(selected)

    def analysis_payload(self) -> dict[str, Any]:
        return {
            "input_path": self.analysis_input.text(),
            "output_root": self.analysis_output.text(),
            "mode": self.analysis_mode.currentText().lower(),
            "driver_order": self.analysis_driver_order.text(),
            "driver_order_offset": self.analysis_driver_offset.value(),
            "auto_sectors": self.analysis_sectors.value(),
            "sector_report_min_seconds": self.analysis_minimum.value(),
            "gg_enabled": self.analysis_gg.isChecked(),
            "include_ascii": self.analysis_ascii.isChecked(),
        }

    def start_analysis(self) -> None:
        if self.analysis_process is not None or self.custom_task_active:
            return
        payload = self.analysis_payload()
        output_root = Path(str(payload["output_root"])).expanduser()
        output_dir: Path | None = None
        try:
            if not str(payload["output_root"]).strip():
                raise ValueError("Select an output folder.")
            output_root.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("analysis_%Y%m%d_%H%M%S")
            output_dir = output_root / stamp
            suffix = 2
            while output_dir.exists():
                output_dir = output_root / f"{stamp}_{suffix}"
                suffix += 1
            output_dir.mkdir()
            command, validated = build_analysis_command(payload, output_dir)
        except (OSError, ValueError) as exc:
            if output_dir and output_dir.is_dir() and not any(output_dir.iterdir()):
                output_dir.rmdir()
            QMessageBox.critical(self, "Analysis setup", str(exc))
            return

        self.state_data["analysis"].update(validated)
        self.save_state_safely()
        self.analysis_job = {
            "id": uuid.uuid4().hex[:12],
            "status": "running",
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "completed_at": "",
            "input_path": validated["input_path"],
            "output_dir": str(output_dir.resolve()),
            "mode": validated["mode"],
            "results": [],
            "exit_code": None,
        }
        self.analysis_cancelled = False
        self.analysis_log.clear()
        self.analysis_log.append("Starting analyzer...")
        self.analysis_source_label.setText(validated["input_path"])
        self.analysis_progress.setRange(0, 0)
        self.analysis_status.set_status("Running", "working")
        self.run_analysis_button.setEnabled(False)
        self.cancel_analysis_button.setEnabled(True)

        process = QProcess(self)
        process.setWorkingDirectory(str(REPO_ROOT))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self.read_analysis_output)
        process.finished.connect(self.analysis_finished)
        process.errorOccurred.connect(self.analysis_process_error)
        process.setProgram(command[0])
        process.setArguments(command[1:])
        self.analysis_process = process
        process.start()

    def read_analysis_output(self) -> None:
        if not self.analysis_process:
            return
        text = bytes(self.analysis_process.readAllStandardOutput()).decode("utf-8", errors="replace")
        if text:
            self.analysis_log.moveCursor(QTextCursor.MoveOperation.End)
            self.analysis_log.insertPlainText(text)
            self.analysis_log.ensureCursorVisible()

    def cancel_analysis(self) -> None:
        if not self.analysis_process:
            return
        self.analysis_cancelled = True
        self.analysis_status.set_status("Cancelling", "working")
        self.analysis_process.terminate()
        QTimer.singleShot(3000, self.kill_analysis_if_running)

    def kill_analysis_if_running(self) -> None:
        if self.analysis_process and self.analysis_process.state() != QProcess.ProcessState.NotRunning:
            self.analysis_process.kill()

    def analysis_process_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            QMessageBox.critical(self, "Analyzer", "The bundled analyzer could not be started.")

    def analysis_finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
        self.analysis_process = None
        self.analysis_progress.setRange(0, 1)
        self.analysis_progress.setValue(1)
        self.run_analysis_button.setEnabled(True)
        self.cancel_analysis_button.setEnabled(False)
        if not self.analysis_job:
            return
        status = "cancelled" if self.analysis_cancelled else ("completed" if exit_code == 0 else "failed")
        self.analysis_job["status"] = status
        self.analysis_job["exit_code"] = exit_code
        self.analysis_job["completed_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        self.analysis_job["results"] = result_files(Path(self.analysis_job["output_dir"]))
        history = [
            item
            for item in self.state_data.get("history", [])
            if item.get("id") != self.analysis_job["id"]
        ]
        self.state_data["history"] = [self.analysis_job, *history][:20]
        self.save_state_safely()
        result_count = len(self.analysis_job["results"])
        if status == "completed" and result_count:
            self.analysis_status.set_status("Complete", "online")
            self.refresh_history(str(self.analysis_job["id"]))
            self.show_page("results")
        elif status == "completed":
            self.analysis_status.set_status("No results", "offline")
            QMessageBox.warning(self, "Analysis complete", "The analyzer finished but produced no report files.")
        else:
            self.analysis_status.set_status(status, "offline")

    def refresh_history(self, select_id: str = "") -> None:
        self.history_list.blockSignals(True)
        self.history_list.clear()
        selected_row = -1
        for row, entry in enumerate(self.state_data.get("history", [])):
            entry_id = str(entry.get("id") or "")
            source = Path(str(entry.get("input_path") or "")).name or entry_id
            completed = str(entry.get("completed_at") or entry.get("created_at") or "").replace("T", " ")[:16]
            status = str(entry.get("status") or "").upper()
            item = QListWidgetItem(f"{source}\n{completed}  {status}")
            item.setData(Qt.ItemDataRole.UserRole, entry_id)
            self.history_list.addItem(item)
            if entry_id == select_id:
                selected_row = row
        self.history_list.blockSignals(False)
        if selected_row >= 0:
            self.history_list.setCurrentRow(selected_row)
        elif self.history_list.count() and self.history_list.currentRow() < 0:
            self.history_list.setCurrentRow(0)

    def history_selected(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current is None:
            return
        entry_id = str(current.data(Qt.ItemDataRole.UserRole) or "")
        self.selected_history = next(
            (entry for entry in self.state_data.get("history", []) if str(entry.get("id")) == entry_id),
            None,
        )
        self.selected_result = None
        self.result_list.blockSignals(True)
        self.result_list.clear()
        first_html = -1
        first_any = -1
        if self.selected_history:
            for index, result in enumerate(self.selected_history.get("results", [])):
                kind = str(result.get("kind") or "").upper()
                item = QListWidgetItem(f"{result.get('label', result.get('name', ''))}    {kind}")
                item.setData(Qt.ItemDataRole.UserRole, index)
                self.result_list.addItem(item)
                if first_any < 0:
                    first_any = index
                if first_html < 0 and result.get("kind") == "html":
                    first_html = index
        self.result_list.blockSignals(False)
        selection = first_html if first_html >= 0 else first_any
        if selection >= 0:
            self.result_list.setCurrentRow(selection)
        else:
            self.result_preview.setCurrentIndex(0)

    def result_selected(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current is None or not self.selected_history:
            return
        index = int(current.data(Qt.ItemDataRole.UserRole))
        results = self.selected_history.get("results", [])
        if not (0 <= index < len(results)):
            return
        self.selected_result = results[index]
        path = Path(str(self.selected_history["output_dir"])) / self.selected_result["name"]
        if not path.is_file():
            self.result_preview.setCurrentIndex(0)
            return
        if self.selected_result.get("kind") == "html":
            self.web_view.setUrl(QUrl.fromLocalFile(str(path.resolve())))
            self.result_preview.setCurrentIndex(1)
        elif self.selected_result.get("kind") == "csv":
            self.preview_csv(path)
            self.result_preview.setCurrentIndex(2)
        else:
            self.result_preview.setCurrentIndex(0)

    def preview_csv(self, path: Path) -> None:
        try:
            with path.open(newline="", encoding="utf-8-sig", errors="replace") as handle:
                reader = csv.reader(handle)
                header = next(reader, [])
                rows = [row for _, row in zip(range(500), reader)]
        except OSError as exc:
            QMessageBox.critical(self, "Result", str(exc))
            return
        self.csv_table.clear()
        self.csv_table.setColumnCount(len(header))
        self.csv_table.setHorizontalHeaderLabels(header)
        self.csv_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column in range(len(header)):
                self.csv_table.setItem(
                    row_index,
                    column,
                    QTableWidgetItem(row[column] if column < len(row) else ""),
                )
        self.csv_table.resizeColumnsToContents()

    def selected_result_path(self) -> Path | None:
        if not self.selected_history or not self.selected_result:
            return None
        return Path(str(self.selected_history["output_dir"])) / self.selected_result["name"]

    def open_result_file(self) -> None:
        path = self.selected_result_path()
        if not path or not path.is_file():
            QMessageBox.warning(self, "Result", "Select an available result file.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))

    def open_result_folder(self) -> None:
        if not self.selected_history:
            QMessageBox.warning(self, "Result", "Select an analysis first.")
            return
        path = Path(str(self.selected_history["output_dir"]))
        if not path.is_dir():
            QMessageBox.warning(self, "Result", "The selected output folder no longer exists.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))

    def check_app_update(self, manual: bool = True) -> None:
        if self.update_check_active:
            return
        self.update_check_active = True
        self.app_update_button.setText("CHECKING...")
        self.app_update_button.setEnabled(False)
        worker = Worker(fetch_remote_version, REPO_ROOT)
        worker.signals.result.connect(partial(self.handle_app_update_check, manual))
        worker.signals.error.connect(partial(self.handle_app_update_error, manual))
        self.start_worker(worker)

    def handle_app_update_check(self, manual: bool, remote: object) -> None:
        self.update_check_active = False
        version = str(remote)
        if update_available(CURRENT_VERSION, version):
            self.available_app_version = version
            self.app_update_button.setText(f"INSTALL v{version}")
            self.app_update_button.setProperty("role", "primary")
        else:
            self.available_app_version = ""
            self.app_update_button.setText(f"v{CURRENT_VERSION}  UP TO DATE")
            self.app_update_button.setProperty("role", "")
            if manual:
                QMessageBox.information(self, "Software update", f"{PRODUCT_NAME} v{CURRENT_VERSION} is up to date.")
        self.app_update_button.setEnabled(True)
        self.app_update_button.style().unpolish(self.app_update_button)
        self.app_update_button.style().polish(self.app_update_button)

    def handle_app_update_error(self, manual: bool, error: str) -> None:
        self.update_check_active = False
        self.app_update_button.setText(f"v{CURRENT_VERSION}  CHECK UPDATES")
        self.app_update_button.setEnabled(True)
        if manual:
            QMessageBox.critical(self, "Software update", f"Could not check GitHub for updates:\n\n{error}")

    def app_update_action(self) -> None:
        if self.available_app_version:
            self.install_app_update()
        else:
            self.check_app_update(True)

    def install_app_update(self) -> None:
        if self.analysis_process:
            QMessageBox.warning(self, "Software update", "Wait for the running analysis to finish before updating.")
            return
        version = self.available_app_version
        if not version:
            self.check_app_update(True)
            return
        answer = QMessageBox.question(
            self,
            "Install software update",
            f"Install {PRODUCT_NAME} v{version} now?\n\nThe application will close and restart.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.app_update_button.setText("PREPARING UPDATE...")
        self.app_update_button.setEnabled(False)
        if not IS_FROZEN and has_git_checkout(REPO_ROOT):
            self.launch_update_and_close({"mode": "git"})
            return
        function = stage_release_installer if IS_FROZEN else stage_branch_archive
        worker = Worker(function, STATE_DIR, version)
        worker.signals.result.connect(self.launch_update_and_close)
        worker.signals.error.connect(self.app_update_stage_failed)
        self.start_worker(worker)

    def app_update_stage_failed(self, error: str) -> None:
        self.app_update_button.setText(f"INSTALL v{self.available_app_version}")
        self.app_update_button.setEnabled(True)
        QMessageBox.critical(self, "Software update", f"Could not prepare the update:\n\n{error}")

    def launch_update_and_close(self, staged: object) -> None:
        if not isinstance(staged, dict):
            self.app_update_stage_failed("The update staging response was invalid.")
            return
        self.persist_window_state()
        try:
            source_root = Path(staged["source_root"]) if staged.get("source_root") else None
            installer_path = Path(staged["installer_path"]) if staged.get("installer_path") else None
            launch_update_helper(
                REPO_ROOT,
                STATE_DIR,
                source_root,
                update_wait_pid(),
                UPDATE_BRANCH,
                installer_path=installer_path,
            )
        except OSError as exc:
            self.app_update_stage_failed(str(exc))
            return
        QApplication.quit()

    def show_last_update_status(self) -> None:
        status = read_last_update_status()
        if not status:
            return
        message = str(status.get("message") or "Software update completed.")
        if status.get("ok"):
            QMessageBox.information(self, "Software update", message)
        else:
            QMessageBox.critical(self, "Software update", message)

    def persist_window_state(self) -> None:
        position = self.pos()
        self.state_data["geometry"] = f"{self.width()}x{self.height()}+{position.x()}+{position.y()}"
        self.save_state_safely()

    def closeEvent(self, event: Any) -> None:
        if self.analysis_process:
            answer = QMessageBox.question(
                self,
                "Analysis running",
                "An analysis is still running. Cancel it and close the application?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.analysis_process.kill()
        self.persist_window_state()
        event.accept()


def main() -> int:
    if os.name == "nt":
        os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    application = QApplication(sys.argv)
    application.setApplicationName(PRODUCT_NAME)
    application.setOrganizationName("SRT26")
    application.setStyle("Fusion")
    window = VN300QtApp()
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
