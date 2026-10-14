# Arduino A0 steering and A1 airspeed

The shared Uno USB connection now sends one line containing two voltages:

```text
2.414,0.543
```

The first value is A0 steering voltage, the second A1 FS7002-D output voltage.
Use `115200` baud and newline termination. Select this format explicitly so
raw ADC integers cannot accidentally be interpreted as voltages:

```sh
--arduino-format a0-a1-volts \
--arduino-reference-voltage 5.0 \
--arduino-adc-max-count 1023 \
--airspeed-calibration /home/vectornav/airspeed_calibration.json
```

Keep the existing `--steering-port`, `--steering-baud`, `--steering-calibration`,
VN300 options and `--can-enable`. Copy the airspeed example to the actual profile
path when first setting up this sensor; future updates preserve that profile.
The original single-integer protocol remains available with the default
`--arduino-format steering-raw`.

## Steering conversion

The existing calibration is in ADC counts. For this voltage protocol, reconstruct
an approximate ADC count with `round(voltage * 1023 / 5)` before applying the
20 / 315 / 688 count calibration for -157.5 / 0 / +157.5 degrees. This assumes
the sketch calculates voltage as `analogRead(pin) * 5.0 / 1023.0`. If its divisor
or scale changes, change the corresponding command options. Sending voltage
rounded to three decimals loses some precision; the original received voltages
are retained. No steering filter or deadband is applied.

## FS7002-D airspeed estimate

Source: [Siargo FS7002 User Manual VD.1.01, July 2022](https://identic.de/uploads/tx_pdtidentic/FS7002_Massflow-Sensors_VD101.pdf),
page 13 specifications and page 15 Figure 7.1. This analog sensor is nonlinear
and has a 0–15 m/s range. The manual provides a typical graph, not an exact
per-sensor calibration equation. The example uses 16 approximate points read
from the blue FS7002-D curve and linear interpolation **between these points**,
not a single proportional voltage scale.

The dashboard explicitly labels this an estimate and displays m/s, mph, and the
received sensor voltage. `mph = m/s / 0.44704`. No pressure/temperature correction
is added; the published characteristics are for air at 20 C and 101.325 kPa.
The actual sensor's zero and full-scale output can vary. Validate against known
airflow before using these values as a calibrated airspeed measurement.
Warm up the sensor for at least one minute as required by the manual.

The nominal example starts at 0.50 V, 0 m/s and ends at 2.92 V, 15 m/s.
Below the nominal zero voltage, speed is zero and the reading is flagged
`below_nominal_zero`. Above the lookup curve, speed is unavailable and flagged
`above_range`; the original voltage is still stored. A voltage above this
particular curve does not prove that airflow exceeds 15 m/s, because individual
sensor full-scale voltage varies. The sensor range corresponds to about 33.6 mph.

Replace profile points with measured voltage/speed pairs when a sensor-specific
calibration is available. Restart the logger after changing a profile. The entire
profile and serial settings are included in run metadata for reproducibility.

## Display and recordings

One serial reader continuously updates both channels. Neither serial port is
reopened between runs. The `/api/latest` endpoint exposes `steering` and
`airspeed`, with shared status and reading age. Invalid or missing fields reject
the entire pair, so the interface cannot silently combine fresh and old values.

During each normal recording, both are saved in the existing
`<run-prefix>_STEERING.csv` file with the same run timestamp:

- Existing steering raw count, angle and range flag.
- `Steering_Voltage_V`: original A0 voltage.
- `Airspeed_Raw`: ADC count reconstructed from A1 voltage.
- `Airspeed_Voltage_V`: original A1 voltage.
- `Airspeed_mps` and `Airspeed_mph`: estimate from the configured curve, blank
  above range.
- `Airspeed_Range`: `in_range`, `below_nominal_zero`, or `above_range`.

`Pi_Logger_Elapsed_Time_s` uses the same monotonic run origin as VN300 and CAN.
It represents reception of the complete line, not the original ADC sample time.
Idle live readings create no run files. Recording stops flush and sync the CSV.
