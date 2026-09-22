#!/usr/bin/env python3
"""Non-traffic health checks; exit 1 on FAIL, 0 on PASS/WARN. Run as vectornav."""
import glob
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request

failures = 0


def report(level, topic, message):
    global failures
    failures += level == 'FAIL'
    print(f'{level:4} {topic}: {message}')


def command(args):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=10)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)


def main():
    if os.geteuid() == 0:
        report('WARN', 'identity', 'Run as vectornav to check actual device/storage permissions.')
    for name in ['serial', 'gpiozero', 'can', 'cantools', 'usb', 'bitstruct', 'textparser',
                 'diskcache', 'argparse_addons', 'crccheck', 'colorzero', 'wrapt',
                 'packaging', 'typing_extensions', 'setuptools', 'lgpio']:
        try:
            importlib.import_module(name)
            report('PASS', 'import', name)
        except Exception as exc:
            report('FAIL', 'import', f'{name}: {exc}')
    try:
        from usb.backend import libusb1
        report('PASS' if libusb1.get_backend() else 'FAIL', 'libusb', 'native USB backend')
    except Exception as exc:
        report('FAIL', 'libusb', str(exc))
    for state in ['is-active', 'is-enabled']:
        rc, out = command(['systemctl', state, 'vn300-button-logger.service'])
        report('PASS' if rc == 0 else 'FAIL', 'service', f'{state}: {out}')
    payload = None
    for endpoint in ['/api/latest', '/']:
        try:
            with urllib.request.urlopen('http://127.0.0.1:8080' + endpoint, timeout=5) as r:
                data = r.read()
                assert r.status == 200 and data
            if endpoint == '/api/latest':
                payload = json.loads(data)
                expected = Path(__file__).with_name('PI_LOGGER_VERSION').read_text().strip()
                assert isinstance(payload, dict) and payload.get('logger_version') == expected
            else:
                assert b'<html' in data.lower() or b'<!doctype' in data.lower()
            report('PASS', 'HTTP', endpoint)
        except Exception as exc:
            report('FAIL', 'HTTP', f'{endpoint}: {exc}')
    devices = sorted(set(glob.glob('/dev/ttyUSB*') + glob.glob('/dev/ttyACM*')))
    if not devices:
        report('WARN', 'serial', 'No USB serial devices connected; VN300 acquisition cannot yet be verified.')
    for device in devices:
        report('PASS' if os.access(device, os.R_OK | os.W_OK) else 'FAIL', 'serial', device + ' read/write access')
    gpio = glob.glob('/dev/gpiochip*')
    report('PASS' if gpio and any(os.access(p, os.R_OK | os.W_OK) for p in gpio) else 'WARN',
           'GPIO', 'accessible gpiochip required for buttons; imports alone do not verify wiring')
    driver = Path('/sys/class/net/can0/device/driver')
    if driver.exists():
        report('PASS' if driver.resolve().name == 'kvaser_usb' else 'WARN', 'CAN driver', str(driver.resolve()))
    else:
        report('WARN', 'CAN driver', 'can0 Kvaser device absent; connect adapter and run sudo modprobe kvaser_usb')
    if Path('/sys/module/kvaser_usb').exists():
        report('PASS', 'kernel', 'kvaser_usb loaded')
    else:
        rc, out = command(['modinfo', 'kvaser_usb'])
        report('WARN' if rc == 0 else 'FAIL', 'kernel', 'kvaser_usb available but unloaded' if rc == 0 else out)
    rc, out = command(['ip', '-details', '-json', 'link', 'show', 'can0'])
    if rc:
        report('WARN', 'can0', 'Interface absent; no bus traffic is required for this check.')
    else:
        try:
            link = json.loads(out)[0]
            info = link.get('linkinfo', {}).get('info_data', {})
            rate = info.get('bittiming', {}).get('bitrate')
            up = 'UP' in link.get('flags', [])
            state = info.get('state', '')
            ok = up and rate == 1000000 and state not in ('BUS-OFF', 'STOPPED')
            report('PASS' if ok else 'WARN', 'can0', f'UP={up}, bitrate={rate}, state={state}; expected UP at 1000000 bit/s')
        except (ValueError, IndexError, TypeError) as exc:
            report('FAIL', 'can0', str(exc))
    report('PASS' if shutil.which('candump') else 'FAIL', 'candump', shutil.which('candump') or 'not installed')
    # Reuse actual logger status; do not guess a storage destination or start acquisition.
    if payload is not None:
        path = payload.get('base_log_dir') or payload.get('log_dir')
        if not path:
            report('FAIL', 'storage', 'API does not report storage path')
        else:
            try:
                p = Path(path)
                with tempfile.NamedTemporaryFile(prefix='.offline-check-', dir=p):
                    pass
                free = shutil.disk_usage(p).free
                external = str(p).startswith(('/media/', '/mnt/'))
                report('PASS' if external and free > 512 * 1024**2 else 'WARN', 'storage',
                       f'{p}, writable, {free // (1024**2)} MiB free; ' + ('external mount' if external else 'local SD fallback'))
            except OSError as exc:
                report('FAIL', 'storage', str(exc))
    else:
        report('FAIL', 'storage', 'No API status available to determine actual log destination')
    report('WARN', 'hardware scope', 'Serial data, GPIO wiring, CAN traffic and physical termination are not exercised.')
    print(f'RESULT: {failures} failure(s); warnings identify disconnected/unconfigured hardware.')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
