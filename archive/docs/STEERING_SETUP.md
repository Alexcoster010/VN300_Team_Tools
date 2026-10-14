# Arduino USB steering input

For the shared A0 steering / A1 FS7002-D voltage protocol, see
`ARDUINO_ANALOG_SETUP.md`. The original one-integer protocol described below
remains supported.

The logger can read one signed integer per newline from an Arduino over USB.
For the Uno tested on the team Pi, the sketch uses **115200 baud** and sends
raw readings such as `295\r\n`. Without a calibration file, these values remain
uncalibrated: the logger does not assume ADC resolution or supply voltage.

Add these arguments to the existing logger service command, preserving its
Python environment, VN300 options, and `--can-enable`:

```sh
--steering-port /dev/serial/by-id/<Arduino-device-id> --steering-baud 115200
```

Use `ls -l /dev/serial/by-id/` to find the actual persistent device name. Use a
separate persistent `--port` for the VN300 where possible. Steering's device
is excluded from VN300 automatic discovery. Do not use the same port for both.
The service user needs serial device access (normally the `dialout` group).

The Arduino reader stays connected while the service is idle, so a new run
does not reopen the serial port and reset the Uno. Opening/reconnecting the
port allows a two-second startup delay, then discards the first partial line.
The reader retries after disconnects; VN300 and CAN capture continue separately.
Use only one reader of the Arduino port at a time.

The dashboard shows **Steering** even between runs, with degrees when calibrated
and an explicit uncalibrated label otherwise. Raw readings remain visible.
`/api/latest` includes a `steering` object with the latest raw value, age,
sample counts, parsing errors, reconnect count, and file/serial error messages.
Readings older than two seconds are marked stale.

The **Steering Calibration** panel can capture the current raw reading at
measured left, center, and right steering positions. Enter the actual left and
right steering angles in degrees, then save while no run is recording. The
panel requires fresh sensor data for capture, checks the five values, and
applies the new profile to the live reading immediately. It writes the profile
atomically so it survives service restarts. An active run cannot change its
calibration partway through a CSV file.

During each normal recording, `<run-prefix>_STEERING.csv` contains:

- `Pi_Logger_Elapsed_Time_s`: Pi receipt time relative to the same monotonic
  run start used by CAN capture. This is not an Arduino acquisition timestamp;
  USB/serial transport adds latency.
- `System_Time_ISO`: Pi wall-clock time, including timezone.
- `Steering_Raw`: the same uncalibrated count shown on the dashboard and used
  by its calibration capture buttons. In `steering-raw` mode it is the received
  integer. In `a0-a1-volts` mode it is reconstructed from the received A0
  voltage as `round(voltage * adc_max_count / reference_voltage)`; the original
  voltage is also logged as `Steering_Voltage_V`.
- With calibration enabled, `Steering_Angle_deg` adds the converted angle and
  `Steering_Outside_Calibration` flags readings beyond the measured endpoints.

Invalid lines are counted and omitted. Each run's session metadata includes
steering configuration and final counters. Files flush every second while data
arrives and are flushed/synced when recording stops. Idle readings create no
recording files. An optional steering write failure is reported separately.

## Calibration in degrees

The webpanel saves to `steering_calibration.json` beside the installed logger
by default. If `--steering-calibration /path/to/steering_calibration.json` is
set, the panel updates that file instead. The logger loads a saved profile at
startup; a new installation with no profile starts uncalibrated. The example
file contains the team's previously measured raw points and
315-degree total steering travel, split equally left and right:

```json
{
  "raw_left": 20,
  "raw_center": 315,
  "raw_right": 688,
  "left_angle_deg": -157.5,
  "right_angle_deg": 157.5
}
```

Left is negative, right is positive, and center is zero. Each side uses its own
linear scale: `(raw - 315) * 157.5 / 295` below center, and
`(raw - 315) * 157.5 / 373` at/above center. The profile is loaded at service
startup and saved in run metadata; restart the service after editing it outside
the webpanel. Webpanel saves take effect immediately.

The angle describes the steering travel used to calibrate the sensor. This
315-degree range must not be interpreted as front road-wheel angle. Intermediate
angles assume the sensor is linear on each side of center; verify with known
positions when more measurements are available.

No deadband, smoothing, or clipping is applied. Small raw variations remain in
the angle, and out-of-range readings are extrapolated and flagged rather than
silently saturated. The raw channel is retained so filtering/calibration can be
revised later. Do not overwrite an installed calibration with the example on
updates.

## Refining calibration

Measure stable raw readings at center and at known left/right steering angles.
Record whether the angle refers to the steering wheel, rack, or road wheel and
choose the positive direction. Verify the range with a slow full sweep. Do not
label raw values as degrees before measuring this relationship. Original raw
readings are retained alongside calibrated angles so the conversion can be
refined later without losing the original measurements.
