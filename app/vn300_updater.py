"""Update-channel helpers for the VN300 native desktop application."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import uuid
import zipfile
from pathlib import Path
from typing import Any


GITHUB_OWNER = "Alexcoster010"
GITHUB_REPOSITORY = "VN300_Team_Tools"
UPDATE_BRANCH = "desktop-app"
RAW_VERSION_URL = (
    f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPOSITORY}/{UPDATE_BRANCH}/APP_VERSION"
)
BRANCH_ARCHIVE_URL = (
    f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPOSITORY}/archive/refs/heads/{UPDATE_BRANCH}.zip"
)
MAX_ARCHIVE_BYTES = 150 * 1024 * 1024
REQUIRED_UPDATE_FILES = (
    "APP_VERSION",
    "Start_VN300_Team_Tools.bat",
    "app/vn300_desktop_app.py",
    "app/vn300_update_helper.py",
)


def parse_version(value: str) -> tuple[int, ...]:
    text = value.strip().removeprefix("v")
    parts = text.split(".")
    if not parts or any(not part.isdigit() for part in parts):
        raise ValueError(f"Invalid application version: {value!r}")
    return tuple(int(part) for part in parts)


def update_available(current: str, remote: str) -> bool:
    current_parts = parse_version(current)
    remote_parts = parse_version(remote)
    width = max(len(current_parts), len(remote_parts))
    return current_parts + (0,) * (width - len(current_parts)) < remote_parts + (0,) * (width - len(remote_parts))


def read_current_version(repo_root: Path) -> str:
    try:
        value = (repo_root / "APP_VERSION").read_text(encoding="utf-8").strip()
        parse_version(value)
        return value
    except (OSError, ValueError):
        return "0.0.0"


def fetch_remote_version(url: str = RAW_VERSION_URL, timeout: float = 5.0) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "VN300DesktopUpdater/0.5"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read(128)
    version = body.decode("utf-8").strip()
    parse_version(version)
    return version


def has_git_checkout(repo_root: Path) -> bool:
    return (repo_root / ".git").exists() and shutil.which("git") is not None


def _path_is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _extract_archive_safely(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            target = destination / member.filename
            if not _path_is_within(target, destination):
                raise ValueError("The downloaded update archive contains an unsafe path.")
        bundle.extractall(destination)


def stage_branch_archive(
    state_dir: Path,
    expected_version: str = "",
    url: str = BRANCH_ARCHIVE_URL,
    timeout: float = 30.0,
) -> dict[str, Any]:
    updates_dir = state_dir / "updates"
    updates_dir.mkdir(parents=True, exist_ok=True)
    staging_root = updates_dir / f"stage_{uuid.uuid4().hex}"
    staging_root.mkdir()
    archive_path = staging_root / "desktop-app.zip"
    request = urllib.request.Request(url, headers={"User-Agent": "VN300DesktopUpdater/0.5"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, archive_path.open("wb") as output:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_ARCHIVE_BYTES:
                raise ValueError("The downloaded update is unexpectedly large.")
            downloaded = 0
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                downloaded += len(block)
                if downloaded > MAX_ARCHIVE_BYTES:
                    raise ValueError("The downloaded update is unexpectedly large.")
                output.write(block)
        extract_root = staging_root / "extracted"
        extract_root.mkdir()
        _extract_archive_safely(archive_path, extract_root)
        candidates = [path for path in extract_root.iterdir() if path.is_dir()]
        if len(candidates) != 1:
            raise ValueError("The downloaded update has an unexpected folder structure.")
        source_root = candidates[0]
        missing = [relative for relative in REQUIRED_UPDATE_FILES if not (source_root / relative).is_file()]
        if missing:
            raise ValueError(f"The downloaded update is incomplete: {', '.join(missing)}")
        staged_version = read_current_version(source_root)
        if staged_version == "0.0.0":
            raise ValueError("The downloaded update has no valid version.")
        if expected_version and parse_version(staged_version) < parse_version(expected_version):
            raise ValueError("The downloaded update is older than the version that was offered.")
        archive_path.unlink(missing_ok=True)
        return {
            "mode": "archive",
            "source_root": str(source_root),
            "staging_root": str(staging_root),
            "version": staged_version,
        }
    except Exception:
        shutil.rmtree(staging_root, ignore_errors=True)
        raise


def launch_update_helper(
    repo_root: Path,
    state_dir: Path,
    source_root: Path | None,
    parent_pid: int,
    branch: str = UPDATE_BRANCH,
) -> None:
    helper_source = repo_root / "app" / "vn300_update_helper.py"
    helper_dir = state_dir / "updates" / f"helper_{uuid.uuid4().hex}"
    helper_dir.mkdir(parents=True, exist_ok=True)
    helper_path = helper_dir / "vn300_update_helper.py"
    shutil.copy2(helper_source, helper_path)
    command = [
        sys.executable,
        "-B",
        str(helper_path),
        "--target",
        str(repo_root),
        "--state-dir",
        str(state_dir),
        "--pid",
        str(parent_pid),
        "--branch",
        branch,
    ]
    if source_root is not None:
        command.extend(("--source", str(source_root)))
    else:
        command.append("--git")
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    subprocess.Popen(
        command,
        cwd=str(helper_dir),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creationflags,
    )


def read_update_status(state_dir: Path) -> dict[str, Any] | None:
    path = state_dir / "last_update.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        path.unlink(missing_ok=True)
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None
