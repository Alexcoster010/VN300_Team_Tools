"""Compile and exercise the shipped Arduino sketch using deterministic host I/O."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class AnalogFirmwareTests(unittest.TestCase):
    def test_sketch_timing_and_serial_output(self):
        compiler = shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("A C++ compiler is required for firmware host checks")
        firmware_tests = Path(__file__).resolve().parent / "firmware"
        with tempfile.TemporaryDirectory(prefix="srt-sfm3505-test-") as directory:
            binary = Path(directory) / "firmware"
            command = [compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                       "-I", str(firmware_tests / "arduino_stubs"),
                       str(firmware_tests / "test_analog_firmware.cpp"), "-o", str(binary)]
            compiled = subprocess.run(command, text=True, capture_output=True, timeout=60)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            checked = subprocess.run([str(binary)], text=True, capture_output=True, timeout=10)
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            self.assertIn("checks passed", checked.stdout)


if __name__ == "__main__":
    unittest.main()
