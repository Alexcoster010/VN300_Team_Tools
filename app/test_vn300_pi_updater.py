import shutil
import unittest
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import vn300_pi_updater as updater


ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP_ROOT = ROOT / "build" / "test_pi_updater"


@contextmanager
def temporary_directory():
    TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    path = TEST_TEMP_ROOT / uuid.uuid4().hex
    path.mkdir()
    try:
        yield str(path)
    finally:
        shutil.rmtree(path, ignore_errors=True)


class PiUpdaterTests(unittest.TestCase):
    def test_default_version_comes_from_bundled_payload_without_urlopen(self):
        root = ROOT
        with mock.patch.object(updater, "bundled_pi_logger_root", return_value=root):
            self.assertEqual(updater.fetch_latest_pi_logger_version(), "0.6.0")

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

    def test_archive_contains_complete_linux_safe_payload(self):
        root = ROOT
        with temporary_directory() as directory:
            archive_path = Path(directory) / "logger.zip"
            version = updater.create_pi_logger_archive(archive_path, root, "0.6.0")
            self.assertEqual(version, "0.6.0")
            with zipfile.ZipFile(archive_path) as archive:
                expected = {
                    f"{updater.PAYLOAD_ARCHIVE_ROOT}/{relative}" for relative in updater.PAYLOAD_FILES
                }
                self.assertEqual(set(archive.namelist()), expected)
                install_script = archive.read(
                    f"{updater.PAYLOAD_ARCHIVE_ROOT}/pi/install_on_pi.sh"
                )
                self.assertNotIn(b"\r\n", install_script)
                self.assertIn(b"VN300_OFFLINE_INSTALL", install_script)

    def test_remote_command_installs_uploaded_archive_without_network(self):
        command = updater.build_remote_update_command("/tmp/srt-pi-logger-test.zip")
        self.assertIn("python3 -m zipfile", command)
        self.assertIn("VN300_OFFLINE_INSTALL=1", command)
        self.assertIn("sudo -v", command)
        self.assertIn("Install_VN300_Logger.sh", command)
        self.assertNotIn("github", command.lower())
        self.assertNotIn("http", command.lower())

    @unittest.skipUnless(updater.os.name == "nt", "Windows launcher test")
    def test_launcher_writes_offline_scp_script_without_storing_password(self):
        with temporary_directory() as directory:
            state = Path(directory)
            process = mock.Mock()
            with mock.patch.object(
                updater.shutil,
                "which",
                side_effect=[
                    r"C:\Windows\System32\OpenSSH\ssh.exe",
                    r"C:\Windows\System32\OpenSSH\scp.exe",
                    r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
                ],
            ), mock.patch.object(updater.subprocess, "Popen", return_value=process) as popen:
                result = updater.launch_pi_logger_update(
                    "http://192.168.1.25:8080/", "vectornav", state, "0.6.0"
                )
            self.assertIs(result, process)
            script_path = next((state / "pi_logger_updates").glob("update_*.ps1"))
            script = script_path.read_text(encoding="utf-8")
            self.assertIn("vectornav@192.168.1.25", script)
            self.assertIn("SRT Pi Logger Offline Update v0.6.0", script)
            self.assertIn("$scp", script)
            self.assertIn("No internet connection is required.", script)
            self.assertNotIn("github", script.lower())
            self.assertNotIn("password =", script.lower())
            archive_path = next((state / "pi_logger_updates").glob("payload_*.zip"))
            self.assertTrue(archive_path.is_file())
            self.assertTrue(popen.call_args.kwargs["creationflags"] & updater.subprocess.CREATE_NEW_CONSOLE)


if __name__ == "__main__":
    unittest.main()
