# VN300 fresh-Pi offline installation

This is a separate, self-contained installation ZIP, not the desktop updater's
source-only Pi package. It includes the committed logger, dashboard (embedded in
the logger), CAN profile/map/documentation, systemd/sudoers, 16 pinned Python
wheels, four native packages, installer, verifier, hashes and source commit ID.
No credentials, vehicle recordings or desktop build products are included.

## Supported target and prerequisites

**Raspberry Pi OS Trixie 64-bit (arm64), system Python 3.13.5**, Desktop or Lite,
on Raspberry Pi hardware. The installer explicitly rejects armhf/armv7, other
OS releases, other Python ABIs, and non-Pi systems before installing any packages. This is a
packaged compatibility target, not a claim of physical Pi validation.

Flash a stock Trixie 64-bit image; in Raspberry Pi Imager configure a login,
SSH and your local network. An internet connection is not needed on the Pi;
SSH/SCP needs a working local network. The installer creates a separate
`vectornav` service account if absent. An existing vectornav account must use
`/home/vectornav`. Core OS facilities must already exist: Python 3.13.5 (including
stdlib venv), libc6 >=2.38, libudev1 >=183, systemd, sudo, iproute2, apt/dpkg,
kmod, and dialout/gpio/netdev groups. These are stock-image prerequisites;
this bundle does not replace the OS, kernel, firmware, or bootloader.

The ARM64 packages are can-utils 2023.03-1+b2 and libusb-1.0-0 1.0.28-1 come from Debian
Trixie. liblgpio1 and python3-lgpio 0.2.2-1~rpt1+trixie come from the
Raspberry Pi Trixie repository; the binding declares Python >=3.13 and <3.14. Their complete non-base dependency
closure is included. Python closure and individual upstream URLs/SHA-256 values
are in `artifacts.lock.json` and `requirements.lock`. Only bitstruct uses an
ARM64/CPython 3.13.5 wheel; the other 15 wheels are architecture-independent.
Package license notices remain inside wheels and Debian packages.

The existing image's `kvaser_usb` kernel module must support the attached Kvaser
Leaf Light HS v2. No third-party kernel driver is installed. The official GPIO
Zero patch release is bundled; GPIO device permissions/wiring still require
on-Pi verification. See [Raspberry Pi OS documentation](https://www.raspberrypi.com/documentation/computers/os.html)
and [GPIO Zero installation](https://gpiozero.readthedocs.io/en/latest/installing.html).

## Windows PowerShell transfer

Replace the hostname and login below with your Imager settings. `ssh` and `scp`
are Windows OpenSSH commands. Do not type a password into a script.

```powershell
Set-Location 'C:\Users\14694\Documents\ChatGPT\Telemetry\dist'
$Zip = 'VN300_Offline_Trixie_arm64_py313.zip'
$Pi = 'yourlogin@raspberrypi.local'
Get-FileHash -Algorithm SHA256 $Zip
Get-Content "$Zip.sha256"
scp $Zip "$Zip.sha256" "${Pi}:~/"
ssh $Pi
```

Compare the computed hash to the delivered checksum before installation.
Alternatively copy the ZIP and checksum to USB, mount it on the Pi, then copy
both to your Pi user's home. Extracting via Python avoids requiring unzip.

## Install on the Pi

```sh
cd ~
sha256sum -c VN300_Offline_Trixie_arm64_py313.zip.sha256
python3 -m zipfile -e VN300_Offline_Trixie_arm64_py313.zip .
cd ~/VN300_Offline
python3 -B verify_manifest.py
sudo sh install.sh
sudo reboot
```

The installer uses only local `.deb` and `.whl` files: apt uses an empty package
list and `--no-download` so stale online repository indexes cannot override a
bundled package; pip has `--no-index`, hashes are mandatory, and no build
toolchain is required.
It installs into `/opt/vn300/releases/<source-commit>` and points
`/opt/vn300/current` at that release. The service runs as vectornav, uses serial
auto-discovery at 921600 baud, and has GPIO buttons enabled. Logging starts by
the button/dashboard; boot does not begin recording automatically. CAN is
opt-in. Existing logs, user profiles and mappings are never overwritten.
Systemd gives the service a private, writable `/run/vn300` working directory
for GPIO notification files; release files remain under `/opt/vn300/releases`.
The root-owned release payload uses a venv with system packages visible solely
for native lgpio; Python requirements are explicitly installed into the venv.

A re-run for the same release stops rather than merging partial directories.
If a dependency install failed, inspect `/opt/vn300/releases/<commit>` and the
service before renaming a partial directory and retrying. If final health checks
fail, the installer reports failure and leaves diagnostics accessible; it does
not claim a rollback. Previous service/sudoers files are saved beneath
`/var/backups/vn300/<timestamp>`. Custom service drop-ins remain in effect;
inspect `systemctl cat vn300-button-logger` when upgrading a customized Pi.

## Checks after reboot

```sh
sudo -u vectornav /opt/vn300/current/venv/bin/python /opt/vn300/current/verify.py
systemctl status vn300-button-logger --no-pager
journalctl -u vn300-button-logger -n 80 --no-pager
hostname -I
ls -l /dev/serial/by-id/ /dev/ttyUSB* /dev/gpiochip*
```

Open `http://raspberrypi.local:8080/` on the local network, or use the Pi IP.
API: `http://raspberrypi.local:8080/api/latest`. The dashboard is unauthenticated;
use it on the intended local network. Verification checks import/native USB,
service enabled/active, actual API version, dashboard HTML, device permissions,
GPIO access, Kvaser driver, CAN bitrate/state, candump and the API's actual
storage path with a temporary create/delete write test. It never opens serial
for acquisition, sends a CAN frame, presses a GPIO button or requests logging.
Exit 0 means no FAIL; **WARN is not a hardware pass**. Missing devices and an
unconfigured CAN interface warn. Missing software, failed HTTP/service checks,
inaccessible attached serial devices, or unwritable storage fail.

A writable USB filesystem mounted below `/media/...` or `/mnt/...` is preferred;
otherwise the logger uses `/home/vectornav/vn300_logs` on SD, reported as WARN.
On Lite, configure a persistent USB mount in `/etc/fstab` by filesystem UUID
with ownership/access for vectornav (FAT/exFAT: mount uid/gid; ext4: directory
ownership). Do not format a drive as part of installation. Restart the logger
after changing mounts, then confirm the API/verifier reports the desired path.
Five-second fsync checkpoints reduce crash loss, but do not guarantee immunity
to storage/power failures; use normal shutdown and suitable power hardware.

## Kvaser / can0 at 1 Mbit/s

Plug in the Kvaser. Configure **1,000,000 bit/s** without requiring any frames:

```sh
sudo modprobe kvaser_usb
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 1000000
sudo ip link set can0 up
ip -details -statistics link show can0
command -v candump
sudo -u vectornav /opt/vn300/current/venv/bin/python /opt/vn300/current/verify.py
```

These settings do not persist across reboot. To configure at boot and on USB
hotplug on Trixie's NetworkManager, create a dedicated systemd/udev pair:

```sh
sudo tee /etc/systemd/system/vn300-can.service >/dev/null <<'EOF'
[Unit]
Description=VN300 Kvaser CAN at 1 Mbit/s
After=sys-subsystem-net-devices-can0.device
BindsTo=sys-subsystem-net-devices-can0.device
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/ip link set can0 down
ExecStart=/usr/sbin/ip link set can0 type can bitrate 1000000
ExecStart=/usr/sbin/ip link set can0 up
EOF
sudo tee /etc/udev/rules.d/80-vn300-can.rules >/dev/null <<'EOF'
SUBSYSTEM=="net", ACTION=="add", KERNEL=="can0", TAG+="systemd", ENV{SYSTEMD_WANTS}="vn300-can.service"
EOF
sudo systemctl daemon-reload
sudo udevadm control --reload-rules
sudo systemctl start vn300-can.service
```

The logger never calls CAN send, but ordinary SocketCAN can still acknowledge
frames. Listen-only requires hardware/driver support; do not claim silent mode
unless `ip -details link show can0` confirms it. Check physical termination,
CAN-H/CAN-L, ground and the vehicle documentation before connection. No traffic
is required for installation or verification; `candump can0` can remain blank.

Enable raw CAN capture during logging sessions (no assumed decoder map):

```sh
sudo install -d /etc/systemd/system/vn300-button-logger.service.d
sudo tee /etc/systemd/system/vn300-button-logger.service.d/can.conf >/dev/null <<'EOF'
[Service]
ExecStart=
ExecStart=/opt/vn300/current/venv/bin/python /opt/vn300/current/vn300_button_logger.py --baud 921600 --can-enable --can-profile /opt/vn300/current/can_profile.example.json
EOF
sudo systemctl daemon-reload
sudo systemctl restart vn300-button-logger
```

See `pi/CAN_SETUP.md` for maps/DBC profiles. Put custom files outside the
root-owned release and point `--can-profile` at that path. No CANlib SDK is
required for Kvaser SocketCAN. Other adapters/vendor backends are not bundled.

## Troubleshooting / recovery

- Unsupported OS/architecture: use Trixie **64-bit**; do not force-install
  ARM64 packages on 32-bit or CPython 3.13.5 extensions into a different Python ABI.
- Offline apt resolution failure: the image lacks a stock baseline prerequisite
  or has conflicting/newer native packages. Nothing will be downloaded; inspect
  the apt simulation output. Reimage with the documented target rather than
  forcing a downgrade. No `apt --fix-broken` network command is required here.
- SHA mismatch: recopy/rebuild; do not bypass checksum checks.
- Service/API failure: `systemctl cat vn300-button-logger` and the journal show
  effective command/configuration. USB discovery may delay startup 20 seconds.
- Serial absence: check USB power/cable, `ls /dev/serial/by-id`, dialout membership.
  Use a stable `/dev/serial/by-id/...` path in a service override if multiple
  devices make auto-discovery ambiguous.
- GPIO warning/failure: check gpiochip permissions and gpio membership; verify
  BCM17/BCM27 wiring. Import success alone does not prove physical buttons work.
- No can0: inspect `dmesg`, `modinfo kvaser_usb`, and `/sys/bus/usb/devices`.
  Kernel/firmware mismatch cannot be repaired by Python wheels. If renamed,
  adjust channel, verifier and persistent CAN setup for the actual interface.
- SD fallback: mount storage before the service starts and allow vectornav writes.
- Restore an earlier installation: stop the service, copy its previous unit and
  sudoers from the reported backup, run `sudo visudo -c`, then daemon-reload and
  restart. Previous release folders/logs are preserved. Account/group/native
  package additions are not rolled back automatically.

## Rebuilding and validation

From a clean committed checkout with Python 3 and internet on the **build host**:

```sh
python3 packaging/build_pi_offline_trixie.py
# A later rebuild using only the previously downloaded, hashed cache:
python3 packaging/build_pi_offline_trixie.py --no-download
python3 -m zipfile -t dist/VN300_Offline_Trixie_arm64_py313.zip
```

The builder reads an explicit source allowlist from `git show HEAD:path`,
rejects tracked modifications, downloads only locked public artifacts, checks
their upstream hashes, produces a complete file manifest, records the commit,
and emits a deterministic ZIP plus external checksum. Keep generated products
under ignored `dist/`/`build/`; the delivered root ZIP/checksum are also ignored.
Tests performed on the build host do not replace a first boot on a physical Pi.
