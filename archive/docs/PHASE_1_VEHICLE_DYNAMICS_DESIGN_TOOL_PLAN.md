# Phase 1 Vehicle Dynamics Design Tool Plan

## Goal

Phase 1 should turn the current VN300 analyzer from a basic lap/run summary tool into the first version of a full FSAE vehicle dynamics analysis and design tool.

The goal is not to solve every vehicle dynamics problem immediately. The goal is to create a reliable foundation that lets the team compare runs, validate setup changes, identify where time is gained or lost, and begin connecting logged data to design decisions.

Phase 1 should focus on:

- [x] data quality checks
- [x] structured run/setup metadata
- [x] better lap/run/sector segmentation
- [x] G-G diagram and grip metrics
- [x] speed, acceleration, and yaw analysis
- [x] run-to-run comparison
- [x] first-pass driver input support plan
- [x] readable HTML reports for the whole team

## Current Tool Starting Point

The current analyzer already does these things:

- reads `*_BINARY.csv` and `*_VNINS.csv`
- imports individual files or folders
- prefers binary files over duplicate ASCII VNINS files
- summarizes duration, distance, speed, GPS bounds, and GPS uncertainty
- splits laps using one GPS start/finish line
- splits autocross runs using separate start and finish GPS lines
- writes per-lap/per-run CSVs
- calculates delta to the best previous lap/run
- creates a basic `overlay.html`

Phase 1 should build on this instead of replacing it.

## What The Team Needs To Do

## 1. Create A Consistent Run Metadata Process

Every logged run needs context. Without metadata, the analyzer can show differences between runs but cannot explain why they happened.

The team should record:

- run number
- driver
- date
- test location
- test type
- course layout or event name
- car configuration
- tire compound
- cold tire pressures
- hot tire pressures
- ambient temperature
- track temperature, if available
- aero package
- wing angles, if applicable
- camber
- toe
- caster, if changed
- ride heights
- damper settings
- anti-roll bar settings
- brake bias setting
- battery/fuel state
- notes about cones, spins, DNFs, driver mistakes, or traffic

This can start as a simple spreadsheet. It does not need to be fancy.

## 2. Run Repeatable Tests

The software is only as useful as the test procedure. The team should plan tests where one variable changes at a time.

Recommended Phase 1 test types:

- autocross-style course
- skidpad or constant-radius corner
- straight-line acceleration
- straight-line braking
- slalom
- same-driver A/B setup comparison

Avoid changing tire pressure, damper settings, aero, and driver all at once. If too many things change, the data cannot identify the cause.

## 3. Record Manual Measurements

Phase 1 does not require new sensors, but manual measurements matter.

Record:

- cold tire pressure
- hot tire pressure
- tire temperatures if the team has a pyrometer or IR gun
- setup changes
- driver notes immediately after the run

These should be matched to the VN300 log file name or run number.

## 4. Decide The Next Sensors To Add

The most useful future channels are:

- steering angle
- throttle position
- front brake pressure
- rear brake pressure
- wheel speeds
- motor/engine RPM
- gear

The best path for MoTeC channels is to broadcast them from the MoTeC M130 over CAN and have the Pi listen passively.

Steering angle can be added with a steering column/rack sensor, but it should be calibrated against real wheel angle measurements.

## What I Would Need To Do In Software

## 1. Add Metadata Import

Status: Done. The analyzer now accepts `--metadata`, includes a `metadata_template.csv`, and merges matching CSV metadata rows into `summary.csv` and `report.html`.

Add support for a team metadata file, probably CSV first.

Example columns:

```text
session_file,run_number,driver,test_type,course,car_config,cold_fl_psi,cold_fr_psi,cold_rl_psi,cold_rr_psi,hot_fl_psi,hot_fr_psi,hot_rl_psi,hot_rr_psi,front_camber_deg,rear_camber_deg,front_toe_deg,rear_toe_deg,brake_bias,notes
```

The analyzer should load this file and attach the metadata to summaries and reports.

## 2. Add Data Quality Checks

Status: Done. The analyzer now writes `data_quality.csv` and includes data-quality status/warnings in `report.html`.

Before producing design conclusions, the tool should flag bad data.

Checks to add:

- missing required columns
- zero latitude/longitude rows
- impossible GPS jumps
- impossible speeds
- duplicate timestamps
- low sample rate
- high GPS position uncertainty
- short or incomplete runs
- sensor dropouts
- mismatched binary and ASCII sessions

The report should clearly mark runs as:

- usable
- usable with warnings
- not recommended for design decisions

## 3. Add Better Segmentation

Status: Started. The analyzer now automatically splits each timed lap/run into three low-cornering sectors by default and writes `sector_summary.csv`.

The current start/finish split is useful, but the tool needs more ways to divide a run.

Add:

- automatic sector splits
- custom named course segments
- acceleration test mode
- braking test mode
- skidpad/constant-radius mode
- slalom mode
- invalid segment marking
- ability to compare only selected segments

This lets the team evaluate specific design questions instead of only full-run time.

## 4. Add G-G Diagram And Grip Metrics

Status: Done. The analyzer now computes G metrics and adds a G-G plot to `report.html`.

Use VN300 acceleration data to generate a friction-circle style plot:

```text
longitudinal G vs lateral G
```

Metrics to compute:

- peak lateral G left
- peak lateral G right
- peak braking G
- peak acceleration G
- peak combined G
- average combined G during active driving
- percent of run above selected lateral G threshold
- percent of run above selected braking G threshold

This helps identify whether the car, driver, or setup is using available grip.

## 5. Add Speed, Acceleration, And Yaw Analysis

Status: Started. The analyzer now exports speed, longitudinal/lateral G, yaw-rate estimate, and curvature estimate in reports and lap/run CSVs.

Use current VN300 channels to compute:

- speed vs distance
- lateral G vs distance
- longitudinal G vs distance
- yaw angle vs distance
- yaw rate estimate
- curvature estimate
- speed vs curvature
- braking zones
- acceleration zones
- corner entry/mid/exit estimates

This makes the analyzer useful for vehicle dynamics, not only timing.

## 6. Add Run-To-Run Comparison

Status: Started. `report.html` now includes a distance-based delta-to-fastest plot.

The tool should compare two runs by distance, not only by timestamp.

Comparison outputs:

- speed delta vs distance
- time delta vs distance
- lateral G comparison
- longitudinal G comparison
- GPS line overlay
- table of where time was gained or lost
- best run vs selected run
- driver A vs driver B
- setup A vs setup B

This should be one of the highest-priority Phase 1 features.

## 7. Add First-Pass Vehicle Balance Metrics

Status: Planned. Phase 2 now defines the driver-input and steering data needed before this should become a real model.

Before true slip angle is available, the tool can still estimate balance trends.

With only VN300 data:

- compare steering-independent yaw response trends
- identify corners with low speed but high time loss
- identify low lateral G at similar curvature
- identify poor braking-to-turning transition

Once steering angle is added:

- steering angle vs lateral G
- steering angle vs curvature
- steering angle vs yaw rate
- understeer/oversteer tendency
- approximate understeer gradient

True tire slip angle should wait until steering, yaw rate, speed, and vehicle geometry are reliable.

## 8. Prepare For MoTeC CAN Data

Status: Not started in code. The intended architecture is documented, but no CAN import format has been implemented yet.

Phase 1 software should define the structure for importing MoTeC data, even if CAN logging is added slightly later.

Target MoTeC channels:

- throttle position
- front brake pressure
- rear brake pressure
- engine/motor RPM
- gear
- wheel speeds
- battery voltage
- coolant/oil temps for IC cars, or powertrain temps for EV cars

The recommended architecture is:

```text
MoTeC M130 -> CAN broadcast -> Pi listens -> analyzer combines by timestamp
```

The Pi should be a passive listener. It should not control the MoTeC.

## 9. Improve HTML Reports

Status: Done for first pass. The analyzer now writes `report.html` with quality, metadata, summary, G-G, speed, G traces, and yaw-rate plots.

The analyzer should generate a report that a design lead, driver, or freshman can open without using Python.

Report sections:

- run metadata
- data quality warnings
- best run summary
- setup comparison table
- G-G diagram
- speed vs distance
- lateral/longitudinal G traces
- yaw/curvature traces
- GPS track map
- run-to-run delta plot
- sector table
- notes and warnings

The current `summary.csv` should remain, but the HTML report should become the primary output for team review.

## 10. Keep Outputs Engineer-Friendly

Status: Started. The analyzer still writes CSV outputs and now adds `data_quality.csv` plus extra G/yaw fields in lap/run CSVs.

The tool should still export CSV files for deeper work.

Useful exports:

- cleaned synchronized run data
- segment summaries
- comparison tables
- data quality report
- metadata-merged summary

This lets advanced users pull the data into MATLAB, Python, Excel, or other tools.

## What Phase 1 Should Not Try To Do Yet

Phase 1 should not become too large.

Not included yet:

- full tire thermal sensor system
- TPMS integration
- full MoTeC CAN logger implementation, unless hardware is ready
- camera integration
- suspension potentiometer analysis
- aero model
- true tire slip angle model
- full understeer/oversteer model
- real-time predictive lap simulation

Those are useful later, but Phase 1 should first make the existing VN300 data trustworthy and useful.

## Future Phase 2 Direction

After Phase 1 works, Phase 2 should add more vehicle channels:

- MoTeC CAN logging
- steering angle logging
- throttle/brake overlays
- wheel speed analysis
- brake balance analysis
- understeer/oversteer estimates
- tire temperature sensor import
- more complete corner analysis

## Future Phase 3 Direction

Phase 3 can become a deeper design-validation tool:

- slip angle estimation
- tire utilization model
- aero/drag estimation
- suspension travel and damper velocity analysis
- setup optimizer reports
- endurance consistency analysis
- driver coaching reports
- live read-only dashboard for team viewing
- admin dashboard for setup/control

## Suggested Phase 1 Workflow

1. Team logs VN300 data during a test.
2. Team records setup metadata and manual measurements.
3. Analyzer imports VN300 files and metadata.
4. Analyzer checks data quality.
5. Analyzer segments the run into laps, runs, sectors, or test sections.
6. Analyzer generates grip metrics and G-G diagrams.
7. Analyzer compares selected runs by distance.
8. Team reviews the HTML report.
9. Team chooses one setup change for the next test.

## Success Criteria

Phase 1 is successful if the team can answer:

- Which run was fastest?
- Where was time gained or lost?
- Which setup produced the highest lateral grip?
- Which setup produced the best braking performance?
- Did the driver or the setup cause the change?
- Were any logs bad enough to ignore?
- Did the car look grip-limited, power-limited, braking-limited, or driver-limited?
- What single setup change should be tested next?

## Bottom Line

Phase 1 should make the analyzer a useful vehicle dynamics and race-engineering tool using the data the team can collect now. The biggest immediate improvements are metadata, data quality checks, G-G plots, segmentation, and run-to-run comparison. Once that foundation is reliable, MoTeC CAN, steering angle, tire temperature, and true slip-angle analysis can be added with much more confidence.
