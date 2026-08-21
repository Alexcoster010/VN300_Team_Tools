# Version History

Use this file to record every meaningful change to the VN300 team tools. Update it whenever logger behavior, dashboard behavior, analyzer outputs, install steps, or file formats change.

## Versioning Format

Use:

```text
vMAJOR.MINOR.PATCH
```

- `MAJOR`: breaking workflow or file-format changes.
- `MINOR`: new features that keep existing workflows mostly compatible.
- `PATCH`: bug fixes, documentation updates, or small improvements.

## Current Version

`v0.11.0`

Stable Pi install tag:

```text
v0.4.0
```

Use the `v0.4.0` Git tag when the team needs the last pre-Phase-2 logger/dashboard.

## v0.11.0 - 2026-08-21

### Sooner Racing Telemetry Rebrand

- Renamed the Windows product from **VN300 Team Tools** to **Sooner Racing Telemetry**.
- Added a deterministic SRT logo with white letters, a red outline, and a black background for the app sidebar, Windows icon, installer, and shortcuts.
- Renamed the primary release asset to `Sooner-Racing-Telemetry-Setup-X.Y.Z.exe`.
- Kept the existing installer App ID and executable name so upgrades replace the current installation cleanly.
- Added automatic legacy state loading so saved Pi addresses, analysis settings, custom presets, geometry, report history, and optional browser-interface settings remain available.
- Published a legacy installer filename alias so v0.10.0 and older desktop updaters can install the renamed release.
- Updated product metadata, update messages, launchers, install paths, documentation, and packaging tests.

## v0.10.0 - 2026-08-21

### Custom Analysis Workspace

- Preserved the existing analyzer as a **Quick Report** tab and added a separate **Custom Workspace**.
- Added telemetry-folder scanning, selectable source files, searchable numeric channels, selectable X/Y axes, and multi-run overlays.
- Added safe user-defined calculated channels with bracketed channel references and math, smoothing, hold, derivative, integral, clipping, and conditional functions.
- Added user filters, line/scatter modes, configurable smoothing and plot detail, per-trace statistics, and offline interactive zoom/pan plots.
- Added five built-in analysis presets plus named user presets stored with the desktop settings.
- Added automatic `Vehicle_Speed_mps`, `Vehicle_Speed_mph`, `Distance_m`, `Yaw_Rate_dps`, and G channels when the necessary raw VN300 fields are present.
- Added direct discovery of the Pi logger's long-form decoded MoTeC CAN channel file.
- Added saved report integration with a full-resolution tidy CSV export and reusable JSON workspace configuration.
- Added formula-safety, mixed-source, raw-channel derivation, CAN discovery, and report-generation tests.

## v0.9.0 - 2026-08-20

### Drive Day Setup

- Added **Live Telemetry** and **Drive Day Setup** tabs inside the desktop dashboard workspace.
- Added all 31 editable Pi run-metadata fields, including driver, course, tire pressures, temperatures, alignment, ride heights, dampers, anti-roll bars, brake bias, aero, battery/fuel state, validity, and notes.
- Added lap and autocross timing setup with two-point start/finish gates, minimum speed, and minimum crossing gap.
- Added asynchronous metadata save, timing save, and timing reset actions through the existing Pi API.
- Preserved unsaved operator edits while live telemetry refreshes and disabled setup writes while the Pi is offline.
- Added API write and metadata-schema regression tests plus responsive minimum-window layout verification.

## v0.8.2 - 2026-08-20

### Desktop Background Task Reliability

- Fixed the Qt desktop app dropping background workers before their result or error signals could reach the interface.
- Fixed the live dashboard remaining on `CONNECTING` even when the Pi dashboard API was reachable.
- Retained background workers for Pi polling, logger checks, software update checks, and update staging until completion.
- Started a Pi health request immediately when the operator presses **Connect**.
- Added a regression test for queued Qt worker result delivery.

## v0.8.1 - 2026-08-20

### Desktop App Pi Connection Fix

- Fixed desktop app Pi health checks using Windows/Python proxy settings instead of connecting directly to the local Pi dashboard.
- Added default `:8080` normalization to the browser-based team app path when a user enters only a Pi hostname or IP address.
- Added frontend request timeouts so the app reports a connection failure instead of staying on `Checking...`.

## v0.8.0 - 2026-07-22

### Trackside Desktop Interface

- Rebuilt the Windows application in PySide6 with a professional, scalable desktop layout.
- Added a trackside-focused live dashboard with dense telemetry metrics, lap timing, GPS and speed traces, lap history, logger status, system health, and connection latency.
- Added a dedicated analysis workspace with validated controls, progress output, cancellation, and automatic report handoff.
- Added an embedded report archive that renders HTML reports inside the app and previews CSV results.
- Preserved saved Pi connections, one-password logger updates, app updates, analysis history, and existing Pi API compatibility.
- Updated the source launcher and Windows installer build to ship the new Qt application.

## v0.7.1 - 2026-07-22

### Multi-Boot Analysis Graph Fix

- Fixed top-level `vn300_logs` analysis selecting an older timing configuration because backup file timestamps changed.
- Timing configurations now use their recorded `Configured_At_Local` time and reject zero-length GPS lines.
- Added telemetry sanitation before distance, lap-crossing, G-G, and graph calculations so corrupt decoder rows cannot flatten plot axes.
- Merged run metadata from every selected boot folder so driver names remain attached to the correct sessions.
- Added regression tests for copied timing files, invalid timing lines, corrupt telemetry samples, and multi-boot metadata.

## v0.7.0 - 2026-07-22

### Pi Logger Updates From The Desktop App

- Added automatic Pi logger version checks after a successful dashboard connection.
- Added an update prompt when `/api/latest` reports an older or unversioned logger.
- Added a Pi dashboard control for manual logger update checks.
- Added a visible Windows OpenSSH update terminal so passwords are never captured or stored by the app.
- Reused the supported Pi ZIP installer with backups, CAN-map preservation, health validation, and rollback.
- Added post-update API polling that verifies the installed logger version before reporting success.
- Blocked logger installation while a logging session is active.
- Added SSH command, version-check, input-validation, and launcher tests.

## v0.6.0 - 2026-07-21

### Windows Installer

- Added a per-user Windows installer that bundles Python and requires no separate Python or Git installation.
- Added Windows Search and Start Menu registration, optional desktop shortcut creation, and Installed Apps uninstall support.
- Packaged the lap analyzer and detached update helper as dedicated executables.
- Moved the installed app's default analysis output to `Documents\VN300 Team Tools\Analysis`.
- Added versioned GitHub release updates with SHA-256 verification and automatic restart.
- Added PyInstaller and Inno Setup build definitions plus an automated GitHub release workflow.
- Added a dedicated VN300 Team Tools Windows icon and packaging regression tests.

## v0.5.2 - 2026-07-21

### Update Reliability

- Switched public version checks to GitHub's Contents API so a newly pushed `APP_VERSION` is visible immediately instead of waiting for raw-file CDN cache expiration.

## v0.5.1 - 2026-07-21

### Public Desktop Distribution

- Published the repository for anonymous branch and ZIP downloads.
- Enabled automatic update checks and validated branch-archive updates for Download ZIP installations without Git.
- Kept clean fast-forward updates for Git clones.
- Updated the teammate installation guide to recommend the public `desktop-app` ZIP.

## v0.5.0 - 2026-07-21

### Native Desktop App

- Added a native Windows interface for offline analysis, live Pi telemetry, timing traces, lap history, and CSV result previews.
- Added first-connect Pi IP/hostname prompting and saved automatic reconnection.
- Added the console-free `Start_VN300_Team_Tools.bat` launcher.
- Added the versioned `desktop-app` GitHub update channel.
- Added automatic and manual update checks using `APP_VERSION`.
- Git clones update with a fast-forward from `origin/desktop-app`.
- Download ZIP installations validate and apply the latest public branch archive without requiring Git.
- Added detached update application, automatic restart, update status reporting, and backups for replaced ZIP-install files.
- Added `DESKTOP_APP_INSTALL.md` for teammate installation and future updates.

## v0.4.5 - 2026-07-20

### Logger

- Added VN-300 UTC date handling for binary logs.
- Run files now start with a provisional Pi-clock name, then after the session closes they are renamed to the first valid VN-300 UTC date when available.
- Session metadata now records Pi clock timestamps, VN-300 UTC start/end timestamps, and the date source used for the final run ID.
- This prevents offline/wrong Pi clock dates from permanently misdating run files.
- Added dashboard log-destination health so the team can see whether logging is going to the flash drive or Pi local fallback.
- Added `VN300_logger_status.json` heartbeat/status output in the boot log folder.
- Added write/flush error detection for raw, ASCII CSV, and binary CSV outputs so failed flash-drive writes stop the session with a visible warning.
- Added visible save icons and saved/error/unsaved status messages for Run Metadata and Timing Setup.
- Timing completed laps, best lap, current trace, and lap count now reset when a new logging run starts.
- Added Pi CPU temperature to the live dashboard.

### Phase 2 Development

- Started optional passive MoTeC/dash CAN logging in the Pi logger.
- Added disabled-by-default command-line options:
  - `--can-enable`
  - `--can-interface`
  - `--can-channel`
  - `--can-bitrate`
  - `--can-signal-map`
- Added raw CAN frame output:

  ```text
  VN300_YYYY-MM-DD_RUN001_MOTEC_RAW_CAN.csv
  ```

- Added decoded CAN channel output from a CSV signal map:

  ```text
  VN300_YYYY-MM-DD_RUN001_MOTEC_CHANNELS.csv
  ```

- Added dashboard CAN health tile showing CAN status, raw frame count, and decoded frame count.
- Added `pi/motec_can_signal_map.csv` as the Pi-side decode map.
- Added `motec_can_signal_map_template.csv` as a team-fillable signal-map example.
- Added `python-can` to Pi requirements.
- Updated the Pi installer to copy the CAN signal map and include the `netdev` group.

### Compatibility

- CAN logging is off unless `--can-enable` is passed.
- The default systemd service still starts the logger in VN300-only mode.

### Analyzer

- Added `analysis/vn300_gg_analysis.py` for corrected per-driver G-G diagrams.
- The G-G analyzer plots lateral G on the X axis and longitudinal G on the Y axis with equal scaling.
- It filters GPS uncertainty above `4.0 m`, speed outside the selected range, and obvious acceleration spikes.
- It writes `gg_diagrams_by_driver.html`, `gg_summary_by_driver.csv`, `gg_envelope_by_driver.csv`, and per-driver G-G point CSVs.
- Added directional percentile envelopes so the outer usable G-G shape is shown without assuming a perfect traction circle.
- Added driver override options for bad metadata: `--driver-map`, `--driver-order`, and `--driver-order-offset`.
- Corrected the existing `report.html` G-G plot axis order in `vn300_lap_analysis.py`.
- Added normal lap/run sector report outputs to `vn300_lap_analysis.py`:
  - `lap_times_sector_splits.html`
  - `lap_sector_splits.csv`
  - `theoretical_best_by_driver.csv`
  - `overall_best_sectors.csv`
- The sector report lists all timed laps/runs, highlights each overall best sector in purple, and computes theoretical best laps by driver.
- Added `--sector-report-min-seconds` to keep obvious false short timing splits out of the sector report.
- Added the same `--driver-map`, `--driver-order`, and `--driver-order-offset` overrides to the lap/run analyzer.

## v0.4.0 - 2026-07-15

### Dashboard

- Added live GPS track plot for active timing runs.
- Shows current lap/run path and current car position.
- Shows best completed valid lap/run path once available.
- Shows a best-lap comparison dot at the same elapsed time as the current lap/run.
- Exposes bounded current/best timing traces through `/api/latest`.

## v0.3.0 - 2026-07-15

### Analyzer

- Added automatic sector splitting for timed laps/runs.
- Automatic sectors default to three sectors per lap/run.
- Sector split points target even elapsed-time sections, then move to nearby low-cornering points.
- Added `sector_summary.csv` output when automatic sectors are generated.
- Added distance-based delta-to-fastest plot in `report.html`.

### Documentation

- Added `PI_INSTALL_CURRENT_VERSION.md`.
- Added `PHASE_2_PLAN.md`.
- Updated Phase 1 plan checkboxes/status.
- Updated `README.md` with automatic sector and delta-comparison notes.

## v0.2.1 - 2026-07-15

### Documentation

- Added `MOTEC_CAN_TO_PI_INTEGRATION_REPORT.md`.
- Linked the MoTeC CAN report from `README.md`.
- No logger, dashboard, analyzer, or installer behavior changed.

## v0.2.0 - 2026-07-15

### Logger And Dashboard

- Added dashboard Run Metadata panel.
- Added `GET /api/run_metadata` and `POST /api/run_metadata`.
- Changed session filenames from time-of-day naming to date/run-number naming:

  ```text
  VN300_YYYY-MM-DD_RUN001_BINARY.csv
  VN300_YYYY-MM-DD_RUN001_ttyUSB0.bin
  VN300_YYYY-MM-DD_RUN001_session_metadata.json
  ```

- First run of each day starts at `RUN001`.
- Same-day Pi reboot continues with the next unused run number by scanning `VN300_LOGS`.
- Session metadata JSON now includes run metadata.
- Logger writes `VN300_run_metadata.csv` in the boot folder.

### Analyzer

- Added optional `--metadata` CSV import.
- Added automatic loading of `VN300_run_metadata.csv` from Pi boot folders.
- Added `metadata_template.csv`.
- Added `data_quality.csv`.
- Added `report.html`.
- Added G-G/grip metrics to `summary.csv`.
- Added derived yaw-rate and curvature estimates.
- Added longitudinal/lateral/vertical G, yaw rate, and curvature fields to lap/run CSV exports.
- Added timestamp repair for older ASCII VNINS logs with repeated Pi logger timestamps.

### Documentation

- Added `DATA_ANALYZER_IMPROVEMENT_REPORT.md`.
- Added `PHASE_1_VEHICLE_DYNAMICS_DESIGN_TOOL_PLAN.md`.
- Added `INSTALLER_UPDATE_CHECKLIST.md`.
- Updated `README.md` for generic paths, run-number filenames, run metadata, and analyzer metadata loading.
- Updated `ONE_PAGE_SETUP_GUIDE.md` for the run metadata workflow.

### Verification

- Ran syntax checks on:
  - `pi/vn300_button_logger.py`
  - `analysis/vn300_lap_analysis.py`
- Ran analyzer smoke tests against existing VN300 logs.

## v0.1.0 - 2026-07-14

### Initial Team Package

- Packaged Raspberry Pi VN300 button logger.
- Added live dashboard on port `8080`.
- Added GPIO log button and power-hold shutdown support.
- Added flash-drive logging under `VN300_LOGS`.
- Added boot folders under `VN300_BOOT_YYYY-MM-DD_HH-MM-SS`.
- Added raw `.bin` capture.
- Added ASCII CSV parsing for `$VN...` messages.
- Added binary CSV parsing for the saved VN-300 binary message configuration.
- Added live timing setup for lap and autocross modes.
- Added offline analyzer for summaries, lap/run splitting, live delta export, and overlay HTML.
- Added Pi installer and systemd service.
