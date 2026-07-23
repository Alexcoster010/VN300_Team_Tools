import tempfile
import unittest
from pathlib import Path
from unittest import mock

import vn300_pi_updater as updater


class PiUpdaterTests(unittest.TestCase):
    def test_public_version_response_is_parsed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "PI_LOGGER_VERSION"
            path.write_text("0.7.1\n", encoding="utf-8")
            self.assertEqual(updater.fetch_latest_pi_logger_version(path.as_uri()), "0.7.1")

    def test_logger_update_comparison_handles_old_and_unknown_versions(self):
        self.assertTrue(updater.logger_update_available("0.5.0", "0.6.0"))
        self.assertTrue(updater.logger_update_available("unknown", "0.6.0"))
        self.assertFalse(updater.logger_update_available("0.6.0", "0.6.0"))
        self.assertFalse(updater.logger_update_available("0.7.0", "0.6.0"))

    def test_endpoint_hostname_ignores_dashboard_port(self):
        self.assertEqual(updater.endpoint_hostname("http://192.168.1.25:8080/"), "192.168.1.25")
        self.assertEqual(updater.endpoint_hostname("https://raspberrypi.local:9000/"), "raspberrypi.local")

    def test_ssh_username_validation_rejects_shell_content(self):
        self.assertEqual(updater.validate_ssh_username("vectornav"), "vectornav")
        with self.assertRaises(ValueError):
            updater.validate_ssh_username("vectornav; reboot")

    def test_remote_command_uses_public_zip_and_supported_installer(self):
        command = updater.build_remote_update_command()
        self.assertIn(updater.PI_ARCHIVE_URL, command)
        self.assertIn("python3 -m zipfile", command)
        self.assertIn("sudo -v", command)
        self.assertIn("Install_VN300_Logger.sh", command)

    @unittest.skipUnless(updater.os.name == "nt", "Windows launcher test")
    def test_launcher_writes_script_without_storing_password(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            process = mock.Mock()
            with mock.patch.object(
                updater.shutil,
                "which",
                side_effect=[r"C:\Windows\System32\OpenSSH\ssh.exe", r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"],
            ), mock.patch.object(updater.subprocess, "Popen", return_value=process) as popen:
                result = updater.launch_pi_logger_update(
                    "http://192.168.1.25:8080/", "vectornav", state, "0.6.0"
                )
            self.assertIs(result, process)
            script_path = next((state / "pi_logger_updates").glob("*.ps1"))
            script = script_path.read_text(encoding="utf-8")
            self.assertIn("vectornav@192.168.1.25", script)
            self.assertIn("VN300 Pi Logger Update v0.6.0", script)
            self.assertNotIn("password =", script.lower())
            self.assertTrue(popen.call_args.kwargs["creationflags"] & updater.subprocess.CREATE_NEW_CONSOLE)


if __name__ == "__main__":
    unittest.main()
