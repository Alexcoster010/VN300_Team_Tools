# VN300 One-Page Setup Guide

This guide is for a first-time team member setting up the VN-300 Raspberry Pi logger.

## What You Need

- Raspberry Pi with this software installed
- VN-300 connected to the Pi by USB serial
- USB flash drive for log files
- Two momentary pushbuttons
- Laptop or phone on the same router as the Pi
- Team router or hotspot

## Button Wiring

Wire each button between the listed Pi pin and any ground pin.

| Button | Pi GPIO | Physical Pin | Other Side |
| --- | ---: | ---: | --- |
| Start/stop logging | GPIO17 | Pin 11 | Ground |
| Safe shutdown | GPIO27 | Pin 13 | Ground |

Do not wire either button to 5V or 3.3V. The software uses the Pi internal pull-up resistors.

## First-Time Software Install

Only do this once, or after the team updates the logger code.

1. On the Pi, download and extract the public logger ZIP:

   `https://github.com/Alexcoster010/VN300_Team_Tools/archive/refs/heads/pi-logger.zip`

2. Open a terminal in the extracted folder and run:

   ```sh
   sh Install_VN300_Logger.sh
   ```

3. Reboot once if the installer changed user groups:

   ```sh
   sudo reboot
   ```

The installer preserves the current CAN map, backs up the old logger, installs dependencies, restarts the service, and verifies the new version through the dashboard API.

## Normal Event Startup

1. Plug the VN-300 into the Pi.
2. Plug the USB flash drive into the Pi.
3. Power on the Pi.
4. Wait about 30 seconds.
5. Find the Pi address:

   ```sh
   hostname -I
   ```

6. Open the dashboard from a laptop/phone on the same router:

   ```text
   http://<pi-host>:8080/
   ```

The dashboard should load even before logging starts. It will show idle until the log button is pressed.

## Start And Stop A Log

- On the dashboard, fill out Run Metadata for the next run.
- Click `Save Run Info`.
- Press the log button once to start logging.
- Drive the run.
- Press the log button again to stop logging.
- Wait a few seconds after stopping so files can flush.
- Hold the power button for about 2 seconds to shut down the Pi safely.

## Check If It Is Working

Run these on the Pi:

```sh
systemctl status vn300-button-logger.service --no-pager
journalctl -u vn300-button-logger.service -f
```

Good signs:

- The service says `active`.
- The dashboard opens at port `8080`.
- Pressing the log button changes the dashboard from idle to logging.
- The flash drive gets a `VN300_LOGS` folder.

## Where The Data Goes

On the flash drive:

```text
VN300_LOGS/
  VN300_BOOT_YYYY-MM-DD_HH-MM-SS/
    VN300_YYYY-MM-DD_RUN001_ttyUSB0.bin
    VN300_YYYY-MM-DD_RUN001_BINARY.csv
    VN300_YYYY-MM-DD_RUN001_session_metadata.json
    VN300_run_metadata.csv
```

Runs are numbered by date. The first run of the day is `RUN001`, then `RUN002`, `RUN003`, and so on. The next day starts over at `RUN001`.

If no flash drive is found, the Pi saves logs to:

```text
/home/vectornav/vn300_logs
```

## Run Offline Analysis

On a Windows laptop with Python installed:

```powershell
py -3 "<team-tools-folder>\analysis\vn300_lap_analysis.py" `
  "<copied-log-folder>" `
  --no-prompts `
  --out "<output-folder>"
```

Open the results:

```text
<output-folder>\summary.csv
<output-folder>\overlay.html
```

## Common Fixes

- Dashboard will not open: make sure the laptop and Pi are on the same router, then check `hostname -I`.
- No serial data: make sure the VN-300 is plugged in and appears as `/dev/ttyUSB0`.
- No files on flash drive: make sure the drive is mounted and has at least 250 MB free.
- Permission errors after install: reboot the Pi once.
- Wrong Pi username: update `install_on_pi.sh`, `vn300-button-logger.service`, and `vn300-shutdown-sudoers`.
