# SFM3505 mass flow over Raspberry Pi I2C

Connect the Sensirion SFM3505-300 / SFM3505-300-X directly to the Pi.
The Uno keeps steering on A0 and analog airspeed on A1. Mass flow has its own
reader, live status, and CSV; it does not require an Arduino port or calibration.

## Wiring

Power down before rewiring. Verify sensor connector orientation against the
[manufacturer's datasheet](https://sensirion.com/resource/datasheet/sfm3505).

| Sensor pin | Signal | Pi 40-pin header |
| --- | --- | --- |
| 1 | SDA | GPIO 2 / physical pin 3 |
| 2 | VDD | 3.3 V / physical pin 1 |
| 3 | GND | Ground / physical pin 6 |
| 4 | SCL | GPIO 3 / physical pin 5 |
| 5 | DRDY | Leave unconnected; software polls |

Use a regulated 3.3 V supply (sensor working range 3.2–3.4 V) and pull-ups to
3.3 V. The Pi's primary I2C pins already have pull-ups; check any breakout's
additional pull-ups. No Uno level shifter is needed with this 3.3 V connection.
Do not connect SDA/SCL or sensor power to the Uno's 5 V rail. Check cable length,
grounding, and bus signal quality on the vehicle.

## Enable Pi I2C

```sh
sudo raspi-config nonint do_i2c 0
sudo modprobe i2c-dev
ls -l /dev/i2c-1
```

The installer installs `smbus2`, adds the service user to `i2c` when that group
exists, and enables the interface with `raspi-config` when mass flow is enabled
in saved settings or service flags. Reboot if required for interface or group changes. On a custom
OS, ensure the service user can read/write the selected `/dev/i2c-N` device.
The sensor's fixed seven-bit address is `0x2E`.

## Configure acquisition

In Setup → Device settings, enable **Pi I²C mass flow**, select bus **1**, and
set **50 Hz**. The shipped settings already include:

```json
{
  "mass_flow_enable": true,
  "mass_flow_i2c_bus": 1,
  "mass_flow_sample_hz": 50
}
```

Restart `vn300-button-logger.service` after saving device settings. Existing
installed settings are preserved by upgrades; enable mass flow there if needed.
Bus numbers must be nonnegative integers; polling supports 1–1000 Hz. Bus clock
speed is configured by the OS, separately from the polling rate.

For Uno A0/A1 input, upload `arduino/analog_telemetry/analog_telemetry.ino`, select
`arduino_format: "a0-a1-volts"`, and retain the Arduino serial path, 115200 baud,
steering calibration, and airspeed calibration. Serial output is exactly
`A0_voltage,A1_voltage` per line. The old `a0-a1-sfm3505` saved setting migrates
to `a0-a1-volts` and enables Pi mass flow unless explicitly disabled. Upload the
new analog sketch as part of that upgrade; old five-field firmware is rejected.
Legacy `--arduino-format a0-a1-sfm3505` service flags also migrate automatically.

Command-line equivalents:

```sh
python3 app.py --mass-flow-enable --mass-flow-i2c-bus 1 --mass-flow-sample-hz 50
```

`--web-only` serves the panel without opening hardware.

## Live data and recording

Sensors displays air/O2 calibrated flow, raw readings, range, last valid sample
age, I2C device information, and errors. `/api/latest.mass_flow` is independent
of `/api/latest.steering`. Readings stream whenever the configured service runs,
including while idle and during setup. Start/Stop Log and GPIO recording controls
attach/detach mass flow capture with the other run files.

Each run writes `<run-prefix>_MASS_FLOW.csv`, with the common
`Pi_Logger_Elapsed_Time_s` origin and `System_Time_ISO`, followed by:

```text
Mass_Flow_Status,Mass_Flow_Air_Raw,Mass_Flow_O2_Raw,Mass_Flow_Air_slm,Mass_Flow_O2_slm,Mass_Flow_Range
```

Fault/warm-up rows have blank flow fields. Session metadata stores the independent
`mass_flow` snapshot, including bus, address, scale, reference conditions,
successful sample counts, recording counts, retries, and errors. Stopping a run
flushes and fsyncs the CSV; idle sensor reads continue.

The reader sends stop (`0x3FF9`), waits at least 1 ms, starts continuous measurement
(`0x3603`), and waits 50 ms before reading. Each raw nine-byte read contains three
CRC-protected words spanning the two unsigned 24-bit values. CRC-8 uses polynomial
`0x31` and initial value `0xFF`. Conversion is `(raw - 8388608) / 25600` slm,
referenced to 20 °C and 1013.25 hPa; nominal range is −150 to +300 slm.
Air and O2 are alternative calibrations of one flow, not gas composition readings.

Bus errors and bad CRCs clear current flow and trigger recovery after one second.
Recovery stops only address `0x2E`; it does not reset other devices on the bus.
**Pi I²C unavailable** indicates a missing bus, dependency, or access permission.
**Sensor not responding** indicates an I2C transaction failure; check wiring and
power. Stale data is hidden after two seconds. Arduino USB failures do not change
mass flow status. A CSV write failure is reported without stopping live reads.

## Verification

```sh
python3 -m unittest discover -s tests -p 'test_mass_flow_support.py' -v
python3 -m unittest discover -s tests -p 'test_arduino_firmware.py' -v
node tests/test_mass_flow_panel.js
sh deploy/install.sh --check
```

On the Pi, check zero/forward/reverse flow and range, unplug/reconnect the sensor,
restart the service while the sensor remains powered, and verify recovery and
blank fault rows. Disconnect the Arduino and confirm flow still streams. Record
and stop a run, then verify the separate CSV and metadata before vehicle use.
