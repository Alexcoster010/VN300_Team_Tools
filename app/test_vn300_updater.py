import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import vn300_update_helper as helper
import vn300_updater as updater


def build_update_zip(path: Path, version: str = "0.6.0") -> None:
    files = {
        "VN300_Team_Tools-desktop-app/APP_VERSION": f"{version}\n",
        "VN300_Team_Tools-desktop-app/Start_VN300_Team_Tools.bat": "@echo off\n",
        "VN300_Team_Tools-desktop-app/app/vn300_desktop_app.py": "print('desktop')\n",
        "VN300_Team_Tools-desktop-app/app/vn300_qt_app.py": "print('qt desktop')\n",
        "VN300_Team_Tools-desktop-app/app/vn300_update_helper.py": "print('helper')\n",
    }
    with zipfile.ZipFile(path, "w") as bundle:
        for name, content in files.items():
            bundle.writestr(name, content)


class UpdaterTests(unittest.TestCase):
    def test_release_url_uses_rebranded_installer_name(self):
        installer, checksum = updater.release_asset_urls("0.12.0")
        self.assertTrue(installer.endswith("/Sooner-Racing-Telemetry-Setup-0.12.0.exe"))
        self.assertEqual(checksum, f"{installer}.sha256")

    def test_semantic_version_comparison(self):
        self.assertTrue(updater.update_available("0.5.0", "0.5.1"))
        self.assertTrue(updater.update_available("0.5.9", "0.6.0"))
        self.assertFalse(updater.update_available("0.5.0", "0.5.0"))
        self.assertFalse(updater.update_available("1.0.0", "0.9.9"))

    def test_public_version_response_is_parsed(self):
        with tempfile.TemporaryDirectory() as directory:
            version_file = Path(directory) / "APP_VERSION"
            version_file.write_text("0.6.1\n", encoding="utf-8")
            self.assertEqual(updater.fetch_remote_version(url=version_file.as_uri()), "0.6.1")

    def test_stage_branch_archive_validates_expected_files_and_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "update.zip"
            build_update_zip(archive)
            staged = updater.stage_branch_archive(root / "state", expected_version="0.6.0", url=archive.as_uri())
            source = Path(staged["source_root"])
            self.assertEqual(staged["version"], "0.6.0")
            self.assertTrue((source / "app" / "vn300_desktop_app.py").is_file())
            self.assertTrue((source / "app" / "vn300_qt_app.py").is_file())

    def test_archive_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "unsafe.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("VN300-desktop-app/../../outside.txt", "unsafe")
            with self.assertRaises(ValueError):
                updater.stage_branch_archive(root / "state", url=archive.as_uri())
            self.assertFalse((root / "outside.txt").exists())

    def test_release_installer_requires_matching_checksum(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installer = root / "setup.exe"
            installer.write_bytes(b"MZ" + b"test installer payload")
            checksum = root / "setup.exe.sha256"
            checksum.write_text(f"{hashlib.sha256(installer.read_bytes()).hexdigest()}  setup.exe\n", encoding="ascii")
            staged = updater.stage_release_installer(
                root / "state",
                "0.6.0",
                installer_url=installer.as_uri(),
                checksum_url=checksum.as_uri(),
            )
            self.assertEqual(staged["mode"], "installer")
            self.assertEqual(Path(staged["installer_path"]).read_bytes(), installer.read_bytes())

    def test_release_installer_rejects_checksum_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installer = root / "setup.exe"
            installer.write_bytes(b"MZinvalid")
            checksum = root / "setup.exe.sha256"
            checksum.write_text(f"{'0' * 64}  setup.exe\n", encoding="ascii")
            with self.assertRaisesRegex(ValueError, "checksum does not match"):
                updater.stage_release_installer(
                    root / "state",
                    "0.6.0",
                    installer_url=installer.as_uri(),
                    checksum_url=checksum.as_uri(),
                )

    def test_archive_copy_creates_backup_for_replaced_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            target = root / "target"
            state = root / "state"
            source.mkdir()
            target.mkdir()
            (source / "APP_VERSION").write_text("0.6.0\n", encoding="utf-8")
            (source / "example.txt").write_text("new\n", encoding="utf-8")
            (target / "example.txt").write_text("old\n", encoding="utf-8")
            helper.copy_archive_update(source, target, state)
            self.assertEqual((target / "example.txt").read_text(encoding="utf-8"), "new\n")
            backups = list((state / "update_backups").rglob("example.txt"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), "old\n")

    def test_update_status_is_consumed_once(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "last_update.json").write_text(json.dumps({"ok": True, "message": "done"}), encoding="utf-8")
            self.assertEqual(updater.read_update_status(state)["message"], "done")
            self.assertIsNone(updater.read_update_status(state))


if __name__ == "__main__":
    unittest.main()
