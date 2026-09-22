# CAN adapter and profile setup

CAN is opt-in. The logger receives frames during a VN300 logging session and never
calls `send`. Receiving alone does not guarantee hardware listen-only mode: configure
that separately for the chosen adapter. No adapter driver or bitrate is selected
automatically by the installer. The confirmed Kvaser Leaf Light HS v2 vehicle bus
bitrate is **1,000,000 bit/s**.

## Dependencies

`requirements-pi.txt` includes `python-can` (transport), `cantools` (DBC decoding),
`pyusb` (USB backend foundation), plus the existing serial and GPIO packages.
The online Pi installer attempts to install these; offline updates use packages
already installed on the Pi and report missing optional dependencies. The desktop
payload includes source/configuration files, not offline Python wheels or drivers.

On Raspberry Pi OS the installer uses `python3-can`, `python3-cantools`, and
`python3-usb` when available, with pip fallback. Check the service user's Python:

```sh
python3 -c 'import can, cantools, usb; print(can.__version__, cantools.__version__)'
```

## Adapter preparation

The selected adapter is a **Kvaser Leaf Light HS v2**. Use it through Linux
SocketCAN and python-can's `socketcan` interface. Current Linux kernels list the
Leaf Light v2 under the `kvaser_usb` driver; Kvaser also provides a SocketCAN
driver for kernels that do not recognize the device. Do not install Kvaser's
proprietary Linux CANlib package when using SocketCAN.

On the Pi, confirm the adapter and driver before enabling logging:

```sh
lsusb
sudo modprobe kvaser_usb
ip -details link show
```

Configure the interface at the confirmed vehicle bitrate, **1,000,000 bit/s**:

```sh
sudo ip link set can0 down 2>/dev/null || true
sudo ip link set can0 type can bitrate 1000000
sudo ip link set can0 up
ip -details -statistics link show can0
candump can0
```

Record the USB VID/PID (`lsusb`), firmware, Pi OS/kernel, assigned channel,
vehicle bitrate, and whether the installed driver exposes listen-only mode.

| Adapter family | Python interface | Driver preparation |
| --- | --- | --- |
| **Kvaser Leaf Light HS v2 (selected)** | `socketcan` | Use the kernel `kvaser_usb` driver and confirm it exposes `can0`. If the Pi kernel does not recognize the adapter, install Kvaser's SocketCAN driver rather than the proprietary CANlib package. Configure bitrate with `ip link` before starting the logger. |
| Linux-supported USB CAN, including suitable candleLight/PEAK devices | `socketcan` | Confirm the matching kernel driver exposes `can0`; configure bitrate and supported listen-only mode through Linux before logging. |
| Serial/SLCAN firmware | `slcan` | Use a stable `/dev/serial/by-id/...` channel, service-user serial access, and adapter-specific serial baud/bitrate options. |
| Direct gs_usb access | `gs_usb` | Requires `python-can[gs-usb]`, libusb and USB permissions in addition to this base package. Select this only after confirming firmware/driver compatibility. |
| Vendor-specific adapter | Vendor's python-can backend | Add the vendor's Pi-compatible driver/SDK and backend dependencies after identifying the model. |

Python USB packages do not install kernel drivers or vendor SDKs. Driver installers,
udev rules, and network setup should be added only for the confirmed device.
Backend options are documented in [python-can](https://python-can.readthedocs.io/en/stable/interfaces.html).

## Profiles

Copy `can_profile.example.json` to `can_profile.json` in the installed logger folder.
The installer refreshes the example and preserves your own profile and databases.
Set `interface`, `channel` (string or integer), `bitrate`, and `bus_options` for the
adapter. `bus_options` passes backend-specific keyword arguments to python-can;
it cannot override the connection fields or `ignore_config`. The logger ignores
ambient python-can configuration to keep the selected connection reproducible.
For SocketCAN, configure the actual network bitrate in Linux; a profile does not
bring up or reconfigure the network interface. Keep the example SocketCAN profile's
`bitrate` at `0`: `can0` is configured externally with `ip link` at `1000000`.

Paths in `dbc` and `signal_map` are relative to the profile file. Use `null` for raw
capture. Set `dbc` to a verified vehicle DBC for scaled numeric decoding (including
multiplexing); DBC takes precedence over CSV. CSV uses the existing
`motec_can_signal_map.csv` schema and bit conventions. MoTeC signals use
`motorola` in that schema's `byte_order` field. Do not populate production rows
until their bit offsets have been translated and verified against the logger's
Motorola start-bit convention; signedness, scaling, and live-capture values also
remain to be confirmed. Do not invent MoTeC IDs or scaling; use the actual ECU
broadcast configuration. See
[cantools decoding documentation](https://cantools.readthedocs.io/en/stable/).

```sh
python3 vn300_button_logger.py --can-enable --can-profile can_profile.json
```

Keep the normal VN300 serial/button options when adding these flags to the service
using a systemd override. A profile overrides CAN connection/decode CLI defaults;
`--can-enable` is still required. Existing `--can-interface`, `--can-channel`,
`--can-bitrate`, and `--can-signal-map` flags continue to work without a profile.
`--can-dbc vehicle.dbc` also works without a profile.

The logger retains `MOTEC_RAW_CAN.csv` and `MOTEC_CHANNELS.csv` filenames for analyzer
compatibility. Unknown IDs, error frames and remote frames remain raw-only. A
malformed payload increments decode errors without stopping raw capture. Invalid
databases or unavailable adapters appear as CAN errors while VN300 logging continues.

Before vehicle use, verify adapter enumeration, the configured 1,000,000 bit/s
bitrate/listen-only settings, raw traffic, known signal values, saved CSVs, and
restart behavior on the real Pi. Live capture must also validate Motorola
within-byte bit numbering, signed values, and scaling.
