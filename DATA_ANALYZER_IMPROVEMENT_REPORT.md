# Data Analyzer Improvement Report

## Purpose

The current VN300 analyzer is a good first-stage data tool. It imports VN300 CSV logs, summarizes runs, splits laps or autocross runs from GPS timing lines, generates an HTML overlay, and computes live delta against the best previous segment.

To become a complete FSAE design tool, it needs to move from answering "what happened during this run?" toward answering "why did the car perform this way, and what should we change?"

## Current Strengths

- Reads both `*_BINARY.csv` and `*_VNINS.csv`.
- Can process a whole folder and prefer binary data over duplicate ASCII data.
- Computes basic run metrics: duration, distance, speed, GPS bounds, and GPS uncertainty.
- Supports lap and autocross run splitting from start/finish GPS lines.
- Generates per-lap/per-run CSVs.
- Computes live delta to the best previous segment.
- Flags poor GPS uncertainty over `4.0 m`.
- Has no third-party dependencies, which makes it easy to run on most team laptops.

## Main Limitation

Right now, the analyzer mostly treats the car as a GPS trace with speed and attitude. For FSAE design work, the tool needs to connect data to vehicle systems: tires, aero, suspension, braking, acceleration, driver inputs, setup changes, and repeatable test procedures.

## High-Value Improvements

## 1. Run Database And Metadata

Add a structured run log so every data file knows the car setup and test context.

Important metadata:

- driver
- event/test day
- tire compound and tire pressures
- ambient and track temperature
- battery/fuel state
- aero package
- suspension setup
- alignment
- damper settings
- anti-roll bar settings
- run notes
- cone hit, DNF, or invalid flag

Without metadata, the team can compare laps but cannot confidently connect performance changes to design changes.

## 2. Better Lap And Autocross Segmentation

The current GPS-line splitter is useful, but FSAE testing needs more segmentation.

Add:

- sector splits
- custom gates
- automatic start/finish detection from speed/GPS patterns
- manual correction UI or config file
- invalid segment marking
- standing-start acceleration tests
- skidpad test mode
- brake test mode
- constant-radius test mode

FSAE teams do many small controlled tests, not just full course laps.

## 3. Vehicle Dynamics Metrics

Add metrics that directly help design and setup decisions:

- max/average lateral G
- max/average longitudinal G
- combined G-G diagram
- yaw rate vs speed
- estimated curvature
- speed vs curvature
- braking zone detection
- acceleration zone detection
- corner entry/mid/exit speed
- minimum speed per corner
- time lost by segment
- distance-based speed traces
- understeer/oversteer indicators if steering angle becomes available

The G-G diagram alone would be a major upgrade because it shows whether the car is tire-limited, driver-limited, power-limited, or braking-limited.

## 4. Driver Comparison Tools

For competition and testing, the tool should compare two runs by distance, not just time.

Add:

- best-vs-current speed delta by distance
- where time was gained or lost
- corner-by-corner time loss
- braking point comparison
- throttle pickup comparison if throttle is logged later
- minimum corner speed comparison
- line comparison with lateral offset

The current live delta logic is a start, but the output should explain where the delta came from.

## 5. Design-System Channels

The analyzer should be ready to ingest more than VN300 data.

Future channels:

- steering angle
- brake pressure
- throttle position
- wheel speeds
- motor RPM or engine RPM
- motor torque or engine torque
- battery voltage/current
- suspension potentiometers
- damper velocities
- tire temperatures
- aero pressure sensors
- GPS quality/RTK state

A complete FSAE design tool needs synchronized multi-channel data, not only INS/GPS.

## 6. Tire And Grip Analysis

For FSAE, tire performance is central.

Useful additions:

- friction circle / G-G plot
- lateral G vs speed
- longitudinal G vs speed
- estimated tire utilization
- tire warmup trends by run
- grip falloff over time
- left/right loading inference if suspension or wheel-speed data is added
- skidpad-specific metrics

This helps answer questions like: "Did the new setup increase usable grip, or did the driver just drive better?"

## 7. Aero And Drag Analysis

If the team has aero or EV power data, the analyzer could estimate:

- acceleration vs speed
- drag-limited acceleration
- coastdown drag estimate
- aero balance hints from speed, yaw, and lateral G
- comparison of aero package A/B runs

Even rough estimates would help validate design changes.

## 8. Suspension And Handling Analysis

With steering/suspension sensors added later, the tool could support:

- roll gradient
- pitch under braking
- damper velocity histograms
- ride height trends
- steering response
- yaw rate gain
- understeer gradient estimate
- transient response comparison

This would make it valuable to suspension and controls teams.

## 9. Better Output Reports

The current `summary.csv` and `overlay.html` are useful, but a design workflow needs reports.

Add generated reports with:

- best run summary
- test conditions
- setup metadata
- key plots
- sector table
- time-loss table
- G-G diagram
- speed trace
- GPS track map
- warnings/data quality
- recommended next checks

Output formats:

- HTML report
- CSV exports
- optional PDF
- plots as PNG/SVG

## 10. Data Quality And Validation

The analyzer should protect the team from drawing bad conclusions.

Add checks for:

- missing columns
- bad GPS jumps
- impossible speeds
- zero lat/lon rows
- bad timestamps
- duplicate samples
- low sample rate
- high GPS uncertainty
- sensor dropouts
- mismatched binary/ASCII sessions

Bad setup-test data with `0.0` GPS values is exactly the kind of thing the tool should automatically flag or exclude.

## Suggested Roadmap

## Phase 1: Make It A Better Race Engineer Tool

- Add run metadata file.
- Add sector support.
- Add G-G plot.
- Add speed/delta comparison between two runs.
- Add data quality report.

## Phase 2: Make It A Vehicle Development Tool

- Add support for steering, throttle, brake pressure, wheel speed, and motor/engine data.
- Add braking/corner/acceleration zone detection.
- Add corner-by-corner reports.
- Add setup comparison reports.

## Phase 3: Make It A Design Validation Tool

- Add tire utilization analysis.
- Add aero/drag analysis.
- Add suspension metrics.
- Add test templates for autocross, skidpad, acceleration, brake testing, and endurance-style runs.

## Bottom Line

The current analyzer is a good logging validation and basic lap comparison tool. To become a complete FSAE design tool, the next big step is not just more plots. It needs structured setup metadata, better segmentation, multi-sensor support, data quality checks, and vehicle-dynamics metrics that connect logged data to design decisions.
