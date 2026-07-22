# VN300 Desktop App Install And Updates

The supported desktop-app update channel is the `desktop-app` branch:

```text
https://github.com/Alexcoster010/VN300_Team_Tools/tree/desktop-app
```

## Requirements

- Windows 10 or Windows 11
- Python 3.10 or newer with the Windows `py` launcher
- Wi-Fi or another network connection to the Raspberry Pi for live telemetry
- Internet access when checking for software updates

## Recommended Install With Git

This repository is private. Use an authenticated Git clone for automatic updates:

```powershell
git clone --branch desktop-app --single-branch `
  https://github.com/Alexcoster010/VN300_Team_Tools.git
```

Git will use Git Credential Manager to sign in to a GitHub account that has access to the team repository. Then run `Start_VN300_Team_Tools.bat` inside the cloned folder.

## Download From GitHub ZIP

1. Open the `desktop-app` branch link above.
2. Select **Code**, then **Download ZIP**.
3. Extract the ZIP to a writable folder such as `Documents\VN300_Team_Tools`.
4. Double-click `Start_VN300_Team_Tools.bat`.
5. Enter the Raspberry Pi IP address or hostname when prompted.

The Pi address is saved only after the first successful connection. Later launches reconnect automatically.

Direct branch ZIP:

```text
https://github.com/Alexcoster010/VN300_Team_Tools/archive/refs/heads/desktop-app.zip
```

Because the GitHub repository is private, a ZIP installation cannot silently authenticate for later downloads. Use the authenticated Git installation above when automatic in-app updates are required. ZIP users can still download and extract future versions manually while signed in on github.com.

## Install Future Updates

The app checks `APP_VERSION` on the GitHub `desktop-app` branch at startup. The header displays **Install update vX.Y.Z** when a newer version is available. The same check can be run manually with **Check updates**.

When an update is accepted:

- A clean authenticated Git checkout checks and fast-forwards from `origin/desktop-app`.
- A public-repository Download ZIP installation can download and validate the latest branch archive without Git.
- The app closes, applies the update, restarts, and reports whether the update succeeded.
- Archive-install files that will be replaced are backed up under `%LOCALAPPDATA%\VN300TeamTools\update_backups`.
- Settings, saved Pi address, and recent-run history remain under `%LOCALAPPDATA%\VN300TeamTools` and are not replaced.

A Git update stops if tracked files have local modifications. Commit, stash, or discard those edits before trying again.

## Publish A Team Update

1. Make changes on the `desktop-app` branch.
2. Increase the version in `APP_VERSION` using `MAJOR.MINOR.PATCH` format.
3. Add the version entry to `VERSION_HISTORY.md`.
4. Run the app and analyzer tests.
5. Commit and push the branch.

Team laptops will offer the new version the next time they have internet access and launch the application.
