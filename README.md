# VN300 Team Tools

This folder is a transfer-ready package for Raspberry Pi logging, live dashboard viewing, and laptop/offline VN-300 data analysis.

For a beginner-friendly setup checklist, start with `ONE_PAGE_SETUP_GUIDE.md`.

For Pi software updates and post-update checks, use `INSTALLER_UPDATE_CHECKLIST.md`.

For change tracking, use `VERSION_HISTORY.md`.

## Folder Contents

- `ONE_PAGE_SETUP_GUIDE.md`: one-page setup guide for a new team member.
- `INSTALLER_UPDATE_CHECKLIST.md`: step-by-step update/install checklist to maintain after future changes.
- `PI_INSTALL_CURRENT_VERSION.md`: concise current-version install/update procedure for the Pi.
- `VERSION_HISTORY.md`: version and change history for the team tools.
- `MOTEC_CAN_TO_PI_INTEGRATION_REPORT.md`: detailed plan for MoTeC M130 CAN broadcast into the Pi logger.
- `PHASE_2_PLAN.md`: next-step plan for MoTeC/dash CAN, steering angle, driver inputs, and balance metrics.
- `motec_can_signal_map_template.csv`: example MoTeC/dash CAN signal map to fill in after CAN IDs and scaling are known.
- `pi/vn300_button_logger.py`: Raspberry Pi logger and live dashboard. It starts at boot, then waits idle until the log button is pressed.
- `pi/vn300-button-logger.service`: systemd service for the logger/dashboard.
- `pi/install_on_pi.sh`: installer to run on the Pi after copying this folder.
- `pi/vn300-shutdown-sudoers`: allows the service user to shut down the Pi from the power button.
- `pi/requirements-pi.txt`: Python packages needed on the Pi.
- `pi/motec_can_signal_map.csv`: Pi-side CAN signal map used by optional Phase 2 CAN logging.
- `analysis/vn300_lap_analysis.py`: offline analysis, lap splitting, and overlay HTML generation from `*_BINARY.csv` or `*_VNINS.csv`.
- `analysis_output/`, `folder_import_output/`, `lap_smoke_output/`: example generated outputs.
- `serial_samples/`: example raw serial capture.

## Assumptions And Placeholders

The included Pi service and installer are set up for these team defaults:

- Pi username: `vectornav`
- VN-300 serial port: `/dev/ttyUSB0`
- VN-300 baud rate: `921600`
- Dashboard port: `8080`
- Installed app path on Pi: `/home/vectornav/vn300_tools`

If your Pi uses a different username, serial port, or baud rate, update these files before installing:

- `pi/install_on_pi.sh`
- `pi/vn300-button-logger.service`
- `pi/vn300-shutdown-sudoers`

Generic placeholders used below:

- `<team-tools-folder>`: local path to this `VN300_Team_Tools` folder on your laptop.
- `<data-folder>`: local path to a folder containing VN300 log CSV files.
- `<output-folder>`: local folder where analysis results should be written.
- `<pi-user>`: Pi login username, usually `vectornav`.
- `<pi-host>`: Pi hostname or IP address, for example `raspberrypi.local` or `192.168.1.25`.
- `<flash-drive-log-folder>`: copied Pi log folder, usually a `VN300_BOOT_YYYY-MM-DD_HH-MM-SS` folder.

## Button Wiring

Use momentary pushbuttons wired from GPIO pin to ground. The script enables internal pull-ups.

| Function | Raspberry Pi BCM GPIO | Physical Pin | Other Button Leg |
| --- | ---: | ---: | --- |
| Log toggle | GPIO17 | Pin 11 | Ground |
| Power hold | GPIO27 | Pin 13 | Ground |

Log button behavior:

- Press once: start a new logging session.
- Press again: stop logging and flush files.

Power button behavior:

- Hold about 2 seconds: stop logging, flush files, and shut down the Pi.

## Stable v0.4.0 Install From GitHub

The last VN300-only version before Phase 2 work is tagged:

```text
v0.4.0
```

On a Pi or laptop with Git installed, that exact version can be checked out with:

```sh
git clone https://github.com/Alexcoster010/VN300_Team_Tools.git
cd VN300_Team_Tools
git checkout v0.4.0
```

Use that tag if the team needs the stable logger/dashboard before the Phase 2 CAN work is ready.

## Copy Tools To The Pi

From Windows PowerShell, after your laptop and Pi are on the same network:

```powershell
scp -r "<team-tools-folder>" <pi-user>@<pi-host>:/home/<pi-user>/
```

Examples:

```powershell
scp -r "C:\Path\To\VN300_Team_Tools" vectornav@raspberrypi.local:/home/vectornav/
scp -r "C:\Path\To\VN300_Team_Tools" vectornav@192.168.1.25:/home/vectornav/
```

Then SSH into the Pi:

```powershell
ssh <pi-user>@<pi-host>
```

Install on the Pi:

```sh
cd /home/<pi-user>/VN300_Team_Tools/pi
chmod +x install_on_pi.sh
./install_on_pi.sh
```

With the default user, that is:

```sh
cd /home/vectornav/VN300_Team_Tools/pi
chmod +x install_on_pi.sh
./install_on_pi.sh
```

The installer adds the default service user to the `dialout` and `gpio` groups so the service can access `/dev/ttyUSB0` and the GPIO pins. If serial or GPIO permissions still fail, reboot the Pi once after running the installer.

The installer disables older auto-start logger services if they exist:

- `vn300-logger.service`
- `vn300-dual-logger.service`

Then it enables and starts:

- `vn300-button-logger.service`

## Pi Test Commands

Check service status:

```sh
systemctl status vn300-button-logger.service --no-pager
```

Watch live service logs:

```sh
journalctl -u vn300-button-logger.service -f
```

Manual test without GPIO buttons:

```sh
python3 /home/<pi-user>/vn300_tools/vn300_button_logger.py --port /dev/ttyUSB0 --baud 921600 --no-buttons --auto-start
```

With the default user:

```sh
python3 /home/vectornav/vn300_tools/vn300_button_logger.py --port /dev/ttyUSB0 --baud 921600 --no-buttons --auto-start
```

Stop the manual test with `Ctrl+C`. It should create one `.bin` file and one or more message CSV files.

The VN-300 serial baud rate and the service baud rate must match.

## Binary VectorNav Output

The logger safely records binary serial output to the raw `.bin` file at `921600` baud. The live dashboard includes a stream counter showing raw bytes, binary bytes, parsed binary packets, and parsed ASCII packet count.

Current limitation:

- Raw binary capture works.
- ASCII CSV/dashboard parsing works for `$VN...` ASCII packets.
- Binary CSV/dashboard parsing is implemented for the saved Serial 1 Binary Output Message Configuration #1:
  `1,4,7F,1FF9,004C,060D,A0BF,0004,00C6,061B,A218,0002`.
- In auto parse mode, valid binary packets become the authoritative live dashboard/timing stream. ASCII packets can still be written, but they will not override binary live timing once binary is active.

With that binary configuration, each logging session creates:

```text
VN300_YYYY-MM-DD_RUN001_ttyUSB0.bin
VN300_YYYY-MM-DD_RUN001_BINARY.csv
VN300_YYYY-MM-DD_RUN001_session_metadata.json
```

The binary CSV includes normalized columns used by the dashboard and analyzer:

- `Latitude_deg`, `Longitude_deg`, `Altitude_m`
- `Yaw_deg`, `Pitch_deg`, `Roll_deg`
- `Vel_N_mps`, `Vel_E_mps`, `Vel_D_mps`, `Speed_mph`
- `Longitudinal_Accel_mps2`, `Lateral_Accel_mps2`, `Vertical_Accel_mps2`
- `Longitudinal_G`, `Lateral_G`, `Vertical_G`
- `PosUncertainty_m`, `VelUncertainty_mps`

The analyzer folder import prefers `*_BINARY.csv` over a matching `*_VNINS.csv` so the same session is not analyzed twice. Use `--include-ascii` only when you intentionally want both files included.

## Optional Phase 2 CAN Logging

Current development versions include the start of passive MoTeC/dash CAN logging. It is disabled by default.

Default service behavior:

- VN300 logging still works without CAN hardware.
- The service file does not include `--can-enable`.
- No CAN files are created unless CAN is enabled.

When CAN is enabled, each run can create:

```text
VN300_YYYY-MM-DD_RUN001_MOTEC_RAW_CAN.csv
VN300_YYYY-MM-DD_RUN001_MOTEC_CHANNELS.csv
```

`*_MOTEC_RAW_CAN.csv` stores every received CAN frame. `*_MOTEC_CHANNELS.csv` stores decoded channels from:

```text
pi/motec_can_signal_map.csv
```

The team must fill in the real MoTeC/dash CAN IDs, bit positions, scaling, offsets, and units before decoded values are meaningful. Use `motec_can_signal_map_template.csv` as the starting point.

Manual CAN test example after `can0` is configured on the Pi:

```sh
python3 /home/<pi-user>/vn300_tools/vn300_button_logger.py \
  --port /dev/ttyUSB0 \
  --baud 921600 \
  --no-buttons \
  --auto-start \
  --can-enable \
  --can-channel can0 \
  --can-bitrate 1000000
```

## Flash Drive Output

The logger searches `/media` and `/mnt` for a writable mounted drive. It writes to:

```text
VN300_LOGS/
```

Every time the Pi boots and the logger service starts, it creates a new boot folder:

```text
VN300_LOGS/
  VN300_BOOT_YYYY-MM-DD_HH-MM-SS/
```

All logger output from that power cycle goes inside that boot folder. The boot folder name is based on the Pi clock at service start, so it can be wrong if the Pi has no network time.

Each logging session is numbered by date. When binary VN-300 packets include a valid UTC date, the logger uses that VN-300 UTC date for the final run filename and run metadata. This keeps run files correctly dated even when the Pi clock is wrong or offline. The first run of a day is `RUN001`, the next is `RUN002`, and the count restarts the next day. If the Pi reboots during a test day, the logger scans existing same-day files under `VN300_LOGS` and continues with the next run number.

Each logging session also writes `VN300_YYYY-MM-DD_RUN###_session_metadata.json` with the run number, dashboard run metadata, port, baud rate, parse mode, stop reason, byte counts, packet counts, free space, Pi clock timestamps, and VN-300 UTC timestamps when available. The logger also appends one row to `VN300_run_metadata.csv` in the boot folder. The logger refuses to start, or stops an active session, when the selected log drive has less than 250 MB free.

If no writable flash drive is found, it falls back to:

```text
/home/<pi-user>/vn300_logs
```

With the default user:

```text
/home/vectornav/vn300_logs
```

Expected files:

```text
VN300_BOOT_YYYY-MM-DD_HH-MM-SS/
  VN300_YYYY-MM-DD_RUN001_ttyUSB0.bin
  VN300_YYYY-MM-DD_RUN001_BINARY.csv
  VN300_YYYY-MM-DD_RUN001_VNINS.csv
  VN300_YYYY-MM-DD_RUN001_VNIMU.csv
  VN300_YYYY-MM-DD_RUN001_session_metadata.json
  VN300_run_metadata.csv
  VN300_dashboard_timing_config.csv
```

`VN300_dashboard_timing_config.csv` is written when timing setup is saved in the dashboard. It records lap/autocross mode, start line coordinates, finish/stop line coordinates if used, minimum speed, and minimum crossing gap.

`VN300_run_metadata.csv` is written from the dashboard Run Metadata panel. Before a run, enter the driver, test type, course, setup notes, tire pressures, and validity flag, then click `Save Run Info`. The physical log button then starts the next numbered run using that saved metadata.

## Live Dashboard Over Router

Connect the Pi Ethernet or Wi-Fi to the car router. Once the service is running, any laptop/phone on that router can open:

```text
http://<pi-host>:8080/
```

Examples:

```text
http://raspberrypi.local:8080/
http://192.168.1.25:8080/
```

The dashboard starts when the Pi service starts. Logging does not need to be active for the dashboard page to load. Before logging starts, the dashboard will show idle/blank live data.

The dashboard shows current logging state, speed, yaw, GPS position, position uncertainty, checksum state, and a rolling speed trace.

The dashboard `Log` tile shows where the logger is writing:

- `flash drive / writing / ... MB`: expected drive-day state.
- `pi local fallback / writing / ... MB`: the Pi could not use the flash drive at service start, so logs are going to `/home/<pi-user>/vn300_logs`.
- `write error`: the active log destination failed during a run. Stop the run and check the service logs before continuing.

Live dashboard data only proves that the Pi is receiving and decoding VN-300 packets. It does not prove the flash drive is accepting writes; use the `Log` tile and `VN300_logger_status.json` in the boot folder to confirm file logging health.

When live timing is configured and a run/lap is active, the dashboard also shows a GPS track plot:

- blue path: current lap/run path
- blue dot: current car position
- green path: best completed valid lap/run path, once one exists
- green dot: where the car was on the best path at the same elapsed time as the current lap/run

The dashboard also has run metadata setup:

- `Next Run`: shows the next date/run-number file ID, such as `VN300_2026-07-15_RUN004`.
- `Driver`, `Test Type`, `Course`, `Location`, `Car Setup`, `Tire`, pressures, brake bias, aero, validity, and notes.
- `Save Run Info`: saves the metadata that will be written into the next log session.

The dashboard also has live timing setup:

- `Lap`: enter the two GPS endpoints of the start/finish line.
- `Autocross`: enter the two GPS endpoints of the start line and the two GPS endpoints of the finish line.
- `Min Speed mph`: ignores line crossings below this speed.
- `Min Gap s`: ignores repeat crossings too close together.

After saving the setup, the dashboard displays:

- completed lap/run count
- current running lap/run time
- best lap/run time
- live delta to the best completed lap/run once a reference exists
- recent completed lap/run table

For a lap track, the first start/finish crossing arms timing. Each later crossing completes the current lap and immediately starts the next one.

For autocross, crossing the start line starts a run and crossing the finish line completes it.

The first completed lap/run creates the reference, so live delta is blank during that first lap/run. Every later lap/run shows live delta against the best completed reference at the same distance into the lap/run. Completed deltas are still shown in the recent results table.

Live delta is suppressed when VN-300 position uncertainty is above `4.0 m`. Timing-line crossings are still recorded, but completed lap/run rows show an invalid GPS warning when the crossing was made with position uncertainty above `4.0 m`. Warned laps/runs are not used as the best-lap reference for future deltas.

Useful network commands on the Pi:

```sh
hostname -I
ip addr
```

If browser clients cannot connect, check that the Pi and laptop are on the same router subnet and that port `8080` is not blocked.

## Offline Analysis

The analysis script reads `*_BINARY.csv`, `*_VNINS.csv`, or a folder containing logger CSV files. It has no third-party dependencies. When a folder has both binary and ASCII output for the same session, binary is used by default.

Basic summary and overlay for a log folder:

```powershell
py -3 "<team-tools-folder>\analysis\vn300_lap_analysis.py" `
  "<data-folder>" `
  --no-prompts `
  --out "<output-folder>"
```

That writes a whole-run summary and overlay without lap/run splitting:

```text
<output-folder>\summary.csv
<output-folder>\overlay.html
```

To split laps, enter two GPS coordinates that define the start/finish line:

```powershell
py -3 "<team-tools-folder>\analysis\vn300_lap_analysis.py" `
  "<data-folder>\VN300_YYYY-MM-DD_RUN001_BINARY.csv" `
  --mode lap `
  --start-lat1 35.000000 `
  --start-lon1 -97.000000 `
  --start-lat2 35.000100 `
  --start-lon2 -97.000100 `
  --min-speed-mph 5 `
  --min-lap-seconds 20 `
  --out "<output-folder>"
```

Replace the example coordinates with the actual two endpoints of the timing line.

To analyze autocross with separate start and finish lines:

```powershell
py -3 "<team-tools-folder>\analysis\vn300_lap_analysis.py" `
  "<data-folder>\VN300_YYYY-MM-DD_RUN001_BINARY.csv" `
  --mode autocross `
  --start-lat1 35.000000 `
  --start-lon1 -97.000000 `
  --start-lat2 35.000100 `
  --start-lon2 -97.000100 `
  --finish-lat1 35.001000 `
  --finish-lon1 -97.001000 `
  --finish-lat2 35.001100 `
  --finish-lon2 -97.001100 `
  --min-speed-mph 5 `
  --min-lap-seconds 20 `
  --out "<output-folder>"
```

If you leave out `--mode` or line coordinates, the analyzer prompts for `lap` or `autocross` and asks for the needed GPS endpoints.

If you pass a boot folder from the Pi, the analyzer automatically finds the session CSVs in it. If that folder contains `VN300_dashboard_timing_config.csv`, the analyzer automatically loads the latest saved dashboard timing setup:

```powershell
py -3 "<team-tools-folder>\analysis\vn300_lap_analysis.py" `
  "<flash-drive-log-folder>" `
  --out "<output-folder>"
```

If the boot folder contains `VN300_run_metadata.csv`, the analyzer also loads the dashboard run metadata automatically. You can still pass a separate metadata file manually:

```powershell
py -3 "<team-tools-folder>\analysis\vn300_lap_analysis.py" `
  "<data-folder>" `
  --metadata "<team-tools-folder>\metadata_template.csv" `
  --out "<output-folder>"
```

When lap or autocross timing is active, the analyzer automatically splits each timed lap/run into three sectors. It aims for even time sections, then moves each split to the nearest low-cornering section so the split is between corners instead of during a corner.

To change the number of automatic sectors:

```powershell
py -3 "<team-tools-folder>\analysis\vn300_lap_analysis.py" `
  "<data-folder>" `
  --mode lap `
  --start-lat1 35.000000 `
  --start-lon1 -97.000000 `
  --start-lat2 35.000100 `
  --start-lon2 -97.000100 `
  --auto-sectors 4 `
  --out "<output-folder>"
```

Use `--auto-sectors 0` to disable automatic sectors.

When automatic sectors are used, the analyzer writes:

```text
<output-folder>\sector_summary.csv
```

The generated `report.html` also includes a distance-based delta-to-fastest plot for run-to-run comparison.

Generated lap/run CSVs include:

- segment number
- segment time
- segment distance
- final delta to best previous lap/run
- live delta to best previous lap/run at each sample
- live delta error text when position uncertainty is above `4.0 m`
- timing warning text when a start/finish crossing was recorded with position uncertainty above `4.0 m`

The first lap/run has blank live delta because no reference exists yet. Every later lap/run gets live delta compared to the best previous completed lap/run.

If `PosUncertainty_m` is above `4.0 m`, `live_delta_to_best_s` is left blank and `live_delta_error` explains the GPS uncertainty problem.

Start/finish line crossings are still recorded when either GPS point around the crossing has `PosUncertainty_m` above `4.0 m`, but the analyzer adds `timing_warning` to the segment summary and lap/run CSV. Warned segments are not used as the best reference for later live-delta calculations.

## Field Checklist

1. Confirm the Pi username matches the installer/service files.
2. Confirm the VN-300 appears as `/dev/ttyUSB0`, or update the service file.
3. Confirm the VN-300 serial baud rate is `921600`, or update the service file.
4. Wire the log button to GPIO17/GND and the power button to GPIO27/GND.
5. Copy this folder to the Pi and run `install_on_pi.sh`.
6. Boot with the flash drive inserted.
7. Verify the service is idle, press the log button, watch `journalctl`, then press again to stop.
8. Open `http://<pi-host>:8080/` from a laptop connected to the same router.
9. Pull the new `VN300_LOGS` files from the flash drive and run the analysis script.
