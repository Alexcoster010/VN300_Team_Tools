# Installer And Update Checklist

Update this file every time the Pi logger, dashboard, analyzer, service file, or setup workflow changes.

Also update `VERSION_HISTORY.md` with a short version entry.

## When To Use This Checklist

Use this checklist when:

- updating code already installed on the Pi
- installing this package on a new Pi
- changing the logger/dashboard behavior
- changing service settings
- changing analysis workflow or metadata files

## Quick Update Existing Pi

Use this when the Pi is already set up and only the team tools changed.

1. Copy the updated folder from the laptop to the Pi:

   ```powershell
   scp -r "<team-tools-folder>" vectornav@<pi-host>:/home/vectornav/
   ```

2. SSH into the Pi:

   ```powershell
   ssh vectornav@<pi-host>
   ```

3. Run the installer:

   ```sh
   cd /home/vectornav/VN300_Team_Tools/pi
   chmod +x install_on_pi.sh
   ./install_on_pi.sh
   ```

4. Check the service:

   ```sh
   systemctl status vn300-button-logger.service --no-pager
   ```

5. Watch live logs:

   ```sh
   journalctl -u vn300-button-logger.service -f
   ```

6. Open the dashboard:

   ```text
   http://<pi-host>:8080/
   ```

7. Confirm the dashboard loads and shows idle before logging starts.

## Full New Pi Install

Use this when setting up a fresh Pi.

1. Confirm the Pi username is `vectornav`.
2. Confirm the VN-300 appears as `/dev/ttyUSB0`.
3. Confirm the VN-300 baud rate is `921600`.
4. Copy the folder to the Pi:

   ```powershell
   scp -r "<team-tools-folder>" vectornav@<pi-host>:/home/vectornav/
   ```

5. SSH into the Pi:

   ```powershell
   ssh vectornav@<pi-host>
   ```

6. Run the installer:

   ```sh
   cd /home/vectornav/VN300_Team_Tools/pi
   chmod +x install_on_pi.sh
   ./install_on_pi.sh
   ```

7. Reboot once if group permissions changed:

   ```sh
   sudo reboot
   ```

8. After reboot, check service status:

   ```sh
   systemctl status vn300-button-logger.service --no-pager
   ```

## Functional Test After Update

Do this after every logger/dashboard update.

1. Plug in the VN-300.
2. Plug in a flash drive.
3. Open the dashboard at `http://<pi-host>:8080/`.
4. Fill out the Run Metadata panel.
5. Click `Save Run Info`.
6. Confirm `Next Run` shows something like:

   ```text
   VN300_YYYY-MM-DD_RUN001
   ```

7. Press the physical log button once.
8. Confirm dashboard changes from idle to logging.
9. Wait at least 10 seconds.
10. Press the physical log button again.
11. Wait a few seconds for files to flush.
12. Confirm the flash drive has:

   ```text
   VN300_LOGS/
     VN300_BOOT_YYYY-MM-DD_HH-MM-SS/
       VN300_YYYY-MM-DD_RUN001_ttyUSB0.bin
       VN300_YYYY-MM-DD_RUN001_BINARY.csv
       VN300_YYYY-MM-DD_RUN001_session_metadata.json
       VN300_run_metadata.csv
   ```

13. Confirm `VN300_run_metadata.csv` contains the saved dashboard run info.

## Analyzer Test After Update

Do this after analyzer changes.

1. Copy a Pi boot folder to the laptop.
2. Run:

   ```powershell
   py -3 "<team-tools-folder>\analysis\vn300_lap_analysis.py" `
     "<copied-boot-folder>" `
     --no-prompts `
     --out "<output-folder>"
   ```

3. Confirm the output folder contains:

   ```text
   summary.csv
   data_quality.csv
   overlay.html
   report.html
   ```

4. Open `report.html`.
5. Confirm the run metadata appears in the report if `VN300_run_metadata.csv` was present.

## Current Update Notes

Last documented update:

- Logger/dashboard now supports dashboard-entered run metadata.
- Session files are named by date and run number, not time of day.
- Example: `VN300_YYYY-MM-DD_RUN001_BINARY.csv`.
- Run numbers restart each day.
- Rebooting the Pi during the same day should continue with the next unused run number.
- Logger writes `VN300_run_metadata.csv`.
- Analyzer automatically loads `VN300_run_metadata.csv` from a Pi boot folder.

## Files Usually Updated

- `pi/vn300_button_logger.py`
- `pi/vn300-button-logger.service`
- `pi/install_on_pi.sh`
- `analysis/vn300_lap_analysis.py`
- `README.md`
- `ONE_PAGE_SETUP_GUIDE.md`
- `INSTALLER_UPDATE_CHECKLIST.md`
- `PI_INSTALL_CURRENT_VERSION.md`
- `VERSION_HISTORY.md`
