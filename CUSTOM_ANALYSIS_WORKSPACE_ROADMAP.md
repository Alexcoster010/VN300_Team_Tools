# Custom Analysis Workspace Roadmap

## Objective

Make Sooner Racing Telemetry useful for both routine drive-day review and open-ended FSAE design work. A freshman should be able to run a standard report without configuring plots, while an experienced engineer should be able to build and save an analysis without changing Python code.

The design is channel-based instead of sensor-specific. VN300, MoTeC M130, CDL3, steering, brake, suspension, tire, and future channels can use the same plotting, formula, filtering, statistics, and export tools.

## Available In v0.10.0

- [x] Keep the existing one-click analyzer as **Quick Report**.
- [x] Add a separate **Custom Workspace**.
- [x] Scan numeric channels from ordinary CSV files.
- [x] Discover decoded long-form `MOTEC_CHANNELS.csv` files.
- [x] Select one or many source files.
- [x] Search the available channel list.
- [x] Select any available channel for the X axis.
- [x] Select multiple Y channels and overlay multiple runs.
- [x] Create calculated channels with safe bracketed references such as `[speed_mph] * 1.609344`.
- [x] Support math, comparisons, filters, smoothing, hold, derivative, integral, clipping, and conditional formulas.
- [x] Add line and scatter plots.
- [x] Add configurable smoothing and plot-point limits.
- [x] Add interactive offline zoom, pan, cursor values, and trace visibility.
- [x] Add count, minimum, maximum, mean, standard deviation, P05, median, and P95 statistics.
- [x] Add built-in speed/G, G-G, driver-input, suspension, and tire presets.
- [x] Let users save and delete named team presets.
- [x] Automatically derive speed, distance, yaw rate, longitudinal G, lateral G, and vertical G when source channels permit.
- [x] Export the full plotted data as a tidy CSV.
- [x] Save the workspace definition as JSON.
- [x] Add saved custom reports to the normal Reports archive.

## Normal Workflow

1. Copy a complete drive-day folder from the Pi to the laptop.
2. Open **Data Analysis**, then **Custom Workspace**.
3. Select the copied drive-day folder and press **Scan Channels**.
4. Check the runs/files to compare.
5. Load a preset or select X/Y channels manually.
6. Add a filter or calculated channel when needed.
7. Use **Preview** while adjusting the analysis.
8. Use **Save Report** when the plot is ready for team review.

## Next: Synchronized Session Data

This is the highest-priority remaining foundation. The VN300 and CAN logger currently write separate files at different sample rates. v0.10.0 can inspect and plot each file, but a formula cannot yet combine a VN300 channel and a CAN channel unless they already share one table.

- [ ] Group VN300 and MoTeC/CDL3 files by run ID.
- [ ] Use Pi logger elapsed time as the common clock.
- [ ] Resample channels at a user-selected rate.
- [ ] Support linear interpolation, nearest sample, and held-value synchronization.
- [ ] Report clock offset, data gaps, and synchronization quality.
- [ ] Make synchronized sessions appear as one channel catalog.
- [ ] Preserve original samples in export for traceability.

## Next: Channel Definitions And Calibration

- [ ] Add a team channel registry with display name, source name, unit, category, valid range, sample type, and calibration version.
- [ ] Add aliases so different MoTeC exports map to the same canonical channel.
- [ ] Add a calibration editor for steering, suspension pots, pressure sensors, and temperature sensors.
- [ ] Store calibration and vehicle-configuration versions with every report.
- [ ] Flag missing, stale, clipped, implausible, or incorrectly scaled channels before analysis.

## Next: Advanced Plot Builder

- [ ] Add multiple linked plot panes with a shared cursor and zoom range.
- [ ] Add independent left/right Y axes and unit-aware scaling.
- [ ] Add histogram, XY scatter, track map, heat map, FFT, and frequency-response views.
- [ ] Add lap-distance normalization and best-lap overlays.
- [ ] Add selectable time/distance ranges and corner/event bookmarks.
- [ ] Add annotations that are saved with the report.
- [ ] Add image and publication-ready CSV export for design reports.

## Next: Vehicle Dynamics Templates

- [ ] Braking performance, pressure balance, lockup, and stopping-distance template.
- [ ] Throttle application, acceleration, shift, and traction template.
- [ ] Steering response, Ackermann, understeer gradient, and yaw-gain template.
- [ ] Wheel-speed slip and driven-wheel traction template.
- [ ] Suspension travel, damper velocity, ride-height, roll, and pitch template.
- [ ] Tire temperature, pressure growth, axle balance, and utilization template.
- [ ] Aero coast-down, drag, downforce balance, and ride-height sensitivity template.
- [ ] Driver consistency, corner entry/apex/exit, and time-loss template.
- [ ] Endurance trend, thermal drift, energy/fuel use, and reliability template.

## Next: Team Workflow

- [ ] Search and filter a run database by driver, track, setup, tire, weather, date, and validity.
- [ ] Compare setup changes against repeatability and confidence limits.
- [ ] Add team-managed preset import/export and revision history.
- [ ] Add report notes, conclusions, and action items.
- [ ] Add a read-only report package that can be sent to judges or alumni without the app.
- [ ] Add preset validation so a template clearly lists required and optional channels.

## Engineering Rules

- Raw logs are never modified.
- Calculated channels record their expression and units.
- Calibration and synchronization assumptions must be visible in saved reports.
- Missing channels produce explicit warnings rather than silently substituting zero.
- Standard reports remain available even as advanced customization grows.
- Physics-based conclusions must include data-quality and repeatability checks.

## Completion Standard

The tool is a complete configurable design environment when a team member can select validated runs, load or build an analysis, combine synchronized channels from every logger, inspect uncertainty and data quality, save a repeatable configuration, and produce a report that another engineer can reproduce without editing source code.
