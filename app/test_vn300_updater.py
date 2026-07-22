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
        "VN300_Team_Tools-desktop-app/app/vn300_update_helper.py": "print('helper')\n",
    }
    with zipfile.ZipFile(path, "w") as bundle:
        for name, content in files.items():
            bundle.writestr(name, content)


class UpdaterTests(unittest.TestCase):
    def test_semantic_version_comparison(self):
        self.assertTrue(updater.update_available("0.5.0", "0.5.1"))
        self.assertTrue(updater.update_available("0.5.9", "0.6.0"))
        self.assertFalse(updater.update_available("0.5.0", "0.5.0"))
        self.assertFalse(updater.update_available("1.0.0", "0.9.9"))

    def test_stage_branch_archive_validates_expected_files_and_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "update.zip"
            build_update_zip(archive)
            staged = updater.stage_branch_archive(root / "state", expected_version="0.6.0", url=archive.as_uri())
            source = Path(staged["source_root"])
            self.assertEqual(staged["version"], "0.6.0")
            self.assertTrue((source / "app" / "vn300_desktop_app.py").is_file())

    def test_archive_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "unsafe.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("VN300-desktop-app/../../outside.txt", "unsafe")
            with self.assertRaises(ValueError):
                updater.stage_branch_archive(root / "state", url=archive.as_uri())
            self.assertFalse((root / "outside.txt").exists())

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
