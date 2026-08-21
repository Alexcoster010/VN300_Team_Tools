# VN300 Desktop App Install And Updates

The supported Windows application is published from the public `desktop-app` branch as a versioned GitHub release.

Releases page:

```text
https://github.com/Alexcoster010/VN300_Team_Tools/releases/latest
```

## Requirements

- Windows 10 or Windows 11 on an x64-compatible computer
- Wi-Fi or another network connection to the Raspberry Pi for live telemetry
- Internet access when installing software updates

Python and Git are not required for the installed application.

## Install On A Team Laptop

1. Open the releases page.
2. Download `Sooner-Racing-Telemetry-Setup-X.Y.Z.exe` from the release assets.
3. Run the downloaded installer.
4. Leave **Create a desktop shortcut** selected only when a desktop shortcut is wanted.
5. Launch **Sooner Racing Telemetry** from Windows Search or the Start Menu.
6. Enter the Raspberry Pi IP address or hostname when prompted.

The installer is per-user and does not require administrator access. It installs under:

```text
%LOCALAPPDATA%\Programs\Sooner Racing Telemetry
```

An installation upgraded from v0.10.0 or older may remain in the former `Programs\VN300 Team Tools` directory. This is intentional installer compatibility; Windows Search, shortcuts, Installed Apps, and the running product all use the Sooner Racing Telemetry name.

It also registers a Start Menu shortcut and an uninstall entry under Windows **Installed apps**.

The current installer is not code-signed. Windows SmartScreen may show an unrecognized-app warning until the project has a signing certificate. Confirm the download came from this repository's release page before choosing **More info**, then **Run anyway**. Every release includes a `.sha256` checksum file.

## Saved Data

The Pi address, window layout, and recent-run history are stored under:

```text
%LOCALAPPDATA%\SoonerRacingTelemetry
```

On the first renamed launch, the app reads the former `%LOCALAPPDATA%\VN300TeamTools\desktop_state.json` when a new state file does not exist. The migrated state is written under the new folder the next time settings or window state are saved.

New installed-app analysis output defaults to:

```text
%USERPROFILE%\Documents\Sooner Racing Telemetry\Analysis
```

Installing, updating, or uninstalling the program does not remove those folders.

## Dashboard Setup

The dashboard contains two tabs:

- **Live Telemetry** is the read-focused trackside display.
- **Drive Day Setup** edits the next run's driver, test, tire, chassis, environmental, and review metadata. It also configures lap or autocross start/finish gates and timing thresholds through the connected Pi.

Setup save and reset buttons remain disabled while the Pi is offline. Unsaved form edits are preserved while live telemetry continues refreshing.

## Data Analysis Workspaces

The Data Analysis page has two tabs:

- **Quick Report** runs the standard lap/autocross analysis and produces the team report set with minimal setup.
- **Custom Workspace** scans CSV channels and lets the user choose source runs, X/Y channels, filters, smoothing, calculated channels, plot style, and saved presets.

Custom Workspace accepts ordinary wide CSV files and the Pi logger's long-form decoded MoTeC channel CSV. A saved workspace produces `custom_workspace.html`, `custom_workspace_data.csv`, and `custom_workspace_config.json` in the selected analysis output folder. These results appear in the normal Reports archive.

## Future Updates

The app checks `APP_VERSION` on the public `desktop-app` branch at startup. The header displays **Install update vX.Y.Z** when a newer release is available.

When an installed-app update is accepted:

- the versioned installer and its SHA-256 file are downloaded from the matching GitHub release
- the checksum and Windows executable header are validated
- the app closes and a detached updater runs the installer silently
- the new version restarts automatically and reports the result
- saved settings and analysis output remain unchanged

## Raspberry Pi Logger Updates

Whenever the desktop app connects to the Pi, it compares the `logger_version` reported by `/api/latest` with the current public `pi-logger` version. If the Pi is behind, the app offers to update it.

Accepting a Pi logger update opens a visible SSH terminal. Enter the Pi SSH password and sudo password when requested. The password is handled by Windows OpenSSH and is never read or stored by Sooner Racing Telemetry.

The remote update uses the same supported Pi installer, including dependency checks, timestamped backup, CAN-map preservation, service/API health checks, and rollback. The desktop app reconnects afterward and verifies the installed logger version.

Requirements:

- stop any active logging session before updating
- enable SSH on the Raspberry Pi
- keep the laptop, Pi, and internet-connected network available during the update
- install Windows OpenSSH Client

## Developer Source Launch

Developers can still clone the branch and run the source application with Python:

```powershell
git clone --branch desktop-app --single-branch `
  https://github.com/Alexcoster010/VN300_Team_Tools.git
cd VN300_Team_Tools
py -3 -m pip install PySide6
py -3 -B .\app\vn300_qt_app.py
```

Clean Git checkouts keep the existing fast-forward source update behavior. Extracted source ZIPs keep the validated branch-archive behavior.

## Publish A Team Update

1. Work on the `desktop-app` branch.
2. Increase `APP_VERSION`; never reuse a released version number.
3. Update `VERSION_HISTORY.md`.
4. Run the app, analyzer, and packaging tests.
5. Commit and push `desktop-app`.

The GitHub workflow builds the three bundled executables, compiles the installer, generates its checksum, and publishes release tag `desktop-vX.Y.Z`. Team laptops will then offer the new installer. Pi logger releases remain independently versioned on the `pi-logger` branch.
