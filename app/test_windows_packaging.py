import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WindowsPackagingTests(unittest.TestCase):
    def test_version_is_semantic(self):
        version = (ROOT / "APP_VERSION").read_text(encoding="utf-8").strip()
        self.assertRegex(version, r"^\d+\.\d+\.\d+$")

    def test_installer_registers_start_menu_and_uninstaller(self):
        source = (ROOT / "packaging" / "VN300TeamTools.iss").read_text(encoding="utf-8")
        self.assertIn("DefaultDirName={localappdata}", source)
        self.assertIn('Name: "{group}\\{#AppName}"', source)
        self.assertIn('Filename: "{uninstallexe}"', source)
        self.assertIn("PrivilegesRequired=lowest", source)

    def test_release_workflow_publishes_versioned_setup_and_checksum(self):
        source = (ROOT / ".github" / "workflows" / "build-desktop-installer.yml").read_text(encoding="utf-8")
        self.assertIn("desktop-v${{ steps.version.outputs.version }}", source)
        self.assertIn(".exe.sha256", source)
        self.assertIn("softprops/action-gh-release", source)

    def test_packaging_assets_exist(self):
        for relative in (
            "packaging/assets/VN300TeamTools.ico",
            "packaging/VN300TeamTools.spec",
            "packaging/VN300TeamTools.iss",
            "packaging/build_windows_installer.ps1",
        ):
            self.assertTrue((ROOT / relative).is_file(), relative)


if __name__ == "__main__":
    unittest.main()
