import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PI_DIR = ROOT / "pi"


class PiPackageTests(unittest.TestCase):
    def test_version_uses_semantic_format(self):
        version = (ROOT / "PI_LOGGER_VERSION").read_text(encoding="utf-8").strip()
        self.assertRegex(version, r"^\d+\.\d+\.\d+$")

    def test_logger_source_parses_and_exposes_version(self):
        source = (PI_DIR / "vn300_button_logger.py").read_text(encoding="utf-8")
        ast.parse(source)
        self.assertIn('"logger_version": LOGGER_VERSION', source)
        self.assertIn('Path(__file__).with_name("PI_LOGGER_VERSION")', source)

    def test_root_installer_delegates_to_pi_installer(self):
        wrapper = (ROOT / "Install_VN300_Logger.sh").read_text(encoding="utf-8")
        self.assertIn("pi/install_on_pi.sh", wrapper)
        self.assertIn('"$@"', wrapper)

    def test_installer_contains_required_safety_steps(self):
        installer = (PI_DIR / "install_on_pi.sh").read_text(encoding="utf-8")
        for expected in (
            "--check",
            "PI_LOGGER_VERSION",
            "vn300_backups",
            "motec_can_signal_map.csv.dist",
            "systemctl restart",
            'payload.get("logger_version")',
            "Restoring the previous logger",
        ):
            self.assertIn(expected, installer)

    def test_service_targets_installed_logger(self):
        service = (PI_DIR / "vn300-button-logger.service").read_text(encoding="utf-8")
        self.assertIn("User=vectornav", service)
        self.assertIn("/home/vectornav/vn300_tools/vn300_button_logger.py", service)
        self.assertIn("Restart=always", service)


if __name__ == "__main__":
    unittest.main()
