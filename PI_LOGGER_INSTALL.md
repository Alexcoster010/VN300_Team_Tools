# VN300 Pi Logger Install And Updates

The supported Raspberry Pi logger channel is the public `pi-logger` branch:

```text
https://github.com/Alexcoster010/VN300_Team_Tools/tree/pi-logger
```

Direct ZIP download:

```text
https://github.com/Alexcoster010/VN300_Team_Tools/archive/refs/heads/pi-logger.zip
```

## Install Or Update From A Downloaded ZIP

1. Download the `pi-logger` ZIP on the Raspberry Pi.
2. Extract it with the Pi file manager.
3. Open a terminal in the extracted `VN300_Team_Tools-pi-logger` folder.
4. Run:

   ```sh
   sh Install_VN300_Logger.sh
   ```

The same command handles both a new installation and an update.

The installer:

- validates every required package file and checks the logger's Python syntax
- installs `pyserial`, `gpiozero`, and `python-can` when needed
- backs up the currently installed software and service files
- stops legacy VN300 services
- installs the logger, systemd service, sudoers rule, dependency list, and version marker
- preserves the installed `motec_can_signal_map.csv`
- places a new default CAN map beside it as `motec_can_signal_map.csv.dist`
- enables and restarts `vn300-button-logger.service`
- waits for `/api/latest` to report the newly installed logger version
- restores the previous logger when the new service fails its health check

Backups are stored at:

```text
/home/vectornav/vn300_backups/
```

Logs on USB storage and under `/home/vectornav/vn300_logs` are not replaced or removed.

## Check A Download Before Installing

```sh
sh Install_VN300_Logger.sh --check
```

This validates the downloaded package without stopping or changing the installed service.

## Verify The Installed Version

Open this address from the Pi or another computer on the same network:

```text
http://<pi-ip>:8080/api/latest
```

The JSON response includes:

```json
{"logger_version": "0.5.0"}
```

Service checks:

```sh
systemctl status vn300-button-logger.service --no-pager
journalctl -u vn300-button-logger.service -n 60 --no-pager
```

## Publish A Future Logger Version

1. Work on the `pi-logger` branch.
2. Increase `PI_LOGGER_VERSION`.
3. Update `VERSION_HISTORY.md` and this guide when behavior changes.
4. Run the package and logger tests.
5. Commit and push `pi-logger`.

The branch ZIP URL remains the same, so the team always downloads the newest published logger package.
