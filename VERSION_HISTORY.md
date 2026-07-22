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

`v0.5.0`

Stable Pi install tag:

```text
v0.4.0
```

Use the `v0.4.0` Git tag when the team needs the last pre-Phase-2 logger/dashboard.

## v0.5.0 - 2026-07-21

### Native Desktop App

- Added a native Windows interface for offline analysis, live Pi telemetry, timing traces, lap history, and CSV result previews.
- Added first-connect Pi IP/hostname prompting and saved automatic reconnection.
- Added the console-free `Start_VN300_Team_Tools.bat` launcher.
- Added the versioned `desktop-app` GitHub update channel.
- Added automatic and manual update checks using `APP_VERSION`.
- Clean Git clones update with a fast-forward from `origin/desktop-app`.
- Download ZIP installations validate and apply the latest branch archive without requiring Git.
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
