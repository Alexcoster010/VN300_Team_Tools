#!/bin/sh
# Installs only Kvaser's open-source kvaser_usb SocketCAN module; no CANlib.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
KERNEL=6.18.50+rpt-rpi-v8
HEADER_VERSION='1:6.18.50-1+rpt1'
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || fail 'Run: sudo sh install.sh'
[ "$#" = 0 ] || fail 'Usage: sudo sh install.sh'
command -v python3 >/dev/null || fail 'python3 is required to verify the bundle manifest.'
python3 -B "$ROOT/verify_manifest.py" "$ROOT" || fail 'Bundle integrity verification failed.'
[ "$(uname -r)" = "$KERNEL" ] || fail "Wrong running kernel: $(uname -r); required exactly $KERNEL. Nothing installed."
[ "$(uname -m)" = aarch64 ] || fail "Wrong machine architecture: $(uname -m); required aarch64."
[ "$(dpkg --print-architecture)" = arm64 ] || fail 'Debian architecture must be arm64.'
for spec in "linux-headers-6.18.50+rpt-common-rpi:$HEADER_VERSION" "linux-headers-6.18.50+rpt-rpi-v8:$HEADER_VERSION" "linux-kbuild-6.18.50+rpt:$HEADER_VERSION"; do
    pkg=${spec%%:*}; version=${spec#*:}
    installed=$(dpkg-query -W -f='${Status}|${Version}|${Architecture}' "$pkg" 2>/dev/null || true)
    case "$installed" in "install ok installed|$version|"*) ;; *) fail "Required installed package missing/wrong: $pkg $version (found: ${installed:-absent}). Install matching headers; this add-on deliberately does not bundle them.";; esac
done
[ -e "/lib/modules/$KERNEL/build/Makefile" ] || fail "Header build link is absent: /lib/modules/$KERNEL/build"
if ! command -v make >/dev/null; then
    dpkg-deb -f "$ROOT/debs/make_4.4.1-2_arm64.deb" Package Version Architecture | grep -qx 'make 4.4.1-2 arm64' || fail 'Bundled make package metadata mismatch.'
    apt-get -y --no-download --no-install-recommends --no-remove install "$ROOT/debs/make_4.4.1-2_arm64.deb" || fail 'Offline installation of make failed.'
fi
command -v make >/dev/null || fail 'make is unavailable after offline prerequisite installation.'
CC=
for candidate in gcc gcc-14 aarch64-linux-gnu-gcc-14; do command -v "$candidate" >/dev/null 2>&1 && CC=$candidate && break; done
[ -n "$CC" ] || fail 'No C compiler found. Matching headers normally install gcc-14-for-host and aarch64-linux-gnu-gcc-14.'
for cmd in tar depmod modprobe ip; do command -v "$cmd" >/dev/null || fail "Required base command absent: $cmd"; done
WORK=$(mktemp -d /tmp/vn300-kvaser.XXXXXX)
trap 'rm -rf "$WORK"' EXIT HUP INT TERM
tar -xzf "$ROOT/source/socketcan_kvaser_drivers_1_23_358.tar.gz" -C "$WORK"
SRC=$WORK/socketcan_kvaser_drivers/kvaser_usb
[ -f "$SRC/drivers/net/can/usb/kvaser_usb/kvaser_usb_core.c" ] || fail 'Unexpected Kvaser source layout.'
grep -q 'USB_LEAF_LITE_V2_PRODUCT_ID 0x0120' "$SRC/drivers/net/can/usb/kvaser_usb/kvaser_usb_core.c" || fail 'Source does not declare Leaf Light v2 USB product 0120.'
make -C "$SRC" KERNEL_PATH="/lib/modules/$KERNEL" CC="$CC" clean all
find "$SRC" -name 'kvaser_usb.ko' -type f | grep -q . || fail 'Build produced no kvaser_usb.ko.'
modprobe -r kvaser_usb 2>/dev/null || true
make -C "$SRC" KERNEL_PATH="/lib/modules/$KERNEL" CC="$CC" modules_install
depmod -a "$KERNEL"
modprobe kvaser_usb
FOUND=0
for dev in /sys/bus/usb/devices/*; do
    [ -r "$dev/idVendor" ] && [ -r "$dev/idProduct" ] || continue
    [ "$(cat "$dev/idVendor")" = 0bfd ] && [ "$(cat "$dev/idProduct")" = 0120 ] || continue
    FOUND=1; BOUND=0
    for intf in "$dev":*; do [ -L "$intf/driver" ] && [ "$(basename "$(readlink -f "$intf/driver")")" = kvaser_usb ] && BOUND=1; done
    [ "$BOUND" = 1 ] || fail 'Kvaser 0bfd:0120 is present but not bound to kvaser_usb; inspect dmesg.'
done
[ "$FOUND" = 1 ] || fail 'Kvaser Leaf Light v2 USB 0bfd:0120 is not attached.'
i=0; while [ ! -e /sys/class/net/can0 ] && [ "$i" -lt 10 ]; do sleep 1; i=$((i+1)); done
[ -e /sys/class/net/can0 ] || fail 'Driver bound, but can0 did not appear.'
[ -L /sys/class/net/can0/device/driver ] && [ "$(basename "$(readlink -f /sys/class/net/can0/device/driver)")" = kvaser_usb ] || fail 'can0 is not owned by kvaser_usb.'
ip link set can0 down 2>/dev/null || true
ip link set can0 type can bitrate 1000000
ip link set can0 up
ip -details link show can0 | grep -q 'bitrate 1000000' || fail 'can0 bitrate verification failed.'
modinfo -F filename kvaser_usb | grep -q "/lib/modules/$KERNEL/updates/" || fail 'Loaded module is not the installed Kvaser update module.'
printf 'PASS: Kvaser 0bfd:0120 bound to kvaser_usb; can0 is UP at 1000000 bit/s.\n'
