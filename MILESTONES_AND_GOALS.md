# Sooner Racing Telemetry: Milestones and Goals

## Project objective

Provide a practical telemetry workflow for Sooner Racing: capture VN-300 data
on a Raspberry Pi, configure a drive day from Windows, review data offline, and
extend the workflow with passively captured vehicle CAN data when the hardware
and signal definitions are ready.

## Release state

- The latest public desktop release is `v0.15.3` (`desktop-v0.15.3` on GitHub Releases). Its published installer and checksum assets are for Windows; the Raspberry Pi Trixie offline ZIP is built locally as described in `OFFLINE_INSTALL_TRIXIE.md`.
- Desktop `v0.15.3` contains the dashboard log download route UX improvements and Raspberry Pi installer fixes, alongside earlier packaged-startup, logger, and CAN work. `APP_VERSION`, release notes, and packaging code identify the release contents.
- The documented embedded Pi logger remains `v0.6.1`. No separate Pi release
  or vehicle validation is implied by the desktop `v0.15.3` work.
- The entries below distinguish code and automated-test evidence from work that
  still requires a Pi, CAN adapter, or vehicle.

## Completed milestones

### Windows application and distribution

- [x] Native Windows desktop application for live Pi monitoring, drive-day
  setup, offline analysis, saved state, and in-app desktop updates.
- [x] Versioned PyInstaller/Inno Setup packaging, release workflow, checksum
  validation, update recovery diagnostics, and compatibility launchers.
- [x] `v0.15.2` packaged-startup repair: the build script limits DLL discovery
  to Python and Windows system paths and performs a frozen-app startup check
  before generating an installer.

### Pi logger and capture reliability

- [x] Button-operated Pi logger records raw VN-300 data, decoded CSV output,
  run metadata, dashboard state, and session metadata.
- [x] Capture pipeline uses a dedicated serial reader, bounded queue, buffered
  writes, final flush, binary CRC checks, and stream resynchronization.
- [x] Dashboard and session metadata expose capture health, including rate,
  sensor-time gaps, missing-sample estimate, CRC errors, and queue pressure.
- [x] Existing tests exercise valid/corrupt binary packets, resynchronization,
  delayed-consumer capture, health counters, and the session pipeline.

### Offline Pi updates and setup stream

- [x] The Windows application embeds the Pi payload and updates a connected Pi
  over local-network `scp`/SSH without a GitHub download on the Pi.
- [x] Update flow retains dependency preflight, timestamped backup, CAN-map
  preservation, service/API checks, rollback, and installed-version checking.
- [x] Non-recording setup stream supplies live telemetry for gate positioning
  without creating a run, metadata record, CAN log, or timing update.
- [x] Desktop and Pi dashboard controls reject setup streaming while recording.

### Analysis and custom workspace

- [x] Quick Report creates lap/autocross summaries, quality output, overlays,
  HTML reports, sector outputs, and optional G-G based analysis from VN-300
  CSV data.
- [x] Custom Workspace scans wide CSV and long-form CAN-channel CSV data;
  supports source selection, channel search, calculated channels, filters,
  statistics, line/scatter views, presets, CSV export, and saved JSON setup.
- [x] Analysis tests cover custom-workspace formulas and sources, lap analysis,
  and G-G lap-predictor behavior using repository fixtures.

### CAN profile and DBC preparation

- [x] Optional CAN capture scaffold writes raw frames and decoded channel CSVs
  while keeping normal VN-300 logging available if CAN setup fails.
- [x] JSON CAN profiles capture adapter connection settings and choose raw,
  CSV-map, or DBC decoding; DBC decoding supports multiplexed signals.
- [x] Pi package includes `python-can`, `cantools`, and `pyusb` dependencies,
  profile/template files, CAN setup documentation, and automated profile,
  DBC, virtual-CAN, and packaging coverage documented for `v0.15.1`.
- [x] CAN remains opt-in; the service does not enable it by default. The selected
  Kvaser Leaf Light HS v2 vehicle integration has a confirmed **1,000,000 bit/s**
  bus bitrate and Motorola byte order for signal-map `byte_order` values.
- [ ] Vehicle-specific message IDs, Motorola start-bit translations, signedness,
  scaling, and live-capture decoding remain unconfirmed.

## Software validation completed

- Automated tests and packaging checks are evidence for the software behavior
  described above. `v0.15.1` release notes record 34 checks covering logger,
  CAN-profile/DBC/virtual-CAN, Windows/Pi packaging, and installer shell syntax.
- The `v0.15.2` installer build completed its frozen-app startup check and the installed application was observed running responsively on the development laptop. This is software validation on one Windows environment, not broad deployment or hardware validation.

## Hardware validation not yet performed

- Pi validation is still required on the target Pi, with the actual VN-300,
  GPIO buttons, storage destination, service restart, and offline update path.
- CAN validation is still required with the selected adapter, its driver,
  configured 1,000,000 bit/s `can0` interface, listen-only configuration, and
  known ECU/dash signals. Live capture must validate Motorola within-byte bit
  numbering, signed values, and scaling.
- Vehicle validation is still required before relying on timing gates, decoded
  CAN values, steering measurements, or analysis conclusions for setup changes.

## Remaining goals

### Near-term software goals

#### Publish and smoke-test `v0.15.2`

Acceptance criteria:

- A versioned installer and checksum are built from the intended revision.
- The installed application starts on a clean Windows test environment.
- The update path from the published release and the normal launch path work.
- Release notes and README links identify the published artifact accurately.

#### Synchronized VN-300 and CAN sessions

Acceptance criteria:

- Related VN-300 and CAN files are grouped by a recorded run/session identity.
- The analyzer aligns channels on an explicit common clock and reports offset,
  gaps, sampling method, and synchronization quality.
- Exports retain raw samples and state the resampling/interpolation method.
- Tests cover mismatched sample rates, missing data, and known clock offsets.

#### Channel definitions and calibration

Acceptance criteria:

- A versioned channel registry supplies canonical names, units, ranges, source
  aliases, calibration version, and vehicle configuration for reports.
- The importer flags missing, stale, clipped, implausible, and mis-scaled data.
- Saved reports record their channel and calibration assumptions.

### Hardware validation goals

#### Validate Pi capture on representative hardware

Acceptance criteria:

- A complete recorded run is made with the target VN-300, Pi, serial settings,
  buttons, and intended storage destination.
- Session metadata and CSV outputs show expected capture-health fields and the
  run can be reopened by Quick Report.
- Stop, reboot/service restart, storage fallback, and offline desktop-to-Pi
  update recovery are checked on the target setup.

#### Commission passive CAN capture

Acceptance criteria:

- The adapter, driver, Pi interface, bitrate, wiring, and listen-only mode are
  documented for the actual vehicle bus.
- Raw frames are observed on the real Pi without transmitting to the vehicle.
- A confirmed DBC or signal map decodes known values with correct ID, unit,
  scaling, byte order, and multiplexing where applicable.
- A recorded session preserves raw and decoded CAN files and reports CAN health.

#### Validate timing and vehicle signals

Acceptance criteria:

- Start/finish or autocross gates are checked against a known course procedure.
- Steering hardware, if added, has documented center/lock calibration and
  repeatable signal values.
- Any throttle, brake, wheel-speed, RPM, or gear signal is compared with a
  trusted vehicle source before it is used in engineering conclusions.

### Analysis and modeling goals

#### Build repeatable vehicle-dynamics reports

Acceptance criteria:

- Standard reports cover the available validated inputs for braking,
  acceleration, driver consistency, grip, and balance.
- Each report declares required channels, units, data-quality checks, and known
  limitations instead of silently substituting unavailable signals.
- Reopening the same raw logs and saved setup reproduces the report outputs.

#### Extend the custom workspace

Acceptance criteria:

- Linked plots, unit-aware axes, distance normalization, event bookmarks, and
  report annotations are saved and restored with a workspace.
- Exported images/data and report packages preserve source, formulas,
  calibration, and synchronization context.
- Tests cover workspace persistence and invalid/missing channel handling.

#### Establish data-supported modeling practice

Acceptance criteria:

- G-G, lap-time, tire, aero, and handling models state their input data,
  filtering, assumptions, and data-quality limits.
- Model outputs are compared with repeatable measured runs before they inform a
  vehicle decision.
- Reports label estimates and predictions separately from measured results.

### Release and operations goals

#### Maintain a repeatable release process

Acceptance criteria:

- Release checklist covers version changes, tests, installer build, checksum,
  release assets, update verification, and rollback/recovery information.
- Pi payload version, desktop version, and compatibility notes are consistent
  across the README, install guide, and version history.
- A published release is verified from its release asset, not only from source.

#### Make drive-day operation reproducible

Acceptance criteria:

- Operators can follow one documented process for setup, recording, copying
  data, analysis, issue capture, and shutdown.
- Run metadata records the context needed to compare valid runs.
- A post-drive review identifies missing data, capture warnings, and files that
  need retest before engineering conclusions are made.

## Evidence sources

- Release and status: `APP_VERSION`, `VERSION_HISTORY.md`, `README.md`, and
  the `321cbc1` baseline commit.
- Windows/packaging: `packaging/build_windows_installer.ps1` and `app` tests.
- Pi/capture/CAN: `pi/vn300_button_logger.py`, `pi/CAN_SETUP.md`, and `pi` tests.
- Analysis roadmap: `CUSTOM_ANALYSIS_WORKSPACE_ROADMAP.md`, `PHASE_1_VEHICLE_DYNAMICS_DESIGN_TOOL_PLAN.md`, and `PHASE_2_PLAN.md`.
