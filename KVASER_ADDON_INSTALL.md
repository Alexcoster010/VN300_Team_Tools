# Kvaser Leaf Light v2 offline driver add-on

This separate add-on targets only Raspberry Pi kernel `6.18.50+rpt-rpi-v8` on ARM64 and USB `0bfd:0120`. It builds and installs Kvaser's open-source SocketCAN `kvaser_usb` v1.23.358 module. It does not install CANlib, replace the kernel, modify the logger bundle, or bundle redundant headers.

The Pi must already have both matching header packages and `linux-kbuild-6.18.50+rpt`, all version `1:6.18.50-1+rpt1`. The installer verifies those packages and `/lib/modules/6.18.50+rpt-rpi-v8/build`. Matching headers already depend on the GCC backend and kbuild; the add-on bundles Debian Trixie ARM64 `make 4.4.1-2` only as an offline fallback.

Transfer and install from the repository directory:

```sh
scp dist/VN300_Kvaser_6.18.50_rpi-v8_arm64.zip dist/VN300_Kvaser_6.18.50_rpi-v8_arm64.zip.sha256 vectornav@192.168.8.134:~/
ssh vectornav@192.168.8.134
cd ~
sha256sum -c VN300_Kvaser_6.18.50_rpi-v8_arm64.zip.sha256
python3 -m zipfile -e VN300_Kvaser_6.18.50_rpi-v8_arm64.zip .
cd VN300_Kvaser_Addon
python3 -B verify_manifest.py
sudo sh install.sh
ip -details link show can0
readlink -f /sys/class/net/can0/device/driver
```

The successful installer result requires the adapter to be attached, bound to `kvaser_usb`, and `can0` to report bitrate 1000000. Bitrate configuration is runtime-only and must be repeated after reboot/hotplug.

Kernel modules are ABI-specific. Any kernel update makes this artifact unsupported; it deliberately refuses every other `uname -r`. Boot the exact kernel again or obtain matching headers and rebuild a separately validated add-on. Do not force the module into another kernel.
