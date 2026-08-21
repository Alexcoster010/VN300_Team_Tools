"""Bundled-version checks and offline SSH launcher for Pi logger updates."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import uuid
import zipfile
from pathlib import Path
from typing import Any

from vn300_updater import parse_version


PAYLOAD_DIRECTORY = "pi_logger_payload"
PAYLOAD_ARCHIVE_ROOT = "vn300-pi-logger"
PAYLOAD_FILES = (
    "PI_LOGGER_VERSION",
    "Install_VN300_Logger.sh",
    "pi/install_on_pi.sh",
    "pi/vn300_button_logger.py",
    "pi/vn300-button-logger.service",
    "pi/vn300-shutdown-sudoers",
    "pi/requirements-pi.txt",
    "pi/motec_can_signal_map.csv",
)
SSH_USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_.-]{0,31}$", re.IGNORECASE)


def bundled_pi_logger_root() -> Path:
    candidates: list[Path] = []
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        candidates.append(Path(frozen_root) / PAYLOAD_DIRECTORY)
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / PAYLOAD_DIRECTORY)
    candidates.append(Path(__file__).resolve().parents[1])

    for root in candidates:
        if all((root / relative).is_file() for relative in PAYLOAD_FILES):
            return root
    locations = ", ".join(str(path) for path in candidates)
    raise FileNotFoundError(f"The bundled Pi logger payload is missing. Checked: {locations}")


def validate_pi_logger_payload(root: Path, expected_version: str | None = None) -> str:
    missing = [relative for relative in PAYLOAD_FILES if not (root / relative).is_file()]
    if missing:
        raise FileNotFoundError(f"The Pi logger payload is incomplete: {', '.join(missing)}")
    version = (root / "PI_LOGGER_VERSION").read_text(encoding="utf-8").strip().removeprefix("v")
    parse_version(version)
    if expected_version is not None and parse_version(version) != parse_version(expected_version):
        raise ValueError(f"Bundled Pi logger v{version} does not match requested v{expected_version}.")
    return version


def fetch_latest_pi_logger_version() -> str:
    return validate_pi_logger_payload(bundled_pi_logger_root())


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


def create_pi_logger_archive(
    destination: Path,
    payload_root: Path | None = None,
    expected_version: str | None = None,
) -> str:
    root = payload_root or bundled_pi_logger_root()
    version = validate_pi_logger_payload(root, expected_version)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in PAYLOAD_FILES:
            source = root / relative
            data = source.read_bytes().replace(b"\r\n", b"\n")
            info = zipfile.ZipInfo(f"{PAYLOAD_ARCHIVE_ROOT}/{relative}")
            mode = 0o755 if relative.endswith((".sh", ".py")) else 0o644
            info.external_attr = mode << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return version


def _shell_literal(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def build_remote_update_command(remote_archive: str) -> str:
    archive = _shell_literal(remote_archive)
    package = f'"$work/{PAYLOAD_ARCHIVE_ROOT}/Install_VN300_Logger.sh"'
    return (
        "set -eu; "
        f"archive={archive}; "
        "work=$(mktemp -d); "
        "trap 'rm -rf \"$work\" \"$archive\"' EXIT HUP INT TERM; "
        "python3 -m zipfile -e \"$archive\" \"$work\"; "
        f"test -f {package}; "
        "sudo -v; "
        f"VN300_OFFLINE_INSTALL=1 sh {package}"
    )


def _powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def write_ssh_update_script(
    destination: Path,
    ssh_executable: Path,
    scp_executable: Path,
    target: str,
    local_archive: Path,
    remote_archive: str,
    remote_command: str,
    available_version: str,
) -> None:
    script = f"""$ErrorActionPreference = 'Stop'
$Host.UI.RawUI.WindowTitle = 'SRT Pi Logger Offline Update'
$ssh = {_powershell_literal(str(ssh_executable))}
$scp = {_powershell_literal(str(scp_executable))}
$target = {_powershell_literal(target)}
$localArchive = {_powershell_literal(str(local_archive))}
$remoteArchive = {_powershell_literal(remote_archive)}
$remoteDestination = $target + ':' + $remoteArchive
$remoteCommand = {_powershell_literal(remote_command)}
Write-Host 'SRT Pi Logger Offline Update v{available_version}' -ForegroundColor Cyan
Write-Host 'No internet connection is required.'
Write-Host 'The Pi SSH password may be requested for both upload and installation.'
Write-Host ''
try {{
    Write-Host 'Uploading the bundled logger to the Pi...'
    & $scp $localArchive $remoteDestination
    if ($LASTEXITCODE -ne 0) {{
        throw "Logger upload failed with exit code $LASTEXITCODE."
    }}
    Write-Host ''
    Write-Host 'Installing and verifying the logger on the Pi...'
    & $ssh -t $target $remoteCommand
    if ($LASTEXITCODE -ne 0) {{
        throw "Logger installation failed with exit code $LASTEXITCODE."
    }}
}} catch {{
    Write-Host ''
    Write-Host $_.Exception.Message -ForegroundColor Red
    Read-Host 'Press Enter to close'
    exit 1
}} finally {{
    Remove-Item -LiteralPath $localArchive -Force -ErrorAction SilentlyContinue
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
    scp_path = shutil.which("scp.exe") or shutil.which("scp")
    powershell_path = shutil.which("powershell.exe") or shutil.which("powershell")
    if not ssh_path or not scp_path:
        raise OSError("Windows OpenSSH Client is not installed.")
    if not powershell_path:
        raise OSError("Windows PowerShell is not available.")

    update_id = uuid.uuid4().hex
    update_dir = state_dir / "pi_logger_updates"
    archive_path = update_dir / f"payload_{update_id}.zip"
    script_path = update_dir / f"update_{update_id}.ps1"
    remote_archive = f"/tmp/srt-pi-logger-{update_id}.zip"
    create_pi_logger_archive(archive_path, expected_version=available_version)
    write_ssh_update_script(
        script_path,
        Path(ssh_path),
        Path(scp_path),
        f"{username}@{host}",
        archive_path,
        remote_archive,
        build_remote_update_command(remote_archive),
        available_version,
    )
    try:
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
    except OSError:
        archive_path.unlink(missing_ok=True)
        raise
