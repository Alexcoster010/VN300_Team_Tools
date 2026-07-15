# MoTeC M130 CAN Broadcast To Raspberry Pi Integration Report

## Purpose

This report explains what is needed to get selected MoTeC M130 data channels onto the Raspberry Pi logger using CAN. The goal is for the MoTeC to broadcast data and for the Pi to passively listen, log the data, show useful channels on the dashboard, and let the offline analyzer combine MoTeC data with VN300 data.

Recommended architecture:

```text
MoTeC M130 -> CAN broadcast -> Raspberry Pi CAN adapter -> Pi logger -> analyzer
```

The Pi should be a passive data logger. It should not control the MoTeC.

## Why Use CAN

CAN is the best way to get MoTeC data into the Pi because:

- it avoids duplicating analog sensors
- it avoids splicing into throttle/brake sensor wiring
- it keeps the MoTeC as the trusted engine/controller system
- it lets the Pi log many channels with only two signal wires
- it can support live dashboard data
- it can later support wheel speeds, brake pressure, throttle, RPM, and other channels

## Target Channels

Start with a small, useful channel set.

High priority:

- throttle position
- front brake pressure
- rear brake pressure
- engine RPM or motor RPM
- gear
- wheel speeds, if available
- battery voltage

Useful later:

- coolant temperature
- oil pressure
- oil temperature
- fuel pressure
- lambda
- manifold pressure
- powertrain temperatures
- driver switches
- traction/launch/status channels

For vehicle dynamics work, the most important channels are throttle, brake pressure, wheel speeds, RPM, and gear.

## Hardware Needed

## 1. Raspberry Pi CAN Interface

The Raspberry Pi does not have native CAN terminals. It needs a CAN interface.

Recommended options:

- isolated USB-to-CAN adapter
- isolated Raspberry Pi CAN HAT
- non-isolated CAN HAT for bench testing only

For a race car, use an isolated interface if possible. The car is electrically noisy, and isolation reduces the chance that the Pi or laptop-side electronics cause communication or ground problems.

## 2. CAN Wiring

CAN uses two signal wires:

```text
CAN-H
CAN-L
```

Minimum wiring:

```text
MoTeC CAN-H  -> Pi CAN adapter CAN-H
MoTeC CAN-L  -> Pi CAN adapter CAN-L
Ground ref   -> Pi CAN adapter ground reference
```

Use twisted pair wire for CAN-H and CAN-L.

Do not connect CAN-H or CAN-L to 12 V, 5 V, or 3.3 V.

## 3. CAN Termination

A CAN bus should have two 120 ohm termination resistors: one at each physical end of the bus.

With the car powered off, measuring resistance between CAN-H and CAN-L should usually read about:

```text
60 ohms
```

That means two 120 ohm terminators are installed in parallel.

Do not add another terminator unless the bus actually needs it. Too much termination can cause CAN problems.

## 4. Power And Mounting

The Pi and CAN adapter need reliable power and mounting.

Recommendations:

- use a regulated Pi power supply
- avoid powering the Pi directly from noisy 12 V
- secure the CAN adapter so it cannot vibrate loose
- strain-relief all CAN wires
- avoid running CAN wires next to ignition, motor phase, or high-current wiring

## MoTeC Side Requirements

## 1. Confirm CAN Bus Details

Before touching the Pi, find out:

- which MoTeC CAN bus is available
- CAN bitrate
- whether the bus is already used by a dash, PDM, logger, or other device
- whether spare bandwidth exists
- whether the bus is standard 11-bit ID or extended 29-bit ID
- whether there are already message IDs that must not be reused

Common bitrates are:

```text
500000
1000000
```

The Pi must use the exact same bitrate.

## 2. Configure MoTeC Transmit Messages

In MoTeC M1 Tune or through the team's MoTeC package configuration, configure the M130 to broadcast selected channels.

Depending on the package, this may be:

- an existing dash/logger CAN stream
- a configurable CAN transmit template
- a custom M1 Build package change
- a dealer/support-created CAN transmit setup

Important note: some M1 packages do not allow fully custom CAN transmit messages directly in M1 Tune. If the package does not expose the needed CAN transmit settings, the team may need help from whoever owns or maintains the MoTeC package.

## 3. Choose Message IDs

Each CAN message needs an ID.

Example IDs:

```text
0x500
0x501
0x502
```

Rules:

- do not reuse IDs already present on the car
- keep IDs grouped logically
- document every ID
- keep message rates reasonable

## 4. Choose Update Rates

Suggested rates:

| Channel Type | Suggested Rate |
| --- | ---: |
| throttle position | 50-100 Hz |
| front/rear brake pressure | 50-100 Hz |
| wheel speeds | 50-100 Hz |
| RPM | 20-50 Hz |
| gear | 20-50 Hz |
| temperatures | 5-20 Hz |
| voltage/status channels | 5-20 Hz |

Do not broadcast every channel at 100 Hz. That wastes CAN bandwidth.

## 5. Define Signal Scaling

Each channel needs:

- message ID
- byte position
- bit length
- byte order
- signed/unsigned
- scale
- offset
- unit

Example:

```text
Message ID: 0x500
Rate: 50 Hz
Byte 0-1: Throttle position, unsigned, scale 0.1 %, offset 0
Byte 2-3: Front brake pressure, unsigned, scale 0.1 psi, offset 0
Byte 4-5: Rear brake pressure, unsigned, scale 0.1 psi, offset 0
Byte 6-7: Engine RPM, unsigned, scale 1 rpm, offset 0
```

The Pi cannot decode useful engineering values unless this layout is known.

## 6. Create A CAN Dictionary

The best format is a DBC file.

A DBC file describes:

- CAN IDs
- signal names
- start bits
- lengths
- scales
- offsets
- units
- signed/unsigned formats

If the team cannot create a DBC file at first, use a CSV or Markdown table. But long term, DBC is better.

Minimum table:

```text
message_id,rate_hz,signal_name,start_byte,length_bytes,signed,scale,offset,unit
0x500,50,Throttle_pct,0,2,no,0.1,0,%
0x500,50,Brake_Front_psi,2,2,no,0.1,0,psi
0x500,50,Brake_Rear_psi,4,2,no,0.1,0,psi
0x500,50,RPM,6,2,no,1,0,rpm
```

## Pi Side Requirements

## 1. Install CAN Tools

On the Pi:

```sh
sudo apt update
sudo apt install -y can-utils
```

Python support likely needed:

```sh
python3 -m pip install --user python-can cantools
```

Package choices:

- `python-can`: reads CAN frames in Python
- `cantools`: decodes DBC files
- `can-utils`: command-line tools like `candump`

## 2. Bring Up CAN Interface

The CAN adapter should appear as something like:

```text
can0
```

Bring it up with the same bitrate as the MoTeC:

```sh
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 1000000
sudo ip link set can0 up
```

For 500 kbit/s:

```sh
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 500000
sudo ip link set can0 up
```

## 3. Test Raw CAN Frames

Run:

```sh
candump can0
```

Expected result:

- CAN frames scroll by when the MoTeC/car is powered
- IDs match the configured MoTeC transmit IDs

Example:

```text
can0  500   [8]  2C 01 E8 03 D2 03 80 3E
can0  501   [8]  ...
```

If nothing appears:

- bitrate may be wrong
- CAN-H/CAN-L may be swapped
- MoTeC may not be transmitting
- bus may not be powered
- termination may be wrong
- CAN adapter may not be up

## 4. Prefer Listen-Only If Available

If the CAN adapter supports listen-only mode, use it for early testing.

The Pi should not transmit frames onto the MoTeC bus unless the controls/electrical team intentionally designs that behavior.

## Logger Software Changes Needed

## 1. Add CAN Configuration

Add logger options such as:

```sh
--can-interface can0
--can-bitrate 1000000
--can-dbc /home/vectornav/vn300_tools/motec.dbc
--no-can
```

Defaults could be:

```text
CAN disabled unless configured
```

This prevents CAN errors from stopping VN300 logging.

## 2. Add A CAN Reader Thread

The Pi logger should have a separate thread:

```text
VN300 serial thread -> raw/bin/csv files
CAN reader thread   -> MoTeC CAN CSV
Dashboard thread    -> live display
```

The CAN thread should:

- open `can0`
- receive frames
- decode frames using DBC or manual mapping
- update latest MoTeC values for the dashboard
- write decoded rows to CSV
- keep running independently from VN300 serial logging

If CAN fails, VN300 logging should continue.

## 3. Write MoTeC CSV Output

Expected file:

```text
VN300_YYYY-MM-DD_RUN001_MOTEC.csv
```

Recommended columns:

```text
Pi_Elapsed_Time_s,Source,CAN_ID,Throttle_pct,Brake_Front_psi,Brake_Rear_psi,RPM,Gear,WheelSpeed_FL_mph,WheelSpeed_FR_mph,WheelSpeed_RL_mph,WheelSpeed_RR_mph
```

There are two possible CSV styles:

## Wide CSV

One row has all latest decoded channel values.

Pros:

- easier for analysis
- easier in Excel

Cons:

- repeated values when messages arrive at different rates

## Raw Decoded Message CSV

One row per received CAN message.

Pros:

- closer to actual CAN bus
- easier to debug

Cons:

- analyzer must resample/merge later

Recommended: write both if possible.

```text
VN300_YYYY-MM-DD_RUN001_MOTEC_RAW_CAN.csv
VN300_YYYY-MM-DD_RUN001_MOTEC_CHANNELS.csv
```

## 4. Add Dashboard MoTeC Fields

Dashboard should show:

- throttle position
- front brake pressure
- rear brake pressure
- brake bias estimate
- RPM
- gear
- wheel speeds
- CAN status
- CAN message count
- last CAN update age

This lets the team verify the MoTeC stream is alive before a run.

## 5. Add Session Metadata

Session metadata JSON should include:

```json
{
  "can_enabled": true,
  "can_interface": "can0",
  "can_bitrate": 1000000,
  "can_dbc": "motec.dbc",
  "can_frames": 12345,
  "can_decode_errors": 0
}
```

## Analyzer Software Changes Needed

## 1. Find MoTeC CSV Files

When analyzing a boot folder, the analyzer should detect:

```text
*_MOTEC_CHANNELS.csv
*_MOTEC_RAW_CAN.csv
```

It should associate them with the matching run ID:

```text
VN300_YYYY-MM-DD_RUN001
```

## 2. Synchronize With VN300 Data

Use Pi elapsed time as the common time base.

Required:

- VN300 sample time
- MoTeC channel time
- same run ID

The analyzer should resample MoTeC channels onto the VN300 timeline or keep them as separate traces aligned by time/distance.

## 3. Add New Analysis Channels

Once MoTeC data is available, add:

- throttle vs distance
- brake pressure vs distance
- front/rear brake pressure balance
- RPM vs distance
- gear vs distance
- wheel speed comparison
- wheel lockup detection
- wheelspin detection
- braking zone detection from brake pressure
- throttle pickup timing

## Safety Rules

## 1. Pi Must Be Passive

The Pi should only listen to MoTeC CAN messages.

Do not send CAN messages from the Pi to the MoTeC bus unless the controls/electrical team explicitly approves and tests it.

## 2. Do Not Modify Critical MoTeC Control Logic

The CAN transmit setup should not affect:

- throttle control
- ignition
- fuel
- torque control
- safety shutdowns
- power limits

## 3. Test On Bench First

Before connecting to a running car:

1. Configure MoTeC CAN transmit.
2. Connect Pi CAN adapter.
3. Power the electronics safely.
4. Run `candump can0`.
5. Confirm expected IDs.
6. Confirm no CAN errors.
7. Confirm MoTeC/dash still behave normally.

## 4. Keep A Rollback Plan

Before changing MoTeC settings:

- save a backup of the current MoTeC package/config
- document the old CAN settings
- know how to restore the previous setup

## Step-By-Step Implementation Plan

## Phase A: MoTeC Configuration

1. Identify which CAN bus will be used.
2. Identify the CAN bitrate.
3. Identify existing CAN IDs.
4. Choose first channel set.
5. Configure MoTeC transmit messages.
6. Document IDs, rates, byte layout, scaling, and units.
7. Create a DBC or simple signal map file.

## Phase B: Pi Bench Test

1. Install CAN adapter on Pi.
2. Install `can-utils`.
3. Bring up `can0` at the correct bitrate.
4. Run `candump can0`.
5. Confirm expected MoTeC IDs appear.
6. Confirm no bus errors.

## Phase C: Logger Integration

1. Add CAN options to logger.
2. Add CAN reader thread.
3. Add decoded MoTeC CSV writer.
4. Add CAN counters to dashboard.
5. Add MoTeC live fields to dashboard.
6. Add CAN metadata to session JSON.
7. Make CAN failure non-fatal to VN300 logging.

## Phase D: Analyzer Integration

1. Auto-detect matching MoTeC CSVs by run ID.
2. Merge MoTeC and VN300 data by Pi elapsed time.
3. Add throttle/brake/RPM/gear plots to `report.html`.
4. Add brake pressure and throttle metrics to `summary.csv`.
5. Add wheel speed analysis if wheel speed channels are available.

## Phase E: Track Validation

1. Run short static test with car powered.
2. Confirm dashboard values move correctly.
3. Press brake pedal and confirm front/rear pressure change.
4. Press throttle and confirm throttle value changes.
5. Start/stop a short log.
6. Confirm MoTeC CSV exists.
7. Run analyzer.
8. Confirm throttle/brake traces align with VN300 data.

## Open Information Needed From The Team

Before coding final CAN support, collect:

- MoTeC package type/name
- CAN bus number used
- CAN bitrate
- available MoTeC transmit options
- chosen channel list
- CAN message IDs
- byte layout
- scale/offset/unit for each signal
- whether wheel speeds are available in the M130
- what CAN adapter the Pi will use
- whether the adapter supports listen-only mode

## Recommended First Channel Pack

Start with one 8-byte message if possible:

```text
ID 0x500 at 50 Hz
Bytes 0-1: Throttle_pct, unsigned, scale 0.1 %
Bytes 2-3: Brake_Front_psi, unsigned, scale 0.1 psi
Bytes 4-5: Brake_Rear_psi, unsigned, scale 0.1 psi
Bytes 6-7: RPM, unsigned, scale 1 rpm
```

Then add a second message:

```text
ID 0x501 at 50 Hz
Bytes 0-1: WheelSpeed_FL_mph, unsigned, scale 0.01 mph
Bytes 2-3: WheelSpeed_FR_mph, unsigned, scale 0.01 mph
Bytes 4-5: WheelSpeed_RL_mph, unsigned, scale 0.01 mph
Bytes 6-7: WheelSpeed_RR_mph, unsigned, scale 0.01 mph
```

Then add slower status messages later.

## Bottom Line

The cleanest integration is for the MoTeC M130 to broadcast selected channels on CAN while the Pi passively listens and logs them. The hardest part is not the Pi code; it is getting a clear, documented MoTeC CAN transmit layout. Once the team has the message IDs, rates, byte layout, scales, offsets, and units, the Pi logger can decode and save the data, and the analyzer can merge it with VN300 data by run ID and timestamp.
