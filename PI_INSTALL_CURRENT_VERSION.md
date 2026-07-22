# Pi Install For Current Version

Current documented Pi logger version: `v0.5.0`

Stable tagged Pi version: `v0.4.0`

If the team needs the last pre-Phase-2 version later:

```sh
git clone https://github.com/Alexcoster010/VN300_Team_Tools.git
cd VN300_Team_Tools
git checkout v0.4.0
```

Use this file to install or update the current VN300 logger/dashboard package on the Raspberry Pi.

The simplest supported workflow is the public `pi-logger` branch ZIP. Download and extract:

```text
https://github.com/Alexcoster010/VN300_Team_Tools/archive/refs/heads/pi-logger.zip
```

Then run from the extracted folder:

```sh
sh Install_VN300_Logger.sh
```

## What This Version Includes

- Dashboard Run Metadata panel.
- Daily run-number filenames:

  ```text
  VN300_YYYY-MM-DD_RUN001_BINARY.csv
  VN300_YYYY-MM-DD_RUN001_ttyUSB0.bin
  VN300_YYYY-MM-DD_RUN001_session_metadata.json
  ```

- VN-300 UTC date correction for binary logs:
  - run files are finalized with the VN-300 UTC date when available
  - session metadata records both Pi clock time and VN-300 UTC time
  - boot folder names can still reflect the Pi clock at service start
- Dashboard log-destination health:
  - `flash drive / writing` means logs are going to the mounted drive
  - `pi local fallback / writing` means the flash drive was not writable at service start
  - `write error` means the active destination failed during logging
  - `VN300_logger_status.json` is written in the boot folder as a heartbeat/status file
- Logger version is included in `/api/latest` as `logger_version`.
- Startup waits up to 20 seconds for a writable USB mount before selecting Pi-local fallback storage.
- Installer backups, CAN-map preservation, service/API health checks, and rollback on failed service startup.

- `VN300_run_metadata.csv` written in the Pi boot log folder.
- Analyzer automatically loads `VN300_run_metadata.csv` from a Pi boot folder.
- Analyzer outputs:

  ```text
  summary.csv
  data_quality.csv
  overlay.html
  report.html
  ```

- Dashboard live track plot during active timing:
  - current path and car position
  - best path after a valid completed lap/run
  - best-path dot at the same elapsed time as the current lap/run
- Dashboard save feedback:
  - Run Metadata and Timing Setup save buttons show a save icon
  - save status shows unsaved, saving, saved, or error feedback
- Completed timing laps reset automatically when a new logging run starts.
- Dashboard shows Pi CPU temperature.
- Optional Phase 2 CAN logging scaffold:
  - disabled unless `--can-enable` is passed
  - raw CAN output to `*_MOTEC_RAW_CAN.csv`
  - decoded channel output to `*_MOTEC_CHANNELS.csv`
  - decode map at `pi/motec_can_signal_map.csv`
  - dashboard CAN health tile

## Existing Pi Update

Use this when the Pi already has the old logger installed.

1. Download and extract the latest public `pi-logger` branch ZIP on the Pi.

2. Open a terminal in the extracted folder and run:

   ```sh
   sh Install_VN300_Logger.sh
   ```

3. Confirm the service is running:

   ```sh
   systemctl status vn300-button-logger.service --no-pager
   ```

4. Watch live logs:

   ```sh
   journalctl -u vn300-button-logger.service -f
   ```

## Fresh Pi Install

Use this when setting up a new Pi.

1. Confirm the Pi user is:

   ```text
   vectornav
   ```

2. Confirm the VN-300 serial device is expected to be:

   ```text
   /dev/ttyUSB0
   ```

3. Download and extract the public `pi-logger` branch ZIP on the Pi.

4. Run the root installer from the extracted folder:

   ```sh
   sh Install_VN300_Logger.sh
   ```

5. Reboot once:

   ```sh
   sudo reboot
   ```

6. Reconnect and check the service:

   ```sh
   ssh vectornav@<pi-host>
   systemctl status vn300-button-logger.service --no-pager
   ```

## Required Functional Test

Run this test after installing.

1. Plug in the VN-300.
2. Plug in the flash drive.
3. Open:

   ```text
   http://<pi-host>:8080/
   ```

4. Fill in the Run Metadata panel.
5. Click `Save Run Info`.
6. Confirm `Next Run` shows:

   ```text
   VN300_YYYY-MM-DD_RUN001
   ```

7. Press the physical log button once.
8. Confirm the dashboard status changes to logging.
9. Wait at least 10 seconds.
10. Press the physical log button again.
11. Wait a few seconds for files to flush.
12. Confirm the flash drive contains:

   ```text
   VN300_LOGS/
     VN300_BOOT_YYYY-MM-DD_HH-MM-SS/
       VN300_YYYY-MM-DD_RUN001_ttyUSB0.bin
       VN300_YYYY-MM-DD_RUN001_session_metadata.json
       VN300_run_metadata.csv
   ```

13. If the VN-300 binary output is configured correctly, also confirm:

   ```text
   VN300_YYYY-MM-DD_RUN001_BINARY.csv
   ```

## If Something Fails

- Dashboard does not open: check that laptop and Pi are on the same router.
- Service is not running: use `journalctl -u vn300-button-logger.service -f`.
- No VN300 data: confirm `/dev/ttyUSB0` exists.
- No flash drive files: confirm the drive is mounted and has at least 250 MB free.
- Permissions error: rerun installer and reboot once.
