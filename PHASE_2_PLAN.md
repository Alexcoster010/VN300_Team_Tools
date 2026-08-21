# Phase 2 Plan

## Goal

Phase 2 should add driver-input and vehicle-system data to the VN300 workflow so the team can explain why a run was faster or slower, not just see that it was faster or slower.

Primary Phase 2 goal:

```text
VN300 motion data + MoTeC/dash data + steering angle = useful vehicle dynamics analysis
```

## Phase 2 Major Work Items

## 1. MoTeC And Dash CAN Logging

Add passive CAN logging to the Pi.

Target data sources:

- MoTeC M130
- MoTeC dash/logger, if some sensors are wired there instead of the M130

Target channels:

- throttle position
- front brake pressure
- rear brake pressure
- RPM
- gear
- wheel speeds
- battery voltage
- useful temperatures/pressures

Needed from team:

- CAN bitrate
- available CAN bus
- M130 transmit layout
- dash transmit layout, if used
- CAN IDs
- signal scaling
- signal units
- CAN adapter choice

Software work:

- add CAN reader thread to Pi logger
- add DBC or signal-map decoding
- write `*_MOTEC_CHANNELS.csv`
- write `*_MOTEC_RAW_CAN.csv`
- add CAN health/status to dashboard
- add MoTeC channels to `report.html`
- merge MoTeC and VN300 by Pi time in analyzer

## 2. Steering Angle Logging

Add steering angle as a required vehicle dynamics channel.

Possible hardware paths:

- steering column/rack angular potentiometer into a small ADC/microcontroller
- steering sensor into MoTeC/dash, then broadcast over CAN
- steering sensor into a dedicated CAN sensor module

Preferred path:

```text
Steering sensor -> MoTeC/dash/CAN module -> CAN -> Pi
```

Needed from team:

- sensor type
- mechanical mounting location
- calibration data
- center value
- left/right lock values
- measured wheel angle at multiple steering positions
- front track
- wheelbase
- static toe
- Ackermann definition and expected percentage

Software work:

- convert raw steering signal to steering wheel/rack angle
- convert steering input to estimated left/right front wheel angle
- include steering traces in report
- compare steering vs yaw rate, lateral G, and curvature

## 3. Brake And Throttle Analysis

Once brake pressure and throttle are available:

Add report plots:

- throttle vs distance
- front brake pressure vs distance
- rear brake pressure vs distance
- brake pressure vs longitudinal G
- throttle pickup points
- brake release points

Add metrics:

- peak brake pressure
- front/rear brake pressure ratio
- braking zone count
- braking start/end distance
- throttle pickup distance
- coast time
- brake/throttle overlap

Use cases:

- compare driver braking points
- tune brake bias
- identify locked/ineffective braking
- identify early/late throttle application

## 4. Wheel Speed Analysis

If wheel speeds are available:

Add:

- wheel speed traces
- wheel speed spread
- lockup detection
- wheelspin detection
- driven vs non-driven speed comparison
- vehicle speed cross-check against VN300

Useful outputs:

- front lockup warnings
- rear lockup warnings
- inside tire unloading indicators
- acceleration traction issues

## 5. First-Pass Understeer/Oversteer Metrics

With VN300 + steering:

Add:

- steering angle vs lateral G
- steering angle vs curvature
- steering angle vs yaw rate
- expected yaw rate vs measured yaw rate
- understeer/oversteer tendency flags

Do not claim true tire slip angle until the needed data quality is validated.

First-pass balance indicators:

- high steering input + low yaw response = likely understeer
- low steering correction + high yaw response = possible oversteer
- increasing steering for same curvature/speed = front grip loss or driver correction

## 6. Better Report Workflow

Phase 2 reports should include:

- run metadata
- data quality
- VN300 traces
- MoTeC/dash traces
- steering traces
- throttle/brake/wheel-speed traces
- run-to-run comparison
- sector comparison
- balance indicators
- notes and warnings

Reports should stay readable for non-programmers.

## 7. Dashboard Improvements

Add live dashboard tiles for:

- throttle
- front brake pressure
- rear brake pressure
- RPM
- gear
- wheel speeds
- steering angle
- CAN status
- CAN frame count
- CAN decode errors

Add warning states:

- CAN offline
- stale MoTeC data
- missing steering data
- missing brake pressure
- no VN300 position

## Suggested Phase 2 Order

- [ ] Confirm MoTeC/dash CAN transmit options.
- [ ] Choose and install Pi CAN hardware.
- [ ] Use `candump can0` to prove CAN frames are visible.
- [x] Create first CSV signal map format.
- [x] Add disabled-by-default CAN logging scaffold to Pi.
- [x] Add basic CAN dashboard health/status.
- [ ] Add analyzer import for MoTeC channels.
- [ ] Add throttle/brake/RPM plots.
- [ ] Add steering angle hardware and calibration.
- [ ] Add steering-based balance metrics.
- [ ] Add wheel speed analysis.

## Phase 2 Work Started

The first Phase 2 code slice adds optional passive CAN capture to the Pi logger. It is intentionally off by default so the `v0.4.0` VN300-only workflow stays usable while CAN hardware and MoTeC/dash transmit settings are still being confirmed.

When enabled, each logging session can create:

```text
VN300_YYYY-MM-DD_RUN001_MOTEC_RAW_CAN.csv
VN300_YYYY-MM-DD_RUN001_MOTEC_CHANNELS.csv
```

The raw file preserves every received CAN frame. The channels file only contains decoded values from `pi/motec_can_signal_map.csv`.

Desktop `v0.10.0` can discover and plot decoded long-form `MOTEC_CHANNELS.csv` channels in Custom Workspace. The MoTeC analyzer-import item remains unchecked because VN300 and CAN files still need automatic run grouping, time synchronization, and resampling before one formula or vehicle-dynamics model can reliably combine their channels.

## Phase 2 Success Criteria

Phase 2 is successful if the team can answer:

- Where did the driver brake?
- How hard did the driver brake?
- When did the driver release the brake?
- When did the driver pick up throttle?
- Did brake pressure match deceleration?
- Was brake bias reasonable?
- Did wheel speed show lockup or wheelspin?
- Did steering input match yaw response?
- Did the car show understeer or oversteer trends?
- Did a setup change improve vehicle response or only change driver behavior?

## Not Yet Phase 2

Leave these for Phase 3 unless there is extra time:

- true tire slip angle model
- tire temperature sensor array
- TPMS
- damper velocity histograms
- suspension travel analysis
- aero/drag estimation
- automated setup recommendations
- packaged desktop app

## Bottom Line

Phase 2 should make the system a real driver-input and vehicle-response tool. The highest-value additions are MoTeC/dash CAN data, steering angle, brake pressure, throttle position, and wheel speeds. Once those are reliable, understeer/oversteer and slip-angle work become much more defensible.
