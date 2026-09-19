# Sooner Racing Telemetry

Raspberry Pi VN-300 logging, live trackside monitoring, and offline vehicle telemetry analysis for the Sooner Racing Team.

## Current status

Last verified against GitHub and local development on **September 19, 2026**.

| Component | Status |
| --- | --- |
| Published Windows app | **v0.15.0**, available from GitHub Releases; bundles Pi logger **v0.6.0**. |
| Independent `pi-logger` branch | **v0.6.0**. |
| Locally completed Windows app | **v0.15.2**, built, installed, and confirmed to open on the development laptop. Not yet published to GitHub Releases. |
| Locally completed Pi logger | **v0.6.1**, with capture reliability improvements and CAN profile/DBC support, bundled in the local desktop build. Not yet published in the release channels above. |

The sections describing local development below are a progress record, not features promised by the currently downloadable installer. This README update does not publish an app or install a logger on the Pi.

## Download and install the Windows app

[**Download the latest published release**](https://github.com/Alexcoster010/VN300_Team_Tools/releases/latest)

Download `Sooner-Racing-Telemetry-Setup-X.Y.Z.exe`, run it, and open **Sooner Racing Telemetry** from Windows Search or the Start Menu. Python and Git are bundled or unnecessary for normal installed-app use. Releases include SHA-256 checksum files; installers are currently unsigned.

The app installs per user. Existing installations may retain the older `Programs\VN300 Team Tools` folder while using the Sooner Racing Telemetry name. Saved connections, analysis presets, and report history are retained across updates.

[Desktop installation and update guide](https://github.com/Alexcoster010/VN300_Team_Tools/blob/desktop-app/DESKTOP_APP_INSTALL.md)

## What the app does

- **Live dashboard:** VN-300 speed, acceleration, GPS position and trace, timing, lap history, storage, and logger health over the local network.
- **Drive Day Setup:** edit run metadata, configure lap or autocross timing gates, and use live GPS to place gate endpoints. Setup Stream provides live VN-300 data without recording a run or advancing the run number.
- **Quick Report:** import VN-300 sessions, split laps/runs, compare drivers and sectors, and generate reports.
- **Custom Workspace:** select channels, define calculated channels and filters, compare line/scatter plots, save presets, and export data and reports.
- **Reports:** view generated HTML reports and CSV previews from the app.
- **Updates:** download versioned Windows installers with checksum validation and update the Pi over SSH from logger files embedded in the desktop app.

The laptop and Pi must be on the same network for live monitoring. Offline analysis works with copied log files. Default analysis output is `Documents\Sooner Racing Telemetry\Analysis`; application settings are stored in `%LOCALAPPDATA%\SoonerRacingTelemetry`.

## Raspberry Pi logging and updates

The default configuration uses user `vectornav`, VN-300 serial port `/dev/ttyUSB0`, baud rate `921600`, and dashboard port `8080`. Confirm these against the actual Pi before installation.

The logger starts at boot and waits idle. The log button on BCM GPIO17 starts/stops a recording; holding the power button on BCM GPIO27 stops recording and shuts the Pi down. Buttons connect to ground with internal pull-ups.

Sessions record raw serial `.bin` data, parsed CSVs, and session metadata. Run metadata and timing setup are saved alongside the recordings. Binary decoding supports the configured VN-300 output layout; use the setup documentation to match sensor and logger settings.

Since desktop **v0.15.0**, the app compares the Pi's reported version with its **bundled logger version**. The update transfers the bundled archive using `scp`/SSH; it does not need GitHub access on the track network. SSH/sudo credentials are entered in the update terminal. The Pi must already have the required Python dependencies for an offline installation.

The Pi installer backs up the existing installation, preserves an installed CAN CSV map, restarts the service, checks its API, and attempts rollback if the new logger fails its health check.

- [Pi install/update instructions](https://github.com/Alexcoster010/VN300_Team_Tools/blob/desktop-app/PI_INSTALL_CURRENT_VERSION.md)
- [Installation and verification checklist](https://github.com/Alexcoster010/VN300_Team_Tools/blob/desktop-app/INSTALLER_UPDATE_CHECKLIST.md)
- [Independent Pi logger source ZIP](https://github.com/Alexcoster010/VN300_Team_Tools/archive/refs/heads/pi-logger.zip)

## Completed locally: logger reliability and CAN preparation

Pi logger **v0.6.1** adds a dedicated serial capture thread and bounded queue, larger buffers, binary CRC validation and resynchronization, and health metrics for sample gaps, estimated missing samples, effective rate, and queue pressure. These changes reduce exposure to processing/storage delays; they do not guarantee loss-free capture under all hardware conditions.

The local CAN implementation includes:

- `python-can` transport, `cantools` DBC decoding, and `pyusb` as a USB backend foundation.
- JSON profiles for adapter interface, channel, bitrate, backend options, and decode-file paths.
- Raw capture, existing CSV signal-map decoding, and DBC decoding including multiplexed signals. A selected DBC takes precedence over the CSV map.
- Compatible `MOTEC_RAW_CAN.csv` and `MOTEC_CHANNELS.csv` outputs for the analyzer.
- Online installer attempts for optional CAN dependencies, plus a setup guide and profile template in the desktop's Pi payload.

CAN remains **disabled by default** and requires `--can-enable`. Selecting a profile alone does not enable it. Setup Stream does not start CAN capture. Receiving frames without calling `send` does not by itself guarantee hardware listen-only operation.

**Still needed:** confirmed USB-to-CAN adapter details, any required Pi-compatible driver/SDK and permissions, verified bus bitrate/listen-only settings, actual vehicle DBC or MoTeC CSV definitions, and testing on the physical bus. Python libraries alone do not install vendor drivers. The supplied CSV map has no vehicle signal definitions; adapter settings and scaling must come from the actual hardware and ECU configuration.

The local source includes `pi/CAN_SETUP.md` and `pi/can_profile.example.json`; those additions are not yet available on the published branches. No real-adapter or vehicle-bus validation is claimed here.

## Completed locally: Windows startup repair

The local **v0.15.2** build fixes a Qt startup failure caused by an incompatible ICU DLL collected from an unrelated tool on the build machine's `PATH`. The build now restricts DLL discovery and runs the frozen desktop executable to verify its main window opens before generating an installer.

The repaired app was installed and observed running responsively on the development laptop. **38 regression tests passed**, including CAN/DBC tests, virtual CAN capture, logger tests, and desktop/packaging checks. This is software validation, not Pi or CAN hardware validation. Use the release status table above to distinguish this local repair from the public download.

## Analysis and engineering tools

The analysis tools support lap/autocross timing, sector comparisons, driver overlays, G-G envelopes, custom calculations, and measured-envelope same-line lap estimates. Additional scripts compare fitted tire-model friction ellipses and calculate same-line minimum-lap scenarios.

These lap estimates are mathematical reference bounds subject to their model assumptions, not achievable-lap forecasts. Tire-model files and recorded telemetry must be supplied separately where required.

[Detailed analysis commands and data formats](https://github.com/Alexcoster010/VN300_Team_Tools/blob/desktop-app/README.md#offline-analysis)

## Repository branches and documentation

| Branch | Purpose |
| --- | --- |
| [`main`](https://github.com/Alexcoster010/VN300_Team_Tools/tree/main) | Repository front page, shared source, and project documentation. |
| [`desktop-app`](https://github.com/Alexcoster010/VN300_Team_Tools/tree/desktop-app) | Published Windows app source and installer workflow. App versions on this branch drive desktop update checks. |
| [`pi-logger`](https://github.com/Alexcoster010/VN300_Team_Tools/tree/pi-logger) | Separate Pi logger source/install channel; current desktop offline updates use their embedded payload. |

- [Beginner setup guide](https://github.com/Alexcoster010/VN300_Team_Tools/blob/desktop-app/ONE_PAGE_SETUP_GUIDE.md)
- [Published version history](https://github.com/Alexcoster010/VN300_Team_Tools/blob/desktop-app/VERSION_HISTORY.md)
- [Custom analysis roadmap](https://github.com/Alexcoster010/VN300_Team_Tools/blob/desktop-app/CUSTOM_ANALYSIS_WORKSPACE_ROADMAP.md)
- [MoTeC integration planning report](https://github.com/Alexcoster010/VN300_Team_Tools/blob/desktop-app/MOTEC_CAN_TO_PI_INTEGRATION_REPORT.md)

Planning reports describe intended integration work; the release/status sections above identify what is actually available and what still requires hardware verification.
