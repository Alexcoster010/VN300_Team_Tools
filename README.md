# SRT Telemetry

Raspberry Pi data collection and a browser web panel for VN300, Arduino steering/airspeed, Pi I²C SFM3505 mass flow, and optional CAN input. Saved-session analysis and vehicle models use the same project.

## Layout

```text
app.py               Entry point, HTTP API, and web file serving
logger.py            Sensors, recording, storage, GPIO, and live timing
analysis.py          Lap analysis, G-G plots, lap prediction, and tire models
web/                 Six-page panel, shared CSS/JS, local team assets, report theme
config/              Settings, version, CAN map, and calibration examples
deploy/              Pi installer, service template, shutdown permissions
arduino/             Arduino A0/A1 analog firmware
mass_flow.py         Pi I²C SFM3505 acquisition and capture
docs/                Current hardware setup guides
tests/               Recording, API, sensor, analysis, and dashboard tests
archive/             Historical docs, reports, captures, and generated work
requirements.txt     Python dependencies
```

The web panel uses plain HTML, CSS, and JavaScript with shared navigation and direct page URLs. Recording APIs and log formats remain compatible. Historical files are retained in `archive/`; their instructions may refer to former paths.

## Web panel

Open `http://<pi-ip>:8080/`. `/` opens Track. Each page supports direct navigation and refresh:

| URL | Functions |
| --- | --- |
| `/track` | Speed / G, Start/Stop Log, setup streaming, capture/CRC/queue health, storage, driver metadata, lap/autocross timing and traces |
| `/sensors` | All received VN300 channels, steering pot raw / voltage / angle, airspeed raw / voltage / estimate, SFM3505 air/O2 flow in slm, latest raw CAN frames and decoded signals with units / ages |
| `/sessions` | Search/filter recorded and imported sessions, metadata/files, individual files/session ZIPs/all logs, saved-log and model-input imports |
| `/analysis` | Select multiple sessions; lap splitting, sectors, overlays, driver/session comparisons, measured G-G lap prediction, separate G-G plots; saved reports and CSV exports |
| `/models` | Theoretical tire envelope, LC0 minimum-lap model, LC0/R20 tire comparison; real command parameters and explicit input-file selectors |
| `/setup` | Saved/effective device settings, CAN configuration, live steering calibration, airspeed curves, timing gates/course setup |
| `/help` | Current setup README, hardware steps, historical guides/plans/reports, and team information |

Connection and storage health appear in the shared header on every page. Recording state, driver, and run details appear on Track. The storage bar shows available Pi filesystem space; log destination space is also shown. `/live` remains a compatibility alias for Track. Stale readings fade and the connection indicator distinguishes a reachable panel from fresh sensor data. The locally bundled Sooner Racing Team logo and Help car photo come from the supplied Porsche OKC Assets folder. The theme uses Oklahoma Crimson (`#841617`), black, white, and neutral grays. No remote fonts, scripts, images, or frontend build system are required.

Track places speed, longitudinal/lateral G, and lap timing in one compact panel above the trace. **Edit run info** opens the next-log form inside the run bar; save it before starting a new recording. Each recording retains the info captured at its start. Diagnostics starts collapsed and contains GPS, stream rate/queue/CRC counts, log health, and Pi temperature; alerts remain visible while it is collapsed. Completed runs starts collapsed when empty, opens when results arrive, and preserves your choice during live refreshes. Timing setup is a small link, with a visible prompt when gates are not configured.

### Timing gates & offline maps

Open Setup → Timing gates (also linked from Track). Use **Start stream** beside Start Log in the header for live GPS. Walk to each end of a gate and use **GPS → endpoint 1 / 2**, or enter gate width and travel heading and choose **Center gate on GPS**. **Use GPS heading** copies the VN300 yaw; confirm the intended travel heading, especially while stationary. GPS capture requires a sample under two seconds old, finite coordinates, and position uncertainty ≤10 m. Capture uses the VN300 GPS, not browser location. Save timing to apply and persist the gate; changing a draft does not change active timing.

Track combines the run trace and speed history in one panel. Hover or tap either plot to highlight the same captured sample in both views and inspect its speed, elapsed time, and distance. Arrow keys also select samples. Current and best traces retain their own captured speeds; before a timed trace is available, the speed plot shows the latest 60 seconds of fresh samples.

Select OU campus or Riverwind parking lots, zoom / pan, select **Draw start / finish gate**, then tap two endpoints. Autocross has separate start and finish gates. Manual coordinates remain available. A map gate needs at least 1 m between endpoints. Map imagery is a reference; confirm gate positions with live GPS on site.

Small local JPEGs and their exact EPSG:3857 export extents are bundled in `web/assets/`. They were downloaded on October 1, 2026 from [USGS NAIP Plus / The National Map](https://imagery.nationalmap.gov/arcgis/rest/services/USGSNAIPPlus/ImageServer), which describes its orthoimagery as public domain. Acquisition dates vary; these are not live images. This avoids a Google Maps API key and online map dependency. Maps require no external requests. Preserve `map-areas.json` alongside the images for correct georeferencing.

Sensors uses one expandable tree grouped by measurement type, with all branches collapsed initially. Steering combines raw voltage, ADC count, calculated angle, calibration, and any CAN steering signals. Airspeed and mass flow group their raw and calculated outputs; motion and navigation include IMU, attitude, GPS, velocity, timing, and every other received VN300 field. CAN sensors appear under vehicle controls, powertrain, electrical, or their other sensor type. CAN bus health, estimated bandwidth, and all raw frames appear in the same tree. Filter by sensor, field, message, or CAN ID; matching ancestors open automatically. Expansion and focused CAN scaling edits survive live refreshes.

CAN collection follows the existing recording lifecycle; setup streaming starts VN300 streaming, and the Arduino and Pi mass flow readers run independently when configured. Enable CAN in Setup to use the supplied MoTeC M1 reference map, or select a DBC / custom CSV map to override it. Standard and extended IDs are distinct. The live cache retains up to 2,048 IDs and 8,192 signals, with an explicit eviction count in CAN bus health; every frame continues to be recorded in the existing CSV files. Values fade after two seconds without an update. No CAN transmission is added.

### Bench preview without hardware

Install the Python dependencies, then run:

```sh
python3 app.py --web-only --dashboard-host 127.0.0.1 --dashboard-port 8080 --data-dir /tmp/srt-bench-data
```

This serves real imports, analysis, models, and settings APIs without opening serial ports, attaching GPIO, or recording. Start/Stop requests do not acquire data in this mode. Use a separate `--config /path/to/bench-settings.json` when testing saved device settings. Bench preview does not connect to the Pi or acquire hardware data.

### Runtime storage and analysis worker

Logs retain their existing USB/`~/vn300_logs` locations and boot folders. Web runtime data defaults to `~/vn300_data`; configure `data_dir` in settings, use `--data-dir`, or set `SRT_DATA_DIR`. The directory must be outside application source.

```text
vn300_data/
  imports/<import-id>/...                    Original imported files and ZIP folder structure
  results/<source-session-id>/<job-id>/
    job.json                                Parameters, source IDs, input files, status, errors
    source_metadata.csv                     Combined driver metadata when available
    worker.log                              Progress messages and diagnostics
    out/...                                 Existing HTML/CSV/JSON/PNG analysis outputs
  timing.json                               Last saved course/timing setup
  run_metadata.json                         Next-run metadata restored after restart
```

Comparisons link every source session in the job manifest and are grouped under the first selected session. Models use selected source-session attribution or their input-file/result group. Independent model work has its own input group. Existing archive files are read-only references and remain in place.

One separate Python worker process calls the real `analysis.py` commands. It runs at reduced CPU priority with numerical thread limits. Jobs queue while recording; an active worker is stopped **before** HTTP/GPIO/auto-start capture begins and resumes only after the run drains and flushes. The panel shows queued/running/paused/completed/error states, actual worker output, and exports. Progress is phase/output based, not a fabricated percentage. Jobs can be cancelled. Saved results and queued jobs survive restart; interrupted running jobs are marked as errors and can be submitted again.

Import ZIP/CSV/BIN/JSON/TIR/DBC files in Sessions. Uploads are limited to 256 MiB, ZIPs to 5000 entries and 1 GiB expanded, with storage-reserve and path/link checks. Imports and archive downloads wait until recording stops. Import preserves metadata/timing files and prefers matching BINARY over ASCII for analysis. Individual active-run downloads wait for capture to finish. Download All Logs includes recorded and imported logs; result exports are available on Analysis/Models.

Saved timing gates are discovered beside source CSVs. Without complete gates, lap analysis creates whole-session comparisons and quality reports; timed sectors/prediction require actual laps/runs. Override timing in the advanced parameters when needed. Select several sessions for driver comparison; session JSON driver metadata is merged, or supply a run-sheet/driver map/driver order. Results disclose missing/filtered/uncertain data instead of promising a prediction from unsuitable inputs.

## Pi installation and updates

From the extracted repository root on the Pi:

```sh
sh deploy/install.sh --check
sh deploy/install.sh
```

Defaults: user `vectornav`, app directory `/home/vectornav/vn300_tools`, VN300 port `/dev/ttyUSB0`, baud `921600`, web port `8080`. Sensor and app baud rates must match. Set `VN300_USER` or `VN300_APP_DIR` to override installation defaults.

The installer includes the panel, report theme, local team assets, README, and historical Markdown references. It backs up the existing app and service, preserves settings, copies legacy calibration/CAN files into `config/` when needed, and carries forward existing service command-line flags. It checks `/api/latest` after restarting and restores the previous app and service if the check fails.

The service retains its existing name, `vn300-button-logger.service`, to avoid duplicate recorders during upgrades. Older `vn300-logger` and `vn300-dual-logger` services are disabled. Serial/GPIO/I²C and optional CAN dependencies are installed; numerical model packages are optional. Set `VN300_INSTALL_MODELS=1` to install NumPy, pandas, SciPy, and matplotlib for the minimum-lap model. Set `VN300_OFFLINE_INSTALL=1` to use existing dependencies without downloading.

```sh
systemctl status vn300-button-logger.service --no-pager
journalctl -u vn300-button-logger.service -f
```

Open `http://<pi-ip>:8080/`. Reboot after the first installation if new serial/GPIO/I²C/CAN group permissions have not taken effect.

## Configuration

Use Setup or edit the installed `config/settings.json`, then restart the service between runs. Setup shows saved settings separately from effective service settings. Sensor/network/GPIO/CAN changes require a restart; calibration saves apply live between runs where the corresponding sensor is enabled. Keys use argument names with underscores, such as `steering_port` and `dashboard_port`. Command-line flags override settings. JSON paths are relative to the settings file; command-line paths are relative to the working directory.

```sh
python3 app.py --help
python3 app.py --no-buttons --auto-start --port /dev/ttyUSB0 --baud 921600
python3 app.py --config /path/to/settings.json
```

Use stable `/dev/serial/by-id/` paths for separate VN300 and Arduino devices. Set `steering_port` to enable Arduino capture; its baud defaults to `115200`. The legacy format is one raw integer per line. The panel's steering calibration saves to `config/steering_calibration.json`; raw readings are always retained.

For paired A0 steering and A1 airspeed voltages, set `arduino_format` to `a0-a1-volts` and `airspeed_calibration` to your calibration JSON. Input is `A0_voltage,A1_voltage` per line. Both channels, original readings, and range flags are recorded in the same steering CSV. Copy and edit hardware-specific profiles from `config/examples/`.

The Sensirion SFM3505 connects directly to the **Pi I²C bus**, with `mass_flow_enable: true`, `mass_flow_i2c_bus: 1`, and `mass_flow_sample_hz: 50` (the shipped settings). The Pi checks every sensor CRC, exposes independent `mass_flow` status in `/api/latest`, and records a separate `_MASS_FLOW.csv` with the same run prefix and elapsed-time origin as the VN300 and Arduino files. It streams even without an Arduino serial port or calibration. Enable I²C and use the Pi's 3.3 V supply and SDA/SCL pins; see [SFM3505 wiring and setup](docs/SFM3505_SETUP.md), also in Help. For Uno steering/airspeed, upload [the analog sketch](arduino/analog_telemetry/analog_telemetry.ino) and select `a0-a1-volts`. Saved `a0-a1-sfm3505` settings automatically migrate to this wiring; upload the analog sketch when upgrading.

Optional passive CAN capture uses `can_enable`, `can_profile`, `can_dbc`, `can_interface`, `can_channel`, and `can_bitrate`. CAN is disabled by default. The default map, `config/motec_can_signal_map.csv`, contains 273 fields across all 50 explicit IDs defined in `MoTeC_M1_CAN_Reference.docx`: 47 active IDs (including five VCS compounds on `0x01F`) and three layouts disabled in the supplied ECU source. The synchronization ID `0x021` and other undefined messages remain raw-only. Header-only legacy default maps fall back to the bundled `config/examples/motec_m1_reference.csv`; custom maps and explicit raw-only profiles are respected. The example map is a placeholder, not validated vehicle configuration. A DBC takes precedence over a CSV map.

The reference decoder assumes big-endian byte order, contiguous MSB-first bit offsets, and signed two’s-complement fields. The document leaves these API details unconfirmed; verify known values on the ECU before relying on the decoding. MoTeC’s [M1 Build support example](https://motec.zendesk.com/hc/en-gb/community/posts/16901358291599-M1-Build-Questions) identifies `true` on the receive API as big-endian; applying that convention to `TxOpen(true)` is an inference. Physical units remain “source units” where the reference did not supply channel metadata; enumeration values remain numeric codes. Flags display On/Off. Source scaling is inverted per message, including temperature offsets, signed nibbles, and fuel-used modulo residues. Multiplexed VCS values retain their last sample until that compound returns.

Each CAN signal has editable multiplier and divider fields on Sensors. Click Save to apply `displayed = decoded × multiplier ÷ divider` immediately, including during capture. Both must be finite and the divider must be nonzero. Adjustments are saved in `can_scaling.json` in the web runtime data directory, keyed by ID, standard/extended frame type, and full channel name. Set both to 1 and save to reset. Raw and decoded columns and recorded CSV values retain their original values.

The Sensors bandwidth tile estimates observed classic CAN utilization over a rolling five-second window, with identifier/control/CRC/ACK/intermission overhead and a 20% allowance for stuffed bits. It includes undecoded frames and remote requests. Rates settle from a shorter window at capture start and decay to zero when traffic stops; the tile shows unavailable when capture is inactive, bitrate is unknown, or CAN FD/error traffic appears in the window. Bitrate comes from the CAN profile/settings, or Linux SocketCAN interface timing when the configured bitrate is zero. Set the actual bus bitrate in Setup if it cannot be detected. Filters, receive loss, error/retry traffic, and actual stuffing can make this differ from full physical bus utilization.

Existing detailed hardware references remain available: [CAN](archive/docs/CAN_SETUP.md), [steering](archive/docs/STEERING_SETUP.md), [Arduino analog inputs](archive/docs/ARDUINO_ANALOG_SETUP.md). Use the current paths above when following these historical references.

## Recording

Wire the maintained Log on/off switch and momentary Power button from GPIO to ground; internal pull-ups are enabled.

| Function | BCM GPIO | Physical pin | Behavior |
| --- | ---: | ---: | --- |
| Log switch | 17 | 11 | ON (closed to ground): start; OFF (open): stop and flush |
| Power | 27 | 13 | Hold about two seconds to flush and shut down |

The logger reads the switch at startup: booting with it ON starts a run, and OFF stays idle even with `--auto-start`. Auto-start still works with `--no-buttons` or unavailable GPIO controls. The switch uses 50 ms debounce. After an acquisition fault, cycle OFF then ON to retry. Panel Start/Stop commands still work; the next switch change applies its physical position.

The panel retains Start/Stop Log, setup streaming without recording, run metadata, lap/autocross timing, steering calibration, health indicators, and Download All Logs. Capture queues, CRC checks, live data, fault handling, and shutdown behavior are preserved.

Logs prefer USB storage and fall back to `~/vn300_logs`. Each boot creates a `VN300_BOOT_...` folder. Existing names/formats remain: raw `.bin`, parsed BINARY/ASCII CSVs, session metadata JSON, and optional steering/mass flow/CAN CSVs. Supported VN300 binary configuration remains `1,4,7F,1FF9,004C,060D,A0BF,0004,00C6,061B,A218,0002`.

When Pi mass flow is enabled, each run includes `_MASS_FLOW.csv` with air/O2 flow, raw values, range, and sample status. Session metadata stores mass flow bus settings, scale, reference conditions, sample counts, and errors independently of Arduino metadata.

Keep logs outside the source directory. Updates preserve installed calibration, CAN maps, and settings instead of replacing them with examples.

## Analysis and models

Previous scripts are consolidated into `analysis.py`. Each command keeps its original flags and output formats; add `--help` for options.

| Previous script | Command |
| --- | --- |
| `vn300_lap_analysis.py` | `python3 analysis.py laps` |
| `vn300_gg_analysis.py` | `python3 analysis.py gg` |
| `lc0_theoretical_gg.py` | `python3 analysis.py tire-model` |
| `lc0_minimum_lap.py` | `python3 analysis.py minimum-lap` |
| `tire_ellipse_comparison.py` | `python3 analysis.py tire-comparison` |

The measured G-G lap predictor is included in lap analysis. Folder imports still prefer BINARY over matching ASCII sessions, discover saved timing/metadata, and support driver comparisons, sectors, autocross, overlays, and reports.

```sh
python3 analysis.py laps /path/to/logs --out /path/to/results --no-prompts
python3 analysis.py gg /path/to/logs --out /path/to/gg-results
python3 analysis.py minimum-lap --help
```

Model inputs:

- **Theoretical tire envelope:** fitted LC0 MF6.1 `.TIR` and `gg_envelope_by_driver.csv` from G-G analysis. Parameters include per-tire load (N), pressure (kPa), camber (deg), track grip, and ellipse exponent.
- **Minimum lap:** fitted `.TIR`, original telemetry CSV with `Speed_mps`, `Longitudinal_G`, `Lateral_G`, GPS latitude/longitude, and `Common_AngularRate_Z_rps` or `Imu_AngularRate_Z_rps`. Provide the reference start row (zero-based), sample count, and duration (s). Optional `lap_sector_splits.csv` supplies benchmarks; a selected propulsion session supplies acceleration fitting. Missing numerical dependencies are listed on the page.
- **Tire comparison:** fitted LC0 and R20 `.TIR` files, common load/pressure/camber/grip/exponent, and a normal-load sweep.

Import these files on Sessions first, then select them on Models. All numeric/optional parameters are derived from the same Python parsers used by the CLI. Exports are standalone and contain their report theme. Only the LC0 minimum-lap model requires numerical packages. Install all dependencies in a virtual environment for that model:

```sh
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -r requirements.txt
```

Existing results and captures are preserved under `archive/analysis_output`, `archive/folder_import_output`, `archive/lap_smoke_output`, and `archive/serial_samples`. Historical guides/plans and the original README are in `archive/docs`. Scratch files, rendered documents, and build output are also preserved in the archive.

## Verification

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
node tests/test_dashboard_polling.js
node tests/test_can_panel.js
node tests/test_gate_editor.js
node tests/test_mass_flow_panel.js
node tests/test_sensor_tree.js
node tests/test_trace_view.js
sh deploy/install.sh --check
```

The regression suite also verifies direct page URLs, offline assets, import/path boundaries, downloads, persistence, real worker errors/results, capture pause/resume through flushing, and G-G/model chaining. Numeric model tests skip when their optional packages are unavailable. Tests simulate serial capture and recording faults; CAN integration tests require `python-can` and `cantools`. Verify actual capture, stop, and shutdown on the Pi before deploying to the vehicle.

Hardware acceptance remains: real VN300 packet rates/CRC and sustained writes; USB removal/fallback and disk exhaustion; GPIO stop/restart/shutdown flushing; Arduino units/signs and calibration; GPS lap/autocross gates; and adapter/DBC/CAN channel decoding on the Pi. Verify capture throughput while queuing and resuming analysis before vehicle use.
