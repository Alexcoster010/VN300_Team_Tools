"""Version checks and SSH launcher for Raspberry Pi logger updates."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from vn300_updater import parse_version


GITHUB_OWNER = "Alexcoster010"
GITHUB_REPOSITORY = "VN300_Team_Tools"
PI_LOGGER_BRANCH = "pi-logger"
PI_VERSION_API_URL = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPOSITORY}/contents/"
    f"PI_LOGGER_VERSION?ref={PI_LOGGER_BRANCH}"
)
PI_ARCHIVE_URL = (
    f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPOSITORY}/archive/refs/heads/{PI_LOGGER_BRANCH}.zip"
)
SSH_USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_.-]{0,31}$", re.IGNORECASE)


def fetch_latest_pi_logger_version(url: str = PI_VERSION_API_URL, timeout: float = 5.0) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github.raw+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "VN300DesktopPiUpdater/0.7",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        version = response.read(128).decode("utf-8").strip()
    parse_version(version)
    return version.removeprefix("v")


def logger_update_available(installed: str, available: str) -> bool:
    available_parts = parse_version(available)
    try:
        installed_parts = parse_version(installed)
    except ValueError:
        return True
    width = max(len(installed_parts), len(available_parts))
    installed_parts += (0,) * (width - len(installed_parts))
    available_parts += (0,) * (width - len(available_parts))
    return installed_parts < available_parts


def validate_ssh_username(value: str) -> str:
    username = value.strip()
    if not SSH_USERNAME_RE.fullmatch(username):
        raise ValueError("The Pi SSH username contains unsupported characters.")
    return username


def endpoint_hostname(endpoint: str) -> str:
    parsed = urllib.parse.urlparse(endpoint)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("The saved Pi dashboard address is invalid.")
    return parsed.hostname


def build_remote_update_command(archive_url: str = PI_ARCHIVE_URL) -> str:
    quoted_url = archive_url.replace("'", "'\"'\"'")
    return (
        "set -eu; "
        "work=$(mktemp -d); "
        "trap 'rm -rf \"$work\"' EXIT HUP INT TERM; "
        "python3 -c 'import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])' "
        f"'{quoted_url}' \"$work/pi-logger.zip\"; "
        "python3 -m zipfile -e \"$work/pi-logger.zip\" \"$work\"; "
        "test -f \"$work/VN300_Team_Tools-pi-logger/Install_VN300_Logger.sh\"; "
        "sudo -v; "
        "sh \"$work/VN300_Team_Tools-pi-logger/Install_VN300_Logger.sh\""
    )


def _powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def write_ssh_update_script(
    destination: Path,
    ssh_executable: Path,
    target: str,
    remote_command: str,
    available_version: str,
) -> None:
    script = f"""$ErrorActionPreference = 'Stop'
$Host.UI.RawUI.WindowTitle = 'VN300 Pi Logger Update'
$ssh = {_powershell_literal(str(ssh_executable))}
$target = {_powershell_literal(target)}
$remoteCommand = {_powershell_literal(remote_command)}
Write-Host 'VN300 Pi Logger Update v{available_version}' -ForegroundColor Cyan
Write-Host 'Enter the Pi SSH password and sudo password if prompted.'
Write-Host ''
& $ssh -t $target $remoteCommand
$exitCode = $LASTEXITCODE
if ($exitCode -ne 0) {{
    Write-Host ''
    Write-Host "Logger update failed with exit code $exitCode." -ForegroundColor Red
    Read-Host 'Press Enter to close'
    exit $exitCode
}}
Write-Host ''
Write-Host 'Logger update completed. The desktop app will verify the Pi.' -ForegroundColor Green
Start-Sleep -Seconds 3
"""
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(script, encoding="utf-8")


def launch_pi_logger_update(
    endpoint: str,
    ssh_username: str,
    state_dir: Path,
    available_version: str,
) -> subprocess.Popen[Any]:
    if os.name != "nt":
        raise OSError("Automatic Pi logger updates currently require Windows.")
    username = validate_ssh_username(ssh_username)
    host = endpoint_hostname(endpoint)
    ssh_path = shutil.which("ssh.exe") or shutil.which("ssh")
    powershell_path = shutil.which("powershell.exe") or shutil.which("powershell")
    if not ssh_path:
        raise OSError("Windows OpenSSH Client is not installed.")
    if not powershell_path:
        raise OSError("Windows PowerShell is not available.")
    parse_version(available_version)
    script_path = state_dir / "pi_logger_updates" / f"update_{uuid.uuid4().hex}.ps1"
    write_ssh_update_script(
        script_path,
        Path(ssh_path),
        f"{username}@{host}",
        build_remote_update_command(),
        available_version,
    )
    return subprocess.Popen(
        [
            powershell_path,
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
        ],
        cwd=str(script_path.parent),
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )
