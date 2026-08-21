import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WindowsPackagingTests(unittest.TestCase):
    def test_version_is_semantic(self):
        version = (ROOT / "APP_VERSION").read_text(encoding="utf-8").strip()
        self.assertRegex(version, r"^\d+\.\d+\.\d+$")

    def test_installer_registers_start_menu_and_uninstaller(self):
        source = (ROOT / "packaging" / "VN300TeamTools.iss").read_text(encoding="utf-8")
        self.assertIn('#define AppName "Sooner Racing Telemetry"', source)
        self.assertIn("DefaultDirName={localappdata}\\Programs\\Sooner Racing Telemetry", source)
        self.assertIn('Name: "{userprograms}\\{#AppName}\\{#AppName}"', source)
        self.assertIn('IconFilename: "{app}\\SoonerRacingTelemetry.ico"', source)
        self.assertIn('Filename: "{uninstallexe}"', source)
        self.assertIn("PrivilegesRequired=lowest", source)
        self.assertIn("AppId={{C2FC304A-8BA5-41B2-B7C7-3BA0BFE08F32}", source)
        self.assertIn("CloseApplications=force", source)

    def test_release_workflow_publishes_versioned_setup_and_checksum(self):
        source = (ROOT / ".github" / "workflows" / "build-desktop-installer.yml").read_text(encoding="utf-8")
        self.assertIn("desktop-v${{ steps.version.outputs.version }}", source)
        self.assertIn("Sooner-Racing-Telemetry-Setup-${{ steps.version.outputs.version }}", source)
        self.assertIn("VN300-Team-Tools-Setup-${{ steps.version.outputs.version }}", source)
        self.assertIn(".exe.sha256", source)
        self.assertIn("softprops/action-gh-release", source)

    def test_packaging_assets_exist(self):
        for relative in (
            "packaging/assets/SoonerRacingTelemetry.ico",
            "packaging/assets/SoonerRacingTelemetry.png",
            "packaging/generate_brand_assets.py",
            "packaging/VN300TeamTools.spec",
            "packaging/VN300TeamTools.iss",
            "packaging/build_windows_installer.ps1",
            "Start_Sooner_Racing_Telemetry.bat",
        ):
            self.assertTrue((ROOT / relative).is_file(), relative)

    def test_installer_build_uses_qt_desktop_shell(self):
        spec = (ROOT / "packaging" / "VN300TeamTools.spec").read_text(encoding="utf-8")
        requirements = (ROOT / "packaging" / "requirements-build.txt").read_text(encoding="utf-8")
        launcher = (ROOT / "Start_Sooner_Racing_Telemetry.bat").read_text(encoding="utf-8")
        self.assertIn('APP_DIR / "vn300_qt_app.py"', spec)
        self.assertIn('"SoonerRacingTelemetry.ico"', spec)
        self.assertIn("PySide6", requirements)
        self.assertIn("Pillow", requirements)
        self.assertIn(r"app\vn300_qt_app.py", launcher)
        self.assertIn("Sooner Racing Telemetry", launcher)

    def test_build_keeps_legacy_installer_alias_for_existing_updaters(self):
        source = (ROOT / "packaging" / "build_windows_installer.ps1").read_text(encoding="utf-8")
        self.assertIn('"Sooner-Racing-Telemetry-Setup-$Version.exe"', source)
        self.assertIn('"VN300-Team-Tools-Setup-$Version.exe"', source)

    def test_desktop_palette_matches_srt_brand(self):
        qt_source = (ROOT / "app" / "vn300_qt_app.py").read_text(encoding="utf-8").lower()
        web_source = (ROOT / "app" / "static" / "app.css").read_text(encoding="utf-8").lower()
        for source in (qt_source, web_source):
            self.assertIn("#d71920", source)
            self.assertIn("#080808", source)
            self.assertNotIn("#00877f", source)
            self.assertNotIn("--teal", source)
        self.assertIn("qmessagebox qlabel", qt_source)


if __name__ == "__main__":
    unittest.main()
