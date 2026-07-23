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
2. Download `VN300-Team-Tools-Setup-X.Y.Z.exe` from the release assets.
3. Run the downloaded installer.
4. Leave **Create a desktop shortcut** selected only when a desktop shortcut is wanted.
5. Launch **VN300 Team Tools** from Windows Search or the Start Menu.
6. Enter the Raspberry Pi IP address or hostname when prompted.

The installer is per-user and does not require administrator access. It installs under:

```text
%LOCALAPPDATA%\Programs\VN300 Team Tools
```

It also registers a Start Menu shortcut and an uninstall entry under Windows **Installed apps**.

The current installer is not code-signed. Windows SmartScreen may show an unrecognized-app warning until the project has a signing certificate. Confirm the download came from this repository's release page before choosing **More info**, then **Run anyway**. Every release includes a `.sha256` checksum file.

## Saved Data

The Pi address, window layout, and recent-run history are stored under:

```text
%LOCALAPPDATA%\VN300TeamTools
```

New installed-app analysis output defaults to:

```text
%USERPROFILE%\Documents\VN300 Team Tools\Analysis
```

Installing, updating, or uninstalling the program does not remove those folders.

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

Accepting a Pi logger update opens a visible SSH terminal. Enter the Pi SSH password and sudo password when requested. The password is handled by Windows OpenSSH and is never read or stored by VN300 Team Tools.

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
