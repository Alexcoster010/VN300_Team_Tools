#!/bin/sh
set -eu

APP_DIR=/home/vectornav/vn300_tools
SERVICE=vn300-button-logger.service

sudo mkdir -p "$APP_DIR"
sudo cp vn300_button_logger.py "$APP_DIR/"
sudo chown -R vectornav:vectornav "$APP_DIR"
sudo chmod +x "$APP_DIR/vn300_button_logger.py"

sudo usermod -aG dialout,gpio vectornav 2>/dev/null || true

if ! python3 -c "import serial, gpiozero" 2>/dev/null; then
  python3 -m pip install --user -r requirements-pi.txt || {
    echo "Could not install Python packages with pip."
    echo "Try: sudo apt install -y python3-serial python3-gpiozero"
    exit 1
  }
fi

sudo cp "$SERVICE" /etc/systemd/system/
sudo cp vn300-shutdown-sudoers /etc/sudoers.d/vn300-shutdown
sudo chmod 440 /etc/sudoers.d/vn300-shutdown

sudo systemctl disable --now vn300-logger.service 2>/dev/null || true
sudo systemctl disable --now vn300-dual-logger.service 2>/dev/null || true
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE"
sudo systemctl start "$SERVICE"

systemctl status "$SERVICE" --no-pager
