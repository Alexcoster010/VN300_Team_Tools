#!/usr/bin/env python3
"""Detached helper that applies a VN300 update after the desktop app exits."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path


def wait_for_process(pid: int, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.2)
    raise TimeoutError("The running VN300 application did not close in time.")


def copy_archive_update(source: Path, target: Path, state_dir: Path) -> None:
    if not source.is_dir() or not (source / "APP_VERSION").is_file():
        raise ValueError("The staged update source is invalid.")
    backup_root = state_dir / "update_backups" / datetime.now().strftime("%Y%m%d_%H%M%S")
    for source_path in source.rglob("*"):
        relative = source_path.relative_to(source)
        if relative.parts and relative.parts[0] == ".git":
            continue
        target_path = target / relative
        if source_path.is_dir():
            target_path.mkdir(parents=True, exist_ok=True)
            continue
        if target_path.is_file():
            backup_path = backup_root / relative
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target_path, backup_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)


def run_git_update(target: Path, branch: str) -> None:
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=target,
        capture_output=True,
        text=True,
        check=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if status.stdout.strip():
        raise RuntimeError("Update stopped because tracked files have local changes.")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    subprocess.run(["git", "fetch", "origin", branch], cwd=target, check=True, creationflags=flags)
    current = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=target,
        capture_output=True,
        text=True,
        check=True,
        creationflags=flags,
    ).stdout.strip()
    if current != branch:
        subprocess.run(["git", "switch", branch], cwd=target, check=True, creationflags=flags)
    subprocess.run(["git", "pull", "--ff-only", "origin", branch], cwd=target, check=True, creationflags=flags)


def run_installer_update(installer: Path) -> None:
    if os.name != "nt" or not installer.is_file():
        raise ValueError("The staged Windows installer is missing.")
    subprocess.run(
        [
            str(installer),
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/CLOSEAPPLICATIONS",
        ],
        check=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def write_status(state_dir: Path, ok: bool, message: str) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "last_update.json"
    path.write_text(json.dumps({"ok": ok, "message": message}), encoding="utf-8")


def restart_app(target: Path, restart_executable: Path | None = None) -> None:
    if os.name == "nt" and restart_executable is not None and restart_executable.is_file():
        subprocess.Popen(
            [str(restart_executable)],
            cwd=str(restart_executable.parent),
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
            close_fds=True,
        )
        return
    launcher = target / "Start_VN300_Team_Tools.bat"
    if os.name == "nt" and launcher.is_file():
        os.startfile(launcher)  # type: ignore[attr-defined]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True, type=Path)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--branch", default="desktop-app")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--git", action="store_true")
    parser.add_argument("--installer", type=Path)
    parser.add_argument("--restart-executable", type=Path)
    args = parser.parse_args()
    try:
        wait_for_process(args.pid)
        if args.installer:
            run_installer_update(args.installer)
        elif args.git:
            run_git_update(args.target, args.branch)
        elif args.source:
            copy_archive_update(args.source, args.target, args.state_dir)
        else:
            raise ValueError("No update source was supplied.")
        write_status(args.state_dir, True, "VN300 Team Tools was updated successfully.")
    except Exception as exc:
        write_status(args.state_dir, False, f"Update failed: {exc}")
    restart_app(args.target, args.restart_executable)


if __name__ == "__main__":
    main()
