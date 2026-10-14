#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PACKAGE_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
APP_USER=${VN300_USER:-vectornav}
SERVICE=vn300-button-logger.service
SERVICE_PATH=/etc/systemd/system/$SERVICE
SUDOERS_PATH=/etc/sudoers.d/vn300-shutdown
CHECK_ONLY=0
OFFLINE_INSTALL=${VN300_OFFLINE_INSTALL:-0}
INSTALL_MODELS=${VN300_INSTALL_MODELS:-0}

if [ "${1:-}" = "--check" ]; then
    CHECK_ONLY=1
elif [ "$#" -gt 0 ]; then
    echo "Usage: $0 [--check]" >&2
    exit 2
fi

log() {
    printf '%s\n' "[VN300 installer] $*"
}

fail() {
    printf '%s\n' "[VN300 installer] ERROR: $*" >&2
    exit 1
}

as_root() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    else
        sudo "$@"
    fi
}

for file in app.py logger.py mass_flow.py analysis.py requirements.txt config/VERSION config/settings.json config/motec_can_signal_map.csv config/examples/motec_m1_reference.csv web/index.html web/app.js web/gates.js web/sensors.js web/trace.js web/style.css web/report.css web/assets/team-logo.png web/assets/team-car.jpg web/assets/ou-map.jpg web/assets/riverwind-map.jpg web/assets/map-areas.json README.md docs/SFM3505_SETUP.md arduino/analog_telemetry/analog_telemetry.ino deploy/telemetry.service deploy/vn300-shutdown-sudoers; do
    [ -f "$PACKAGE_ROOT/$file" ] || fail "Package file is missing: $file"
done

PACKAGE_VERSION=$(tr -d '[:space:]' < "$PACKAGE_ROOT/config/VERSION")
case "$PACKAGE_VERSION" in
    ''|*[!0-9.]*) fail "config/VERSION is invalid." ;;
esac

python3 - "$PACKAGE_ROOT" <<'PY'
import json
import pathlib
import sys
root = pathlib.Path(sys.argv[1])
for name in ('app.py', 'logger.py', 'mass_flow.py', 'analysis.py'):
    compile((root / name).read_text(encoding='utf-8'), name, 'exec')
settings = json.loads((root / 'config/settings.json').read_text())
if not isinstance(settings, dict):
    raise SystemExit('Settings must be a JSON object')
PY

VISUDO=$(command -v visudo 2>/dev/null || true)
if [ -z "$VISUDO" ] && [ -x /usr/sbin/visudo ]; then
    VISUDO=/usr/sbin/visudo
fi
if [ -n "$VISUDO" ]; then
    "$VISUDO" -cf "$SCRIPT_DIR/vn300-shutdown-sudoers" >/dev/null || fail "Sudoers validation failed."
fi

log "Package v$PACKAGE_VERSION passed preflight checks."
if [ "$CHECK_ONLY" -eq 1 ]; then
    exit 0
fi

command -v sudo >/dev/null 2>&1 || [ "$(id -u)" -eq 0 ] || fail "sudo is required."
id "$APP_USER" >/dev/null 2>&1 || fail "Required Pi user '$APP_USER' does not exist."
APP_HOME=$(getent passwd "$APP_USER" | cut -d: -f6)
[ -n "$APP_HOME" ] || fail "Could not determine the home folder for '$APP_USER'."
APP_DIR=${VN300_APP_DIR:-$APP_HOME/vn300_tools}
BACKUP_ROOT=$APP_HOME/vn300_backups
BACKUP_DIR=$BACKUP_ROOT/$(date +%Y%m%d_%H%M%S)_before_v$PACKAGE_VERSION

as_app_python() {
    if [ "$(id -un)" = "$APP_USER" ]; then
        python3 "$@"
    else
        sudo -H -u "$APP_USER" python3 "$@"
    fi
}

app_python_has_serial() {
    as_app_python -c 'import serial' >/dev/null 2>&1
}

app_python_has_gpiozero() {
    as_app_python -c 'import gpiozero' >/dev/null 2>&1
}

app_python_has_core_dependencies() {
    app_python_has_serial && app_python_has_gpiozero && as_app_python -c "import smbus2" >/dev/null 2>&1
}

app_python_has_can_dependency() {
    as_app_python -c 'import can' >/dev/null 2>&1
}

if ! app_python_has_core_dependencies; then
    if [ "$OFFLINE_INSTALL" = "1" ]; then
        missing=""
        app_python_has_serial || missing="$missing pyserial"
        app_python_has_gpiozero || missing="$missing gpiozero"
        as_app_python -c "import smbus2" >/dev/null 2>&1 || missing="$missing smbus2"
        fail "Required offline Python package(s) are missing:$missing. Install them while the Pi has internet access, then retry."
    fi
    log "Installing required core Python packages."
    if command -v apt-get >/dev/null 2>&1; then
        as_root apt-get update
        as_root apt-get install -y python3-serial python3-gpiozero python3-smbus2 python3-can || true
    fi
fi

if ! app_python_has_core_dependencies; then
    log "System packages were unavailable; trying a user-local pip install."
    if [ "$(id -un)" = "$APP_USER" ]; then
        python3 -m pip install --user pyserial gpiozero smbus2
    else
        sudo -H -u "$APP_USER" python3 -m pip install --user pyserial gpiozero smbus2
    fi
fi

app_python_has_core_dependencies || fail "Required core Python packages are still unavailable."

if ! app_python_has_can_dependency; then
    if [ "$OFFLINE_INSTALL" = "1" ]; then
        log "python-can is not installed; VN300 logging and buttons will work, but CAN logging remains disabled."
    else
        log "Installing the optional python-can package."
        if command -v apt-get >/dev/null 2>&1; then
            as_root apt-get update
            as_root apt-get install -y python3-can || true
        fi
        if ! app_python_has_can_dependency; then
            if [ "$(id -un)" = "$APP_USER" ]; then
                python3 -m pip install --user python-can || true
            else
                sudo -H -u "$APP_USER" python3 -m pip install --user python-can || true
            fi
        fi
        if ! app_python_has_can_dependency; then
            log "python-can is still unavailable; CAN logging remains disabled."
        fi
    fi
fi

# Install decoding/USB foundations independently so an unavailable optional package
# cannot prevent VN300 serial/GPIO operation or the other CAN packages installing.
for dependency in 'cantools:python3-cantools:cantools' 'usb:python3-usb:pyusb'; do
    module=${dependency%%:*}
    packages=${dependency#*:}
    apt_package=${packages%%:*}
    pip_package=${packages#*:}
    if ! as_app_python -c "import $module" >/dev/null 2>&1; then
        if [ "$OFFLINE_INSTALL" != "1" ]; then
            if command -v apt-get >/dev/null 2>&1; then
                as_root apt-get update
                as_root apt-get install -y "$apt_package" || true
            fi
            if ! as_app_python -c "import $module" >/dev/null 2>&1; then
                as_app_python -m pip install --user "$pip_package" || true
            fi
        fi
        if ! as_app_python -c "import $module" >/dev/null 2>&1; then
            log "Optional CAN dependency $pip_package unavailable; install it before using its CAN features."
        fi
    fi
done

# Models are optional; ordinary analysis uses the standard library only.
if [ "$INSTALL_MODELS" = "1" ]; then
    for module in numpy pandas scipy matplotlib; do
        if ! as_app_python -c "import $module" >/dev/null 2>&1; then
            if [ "$OFFLINE_INSTALL" != "1" ]; then
                if command -v apt-get >/dev/null 2>&1; then
                    as_root apt-get install -y "python3-$module" || true
                fi
                if ! as_app_python -c "import $module" >/dev/null 2>&1; then
                    as_app_python -m pip install --user "$module" || true
                fi
            fi
            if ! as_app_python -c "import $module" >/dev/null 2>&1; then
                log "Optional model dependency $module unavailable; Models will show this requirement."
            fi
        fi
    done
fi

log "Backing up the currently installed logger to $BACKUP_DIR"
as_root install -d -o "$APP_USER" -g "$APP_USER" -m 755 "$BACKUP_DIR"
if [ -d "$APP_DIR" ]; then
    as_root cp -a "$APP_DIR" "$BACKUP_DIR/app"
fi
if [ -f "$SERVICE_PATH" ]; then
    as_root cp -a "$SERVICE_PATH" "$BACKUP_DIR/$SERVICE"
fi
if [ -f "$SUDOERS_PATH" ]; then
    as_root cp -a "$SUDOERS_PATH" "$BACKUP_DIR/vn300-shutdown-sudoers"
fi

log "Stopping the current service."
as_root systemctl stop "$SERVICE" 2>/dev/null || true
as_root systemctl disable --now vn300-logger.service 2>/dev/null || true
as_root systemctl disable --now vn300-dual-logger.service 2>/dev/null || true

log "Installing VN300 logger v$PACKAGE_VERSION to $APP_DIR"
as_root install -d -o "$APP_USER" -g "$APP_USER" -m 755 "$APP_DIR"
for file in app.py logger.py mass_flow.py analysis.py requirements.txt README.md; do
    as_root install -o "$APP_USER" -g "$APP_USER" -m 644 "$PACKAGE_ROOT/$file" "$APP_DIR/$file"
done
as_root install -d -o "$APP_USER" -g "$APP_USER" -m 755 "$APP_DIR/web" "$APP_DIR/config" "$APP_DIR/config/examples"
as_root install -d -o "$APP_USER" -g "$APP_USER" -m 755 "$APP_DIR/docs" "$APP_DIR/arduino" "$APP_DIR/arduino/analog_telemetry"
as_root install -o "$APP_USER" -g "$APP_USER" -m 644 "$PACKAGE_ROOT/docs/SFM3505_SETUP.md" "$APP_DIR/docs/SFM3505_SETUP.md"
as_root install -o "$APP_USER" -g "$APP_USER" -m 644 "$PACKAGE_ROOT/arduino/analog_telemetry/analog_telemetry.ino" "$APP_DIR/arduino/analog_telemetry/analog_telemetry.ino"
for file in index.html app.js gates.js sensors.js trace.js style.css report.css; do
    as_root install -o "$APP_USER" -g "$APP_USER" -m 644 "$PACKAGE_ROOT/web/$file" "$APP_DIR/web/$file"
done
as_root install -d -o "$APP_USER" -g "$APP_USER" -m 755 "$APP_DIR/web/assets" "$APP_DIR/archive" "$APP_DIR/archive/docs"
for file in "$PACKAGE_ROOT"/web/assets/*; do
    as_root install -o "$APP_USER" -g "$APP_USER" -m 644 "$file" "$APP_DIR/web/assets/$(basename "$file")"
done
# Keep historical references locally available without replacing installed archives.
for file in "$PACKAGE_ROOT"/archive/docs/*.md; do
    [ -f "$file" ] || continue
    if [ ! -f "$APP_DIR/archive/docs/$(basename "$file")" ]; then
        as_root install -o "$APP_USER" -g "$APP_USER" -m 644 "$file" "$APP_DIR/archive/docs/$(basename "$file")"
    fi
done
for file in "$PACKAGE_ROOT"/config/examples/*; do
    as_root install -o "$APP_USER" -g "$APP_USER" -m 644 "$file" "$APP_DIR/config/examples/$(basename "$file")"
done
as_root install -o "$APP_USER" -g "$APP_USER" -m 644 "$PACKAGE_ROOT/config/VERSION" "$APP_DIR/config/VERSION"
if [ ! -f "$APP_DIR/config/settings.json" ]; then
    as_root install -o "$APP_USER" -g "$APP_USER" -m 644 "$PACKAGE_ROOT/config/settings.json" "$APP_DIR/config/settings.json"
fi
# Move legacy settings to the config directory without overwriting newer ones.
for file in steering_calibration.json airspeed_calibration.json can_profile.json motec_can_signal_map.csv; do
    if [ -f "$APP_DIR/$file" ] && [ ! -f "$APP_DIR/config/$file" ]; then
        as_root install -o "$APP_USER" -g "$APP_USER" -m 644 "$APP_DIR/$file" "$APP_DIR/config/$file"
    fi
done
if [ -f "$APP_DIR/config/motec_can_signal_map.csv" ]; then
    as_root install -o "$APP_USER" -g "$APP_USER" -m 644 "$PACKAGE_ROOT/config/motec_can_signal_map.csv" "$APP_DIR/config/motec_can_signal_map.csv.dist"
    log "Preserved the installed CAN map; the package default is motec_can_signal_map.csv.dist"
else
    as_root install -o "$APP_USER" -g "$APP_USER" -m 644 "$PACKAGE_ROOT/config/motec_can_signal_map.csv" "$APP_DIR/config/motec_can_signal_map.csv"
fi

# Keep the existing service identity so upgrades do not create two recorders.
# Carry forward configured command-line flags from the old service.
GENERATED_SERVICE=$(mktemp)
trap 'rm -f "$GENERATED_SERVICE"' EXIT HUP INT TERM
python3 - "$SCRIPT_DIR/telemetry.service" "$GENERATED_SERVICE" "$APP_USER" "$APP_DIR" "$BACKUP_DIR/$SERVICE" <<'PY'
import json
from pathlib import Path
import shlex
import sys
template, destination, user, app_dir, previous = sys.argv[1:]
arguments = []
if Path(previous).exists():
    for line in Path(previous).read_text().splitlines():
        if line.startswith('ExecStart='):
            words = shlex.split(line.split('=', 1)[1])
            for index, word in enumerate(words):
                if Path(word).name in ('vn300_button_logger.py', 'app.py'):
                    arguments = words[index + 1:]
                    break
command = ['/usr/bin/python3', str(Path(app_dir) / 'app.py'), *arguments]
lines = []
for line in Path(template).read_text().splitlines():
    if line.startswith('User='):
        line = 'User=' + user
    elif line.startswith('WorkingDirectory='):
        line = 'WorkingDirectory=' + app_dir.replace('%', '%%')
    elif line.startswith('ExecStart='):
        line = 'ExecStart=' + ' '.join(json.dumps(word) for word in command)
    lines.append(line)
Path(destination).write_text('\n'.join(lines) + '\n')
PY
as_root install -o root -g root -m 644 "$GENERATED_SERVICE" "$SERVICE_PATH"
as_root install -o root -g root -m 440 "$SCRIPT_DIR/vn300-shutdown-sudoers" "$SUDOERS_PATH"

as_root usermod -aG dialout,gpio,netdev "$APP_USER" 2>/dev/null || true
if getent group i2c >/dev/null 2>&1; then
    as_root usermod -aG i2c "$APP_USER"
else
    log "I2C group is unavailable; check /dev/i2c-1 permissions before using mass flow."
fi
if as_app_python - "$APP_DIR/config/settings.json" "$GENERATED_SERVICE" <<'PYI2C'
import json
from pathlib import Path
import shlex
import sys
settings = json.loads(Path(sys.argv[1]).read_text())
flags = []
for line in Path(sys.argv[2]).read_text().splitlines():
    if line.startswith('ExecStart='):
        flags = shlex.split(line.split('=', 1)[1])
enabled = settings.get('mass_flow_enable', settings.get('arduino_format') == 'a0-a1-sfm3505')
legacy = '--arduino-format=a0-a1-sfm3505' in flags or any(
    flag == '--arduino-format' and value == 'a0-a1-sfm3505' for flag, value in zip(flags, flags[1:]))
sys.exit(0 if enabled or legacy or '--mass-flow-enable' in flags else 1)
PYI2C
then
    if command -v raspi-config >/dev/null 2>&1; then
        as_root raspi-config nonint do_i2c 0
        as_root modprobe i2c-dev
    else
        log "Enable the Pi I2C interface before using mass flow; see docs/SFM3505_SETUP.md."
    fi
fi

as_root systemctl daemon-reload
as_root systemctl enable "$SERVICE"
if ! as_root systemctl restart "$SERVICE"; then
    log "The logger could not start; continuing to its health check and rollback."
fi

DASHBOARD_PORT=$(python3 - "$APP_DIR/config/settings.json" "$GENERATED_SERVICE" "$APP_DIR" <<'PY'
import json
from pathlib import Path
import shlex
import sys
settings_path, service_path, app_dir = sys.argv[1:]
settings = json.loads(Path(settings_path).read_text())
arguments = []
for line in Path(service_path).read_text().splitlines():
    if line.startswith('ExecStart='):
        arguments = shlex.split(line.split('=', 1)[1])[2:]
for index, argument in enumerate(arguments):
    if argument == '--config':
        config = Path(arguments[index + 1])
        if not config.is_absolute():
            config = Path(app_dir) / config
        settings = json.loads(config.read_text())
    elif argument.startswith('--config='):
        config = Path(argument.split('=', 1)[1])
        if not config.is_absolute():
            config = Path(app_dir) / config
        settings = json.loads(config.read_text())
port = settings.get('dashboard_port', 8080)
for index, argument in enumerate(arguments):
    if argument == '--dashboard-port':
        port = int(arguments[index + 1])
    elif argument.startswith('--dashboard-port='):
        port = int(argument.split('=', 1)[1])
print(port)
PY
)
log "Waiting for the logger service and API. USB discovery can take up to 20 seconds."
service_ready=0
api_ready=0
attempt=0
while [ "$attempt" -lt 40 ]; do
    attempt=$((attempt + 1))
    if as_root systemctl is-active --quiet "$SERVICE"; then
        service_ready=1
        if python3 -c 'import json, sys, urllib.request; payload=json.load(urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[2]}/api/latest", timeout=2)); assert isinstance(payload, dict) and payload.get("logger_version") == sys.argv[1]' "$PACKAGE_VERSION" "$DASHBOARD_PORT" >/dev/null 2>&1; then
            api_ready=1
            break
        fi
    fi
    sleep 1
done

if [ "$service_ready" -ne 1 ] || [ "$api_ready" -ne 1 ]; then
    log "The new service did not become healthy. Recent service output follows."
    as_root journalctl -u "$SERVICE" -n 60 --no-pager || true
    if [ -d "$BACKUP_DIR/app" ]; then
        log "Restoring the previous logger and service files."
        as_root cp -a "$BACKUP_DIR/app/." "$APP_DIR/"
        if [ -f "$BACKUP_DIR/$SERVICE" ]; then
            as_root install -o root -g root -m 644 "$BACKUP_DIR/$SERVICE" "$SERVICE_PATH"
        fi
        if [ -f "$BACKUP_DIR/vn300-shutdown-sudoers" ]; then
            as_root install -o root -g root -m 440 "$BACKUP_DIR/vn300-shutdown-sudoers" "$SUDOERS_PATH"
        fi
        as_root systemctl daemon-reload
        as_root systemctl restart "$SERVICE" || true
    fi
    fail "Installation failed its health check. The previous logger was restored when a backup was available."
fi

log "VN300 logger v$PACKAGE_VERSION installed successfully."
log "Backup: $BACKUP_DIR"
as_root systemctl status "$SERVICE" --no-pager --full || true
log "Dashboard: http://$(hostname -I 2>/dev/null | awk '{print $1}'):$DASHBOARD_PORT/"
log "Reboot once if this was the first install so new group membership takes effect."
