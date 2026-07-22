#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PACKAGE_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
APP_USER=${VN300_USER:-vectornav}
SERVICE=vn300-button-logger.service
SERVICE_PATH=/etc/systemd/system/$SERVICE
SUDOERS_PATH=/etc/sudoers.d/vn300-shutdown
CHECK_ONLY=0

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

[ -f "$PACKAGE_ROOT/PI_LOGGER_VERSION" ] || fail "Package file is missing: PI_LOGGER_VERSION"
[ -f "$SCRIPT_DIR/vn300_button_logger.py" ] || fail "Package file is missing: vn300_button_logger.py"
[ -f "$SCRIPT_DIR/vn300-button-logger.service" ] || fail "Package file is missing: vn300-button-logger.service"
[ -f "$SCRIPT_DIR/vn300-shutdown-sudoers" ] || fail "Package file is missing: vn300-shutdown-sudoers"
[ -f "$SCRIPT_DIR/requirements-pi.txt" ] || fail "Package file is missing: requirements-pi.txt"
[ -f "$SCRIPT_DIR/motec_can_signal_map.csv" ] || fail "Package file is missing: motec_can_signal_map.csv"

PACKAGE_VERSION=$(tr -d '[:space:]' < "$PACKAGE_ROOT/PI_LOGGER_VERSION")
case "$PACKAGE_VERSION" in
    ''|*[!0-9.]*) fail "PI_LOGGER_VERSION is invalid." ;;
esac

python3 -c 'import pathlib, sys; source=pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"); compile(source, sys.argv[1], "exec")' \
    "$SCRIPT_DIR/vn300_button_logger.py" || fail "Logger Python syntax check failed."

VISUDO=$(command -v visudo 2>/dev/null || true)
if [ -z "$VISUDO" ] && [ -x /usr/sbin/visudo ]; then
    VISUDO=/usr/sbin/visudo
fi
if [ -n "$VISUDO" ]; then
    as_root "$VISUDO" -cf "$SCRIPT_DIR/vn300-shutdown-sudoers" >/dev/null || fail "Sudoers validation failed."
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

app_python_has_dependencies() {
    if [ "$(id -un)" = "$APP_USER" ]; then
        python3 -c 'import serial, gpiozero, can' >/dev/null 2>&1
    else
        sudo -H -u "$APP_USER" python3 -c 'import serial, gpiozero, can' >/dev/null 2>&1
    fi
}

if ! app_python_has_dependencies; then
    log "Installing required Python packages."
    if command -v apt-get >/dev/null 2>&1; then
        as_root apt-get update
        as_root apt-get install -y python3-serial python3-gpiozero python3-can || true
    fi
fi

if ! app_python_has_dependencies; then
    log "System packages were unavailable; trying a user-local pip install."
    if [ "$(id -un)" = "$APP_USER" ]; then
        python3 -m pip install --user -r "$SCRIPT_DIR/requirements-pi.txt"
    else
        sudo -H -u "$APP_USER" python3 -m pip install --user -r "$SCRIPT_DIR/requirements-pi.txt"
    fi
fi

app_python_has_dependencies || fail "Required Python packages are still unavailable."

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
as_root install -o "$APP_USER" -g "$APP_USER" -m 755 "$SCRIPT_DIR/vn300_button_logger.py" "$APP_DIR/vn300_button_logger.py"
as_root install -o "$APP_USER" -g "$APP_USER" -m 644 "$PACKAGE_ROOT/PI_LOGGER_VERSION" "$APP_DIR/PI_LOGGER_VERSION"
as_root install -o "$APP_USER" -g "$APP_USER" -m 644 "$SCRIPT_DIR/requirements-pi.txt" "$APP_DIR/requirements-pi.txt"

if [ -f "$APP_DIR/motec_can_signal_map.csv" ]; then
    as_root install -o "$APP_USER" -g "$APP_USER" -m 644 "$SCRIPT_DIR/motec_can_signal_map.csv" "$APP_DIR/motec_can_signal_map.csv.dist"
    log "Preserved the installed CAN map; the package default is motec_can_signal_map.csv.dist"
else
    as_root install -o "$APP_USER" -g "$APP_USER" -m 644 "$SCRIPT_DIR/motec_can_signal_map.csv" "$APP_DIR/motec_can_signal_map.csv"
fi

as_root install -o root -g root -m 644 "$SCRIPT_DIR/$SERVICE" "$SERVICE_PATH"
as_root install -o root -g root -m 440 "$SCRIPT_DIR/vn300-shutdown-sudoers" "$SUDOERS_PATH"
as_root usermod -aG dialout,gpio,netdev "$APP_USER" 2>/dev/null || true

as_root systemctl daemon-reload
as_root systemctl enable "$SERVICE"
as_root systemctl restart "$SERVICE"

log "Waiting for the logger service and API. USB discovery can take up to 20 seconds."
service_ready=0
api_ready=0
attempt=0
while [ "$attempt" -lt 40 ]; do
    attempt=$((attempt + 1))
    if as_root systemctl is-active --quiet "$SERVICE"; then
        service_ready=1
        if python3 -c 'import json, sys, urllib.request; payload=json.load(urllib.request.urlopen("http://127.0.0.1:8080/api/latest", timeout=2)); assert isinstance(payload, dict) and payload.get("logger_version") == sys.argv[1]' "$PACKAGE_VERSION" >/dev/null 2>&1; then
            api_ready=1
            break
        fi
    fi
    sleep 1
done

if [ "$service_ready" -ne 1 ] || [ "$api_ready" -ne 1 ]; then
    log "The new service did not become healthy. Recent service output follows."
    as_root journalctl -u "$SERVICE" -n 60 --no-pager || true
    if [ -f "$BACKUP_DIR/app/vn300_button_logger.py" ]; then
        log "Restoring the previous logger and service files."
        as_root install -o "$APP_USER" -g "$APP_USER" -m 755 "$BACKUP_DIR/app/vn300_button_logger.py" "$APP_DIR/vn300_button_logger.py"
        if [ -f "$BACKUP_DIR/app/PI_LOGGER_VERSION" ]; then
            as_root install -o "$APP_USER" -g "$APP_USER" -m 644 "$BACKUP_DIR/app/PI_LOGGER_VERSION" "$APP_DIR/PI_LOGGER_VERSION"
        fi
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
log "Dashboard: http://$(hostname -I 2>/dev/null | awk '{print $1}'):8080/"
log "Reboot once if this was the first install so new group membership takes effect."
