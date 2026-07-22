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

## Developer Source Launch

Developers can still clone the branch and run the source application with Python:

```powershell
git clone --branch desktop-app --single-branch `
  https://github.com/Alexcoster010/VN300_Team_Tools.git
cd VN300_Team_Tools
py -3 -B .\app\vn300_desktop_app.py
```

Clean Git checkouts keep the existing fast-forward source update behavior. Extracted source ZIPs keep the validated branch-archive behavior.

## Publish A Team Update

1. Work on the `desktop-app` branch.
2. Increase `APP_VERSION`; never reuse a released version number.
3. Update `VERSION_HISTORY.md`.
4. Run the app, analyzer, and packaging tests.
5. Commit and push `desktop-app`.

The GitHub workflow builds the three bundled executables, compiles the installer, generates its checksum, and publishes release tag `desktop-vX.Y.Z`. Team laptops will then offer the new installer.
