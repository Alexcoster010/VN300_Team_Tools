"""Update-channel helpers for the VN300 native desktop application."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path
from typing import Any


GITHUB_OWNER = "Alexcoster010"
GITHUB_REPOSITORY = "VN300_Team_Tools"
UPDATE_BRANCH = "desktop-app"
VERSION_API_URL = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPOSITORY}/contents/APP_VERSION?ref={UPDATE_BRANCH}"
)
BRANCH_ARCHIVE_URL = (
    f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPOSITORY}/archive/refs/heads/{UPDATE_BRANCH}.zip"
)
MAX_ARCHIVE_BYTES = 150 * 1024 * 1024
MAX_INSTALLER_BYTES = 250 * 1024 * 1024
RELEASE_TAG_PREFIX = "desktop-v"
RELEASE_ASSET_TEMPLATE = "VN300-Team-Tools-Setup-{version}.exe"
REQUIRED_UPDATE_FILES = (
    "APP_VERSION",
    "Start_VN300_Team_Tools.bat",
    "app/vn300_desktop_app.py",
    "app/vn300_qt_app.py",
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


def _git_flags() -> int:
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def fetch_remote_version(repo_root: Path | None = None, url: str = VERSION_API_URL, timeout: float = 5.0) -> str:
    if repo_root is not None and has_git_checkout(repo_root):
        subprocess.run(
            ["git", "fetch", "--quiet", "origin", UPDATE_BRANCH],
            cwd=repo_root,
            check=True,
            timeout=max(timeout, 15.0),
            creationflags=_git_flags(),
        )
        version = subprocess.run(
            ["git", "show", "FETCH_HEAD:APP_VERSION"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
            timeout=max(timeout, 15.0),
            creationflags=_git_flags(),
        ).stdout.strip()
        parse_version(version)
        return version
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github.raw+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "VN300DesktopUpdater/0.5",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(128)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403, 404):
            raise RuntimeError(
                "The public GitHub update channel is unavailable. Check the internet connection and try again."
            ) from exc
        raise
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


def release_asset_urls(version: str) -> tuple[str, str]:
    parse_version(version)
    normalized = version.strip().removeprefix("v")
    asset = RELEASE_ASSET_TEMPLATE.format(version=normalized)
    base = (
        f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPOSITORY}/releases/download/"
        f"{RELEASE_TAG_PREFIX}{normalized}/{asset}"
    )
    return base, f"{base}.sha256"


def _download_limited(url: str, destination: Path, maximum_bytes: int, timeout: float) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "VN300DesktopUpdater/0.6"})
    with urllib.request.urlopen(request, timeout=timeout) as response, destination.open("wb") as output:
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > maximum_bytes:
            raise ValueError("The downloaded installer is unexpectedly large.")
        downloaded = 0
        while True:
            block = response.read(1024 * 1024)
            if not block:
                break
            downloaded += len(block)
            if downloaded > maximum_bytes:
                raise ValueError("The downloaded installer is unexpectedly large.")
            output.write(block)


def stage_release_installer(
    state_dir: Path,
    expected_version: str,
    installer_url: str = "",
    checksum_url: str = "",
    timeout: float = 60.0,
) -> dict[str, Any]:
    normalized = expected_version.strip().removeprefix("v")
    parse_version(normalized)
    default_installer_url, default_checksum_url = release_asset_urls(normalized)
    installer_url = installer_url or default_installer_url
    checksum_url = checksum_url or default_checksum_url
    updates_dir = state_dir / "updates"
    updates_dir.mkdir(parents=True, exist_ok=True)
    staging_root = updates_dir / f"installer_{uuid.uuid4().hex}"
    staging_root.mkdir()
    installer_path = staging_root / RELEASE_ASSET_TEMPLATE.format(version=normalized)
    checksum_path = installer_path.with_suffix(installer_path.suffix + ".sha256")
    try:
        _download_limited(installer_url, installer_path, MAX_INSTALLER_BYTES, timeout)
        _download_limited(checksum_url, checksum_path, 4096, timeout)
        checksum_text = checksum_path.read_text(encoding="ascii").strip()
        expected_hash = checksum_text.split()[0].lower() if checksum_text else ""
        if len(expected_hash) != 64 or any(character not in "0123456789abcdef" for character in expected_hash):
            raise ValueError("The installer checksum file is invalid.")
        digest = hashlib.sha256()
        with installer_path.open("rb") as installer_file:
            header = installer_file.read(2)
            digest.update(header)
            while block := installer_file.read(1024 * 1024):
                digest.update(block)
        actual_hash = digest.hexdigest()
        if actual_hash != expected_hash:
            raise ValueError("The installer checksum does not match the published release.")
        if header != b"MZ":
            raise ValueError("The downloaded update is not a Windows installer.")
        return {
            "mode": "installer",
            "installer_path": str(installer_path),
            "staging_root": str(staging_root),
            "version": normalized,
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
    installer_path: Path | None = None,
) -> None:
    helper_dir = state_dir / "updates" / f"helper_{uuid.uuid4().hex}"
    helper_dir.mkdir(parents=True, exist_ok=True)
    packaged = bool(getattr(sys, "frozen", False))
    if packaged:
        helper_source = repo_root / "VN300UpdateHelper.exe"
        helper_path = helper_dir / "VN300UpdateHelper.exe"
    else:
        helper_source = repo_root / "app" / "vn300_update_helper.py"
        helper_path = helper_dir / "vn300_update_helper.py"
    if not helper_source.is_file():
        raise FileNotFoundError(f"Update helper is missing: {helper_source}")
    shutil.copy2(helper_source, helper_path)
    command = ([str(helper_path)] if packaged else [sys.executable, "-B", str(helper_path)]) + [
        "--target",
        str(repo_root),
        "--state-dir",
        str(state_dir),
        "--pid",
        str(parent_pid),
        "--branch",
        branch,
    ]
    if installer_path is not None:
        command.extend(("--installer", str(installer_path), "--restart-executable", str(repo_root / "VN300TeamTools.exe")))
    elif source_root is not None:
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
