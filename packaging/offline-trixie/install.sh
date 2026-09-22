#!/bin/sh
# Offline only: never apt update, never contact a Python package index.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || fail 'Run: sudo sh install.sh'
[ "$#" = 0 ] || fail 'Usage: sudo sh install.sh'
command -v python3 >/dev/null || fail 'System python3 is absent; reimage with Raspberry Pi OS Trixie 64-bit.'
python3 -B "$ROOT/verify_manifest.py" "$ROOT"
. /etc/os-release
ARCH=$(dpkg --print-architecture)
PY=$(python3 -c 'import platform; print(platform.python_version())')
[ "${VERSION_CODENAME:-}" = trixie ] && [ "$ARCH" = arm64 ] && [ "$PY" = 3.13.5 ] || fail "Unsupported OS/ABI: ${VERSION_CODENAME:-unknown}/$ARCH/Python $PY. This bundle requires Raspberry Pi OS Trixie 64-bit (arm64), Python 3.13.5; armhf/armv7 and other OS/ABIs need a separately built bundle. No packages installed."
[ -f /proc/device-tree/model ] && grep -q 'Raspberry Pi' /proc/device-tree/model || fail 'This installer requires Raspberry Pi hardware.'
for cmd in apt-get dpkg systemctl ip runuser visudo useradd usermod getent; do
    command -v "$cmd" >/dev/null || fail "Base OS command absent: $cmd. Use a stock Raspberry Pi OS Trixie image."
done
COMMIT=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["source_commit"])' "$ROOT/BUILD.json")
RELEASE=/opt/vn300/releases/$COMMIT
[ ! -e "$RELEASE" ] || fail "Release $RELEASE already exists. Inspect it before reinstalling; no installation changes made."
# All transitive native dependencies beyond stock Python/libc6/libudev1 are bundled.
# Simulation catches missing baseline packages without modifying the system.
apt-get --simulate --no-download --no-install-recommends --no-remove install "$ROOT"/debs/*.deb || fail 'Offline dependency preflight failed. Required baseline: Trixie python3 3.13.5, libc6 >=2.38, libudev1 >=183. No network fallback is permitted.'
visudo -cf "$ROOT/pi/vn300-shutdown-sudoers"
apt-get -y --no-download --no-install-recommends --no-remove install "$ROOT"/debs/*.deb
id vectornav >/dev/null 2>&1 || useradd --create-home --user-group --shell /bin/bash vectornav
HOME_DIR=$(getent passwd vectornav | cut -d: -f6)
[ "$HOME_DIR" = /home/vectornav ] || fail 'Existing vectornav user must have home /home/vectornav; refusing to change its account.'
for group in dialout gpio netdev; do
    getent group "$group" >/dev/null || fail "Required OS group absent: $group"
    usermod -aG "$group" vectornav
done
install -d -m 755 "$RELEASE"
python3 -m venv --without-pip --system-site-packages "$RELEASE/venv" || fail 'Stock Python venv module missing; no online bootstrap attempted.'
export PIP_CONFIG_FILE=/dev/null PIP_NO_INDEX=1 PIP_DISABLE_PIP_VERSION_CHECK=1
PYTHONPATH="$ROOT/wheels/pip-25.3-py3-none-any.whl" "$RELEASE/venv/bin/python" -m pip install \
    --no-index --only-binary=:all: --find-links "$ROOT/wheels" --ignore-installed \
    --require-hashes -r "$ROOT/requirements.lock"
"$RELEASE/venv/bin/python" -c 'import serial, gpiozero, can, cantools, usb, lgpio; import usb.backend.libusb1; assert usb.backend.libusb1.get_backend() is not None'
install -m 644 "$ROOT/pi/vn300_button_logger.py" "$ROOT/PI_LOGGER_VERSION" "$ROOT/pi/requirements-pi.txt" \
    "$ROOT/pi/motec_can_signal_map.csv" "$ROOT/pi/can_profile.example.json" "$ROOT/pi/CAN_SETUP.md" "$ROOT/verify.py" "$ROOT/BUILD.json" "$RELEASE/"
install -d -o vectornav -g "$(id -gn vectornav)" -m 755 /home/vectornav/vn300_logs
BACKUP=/var/backups/vn300/$(date +%Y%m%d_%H%M%S)
install -d -m 700 "$BACKUP"
for f in /etc/systemd/system/vn300-button-logger.service /etc/sudoers.d/vn300-shutdown; do
    [ ! -f "$f" ] || cp -a "$f" "$BACKUP/"
done
systemctl stop vn300-button-logger.service 2>/dev/null || true
systemctl disable --now vn300-logger.service vn300-dual-logger.service 2>/dev/null || true
install -m 440 "$ROOT/pi/vn300-shutdown-sudoers" /etc/sudoers.d/vn300-shutdown
cat > /etc/systemd/system/vn300-button-logger.service <<EOF
[Unit]
Description=VN300 offline logger and dashboard
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
User=vectornav
WorkingDirectory=$RELEASE
ExecStart=$RELEASE/venv/bin/python $RELEASE/vn300_button_logger.py --baud 921600
Environment=GPIOZERO_PIN_FACTORY=lgpio
Restart=on-failure
RestartSec=2
TimeoutStopSec=45
[Install]
WantedBy=multi-user.target
EOF
ln -sfn "$RELEASE" /opt/vn300/current
systemctl daemon-reload
systemctl enable --now vn300-button-logger.service
printf 'Installed %s; previous service/sudoers saved in %s\n' "$RELEASE" "$BACKUP"
printf 'Waiting for USB discovery and dashboard...\n'
sleep 25
runuser -u vectornav -- "$RELEASE/venv/bin/python" "$RELEASE/verify.py" || fail "Installation completed but verification failed. Inspect journalctl -u vn300-button-logger; backup: $BACKUP. No automatic rollback performed."
printf 'Reboot: sudo reboot\nCAN is opt-in; follow OFFLINE_INSTALL_TRIXIE.md to configure can0 at 1000000 bit/s.\n'
