# Coil-Gun Sequencer -- User Guide

## What This App Does

This app controls and measures a multi-stage electromagnetic accelerator
(coil gun). It lets you:

- **Fire** the coil gun from a touchscreen or physical trigger button
- **Measure** how fast the projectile is moving at each stage
- **Tune** timing parameters to improve performance
- **Log** every test run for later analysis

The app runs on a Raspberry Pi 5 and is operated through a web browser on
the attached touchscreen. Two people can view it simultaneously (for example,
one on the touchscreen and one on a laptop).

---

## Getting Started

### First-time setup

1. Power on the Raspberry Pi and open a terminal.
2. Navigate to the project folder and run the setup script:
   ```
   cd coil-gun-sequencer
   ./setup_host_rpi.sh
   ```
3. The script installs everything needed. When it finishes you will see
   instructions for starting the app.

### Starting the app

```
cd coil-gun-sequencer
source .venv/bin/activate
python run.py
```

The app starts on port 5000. Open the browser on the touchscreen and go to:

```
http://localhost:5000
```

To access it from another device on the same network, use the Pi's IP
address (for example `http://192.168.1.42:5000`).

### Stopping the app

Press **Ctrl+C** in the terminal where the app is running.

---

## The Touchscreen Page (Fire Control)

This is the main page you will use during testing. It is designed to be
operated with one hand on the 5-inch touchscreen.

### Layout

```
┌──────────────────────────────────────────────┐
│  Fire Control     Configuration        LIVE  │
├────────────┬─────────────────────────────────┤
│            │                                 │
│  [ READY ] │  Run Statistics                 │
│            │  ┌────────────────────────────┐ │
│ ┌────────┐ │  │       Transit    Flight    │ │
│ │  ARM   │ │  │ Gate 1  0.52ms    --       │ │
│ └────────┘ │  │ Gate 2  0.48ms   12.3ms    │ │
│ ┌────────┐ │  │ Gate 3   --       --       │ │
│ │        │ │  └────────────────────────────┘ │
│ │  FIRE  │ │  ┌────────────────────────────┐ │
│ │        │ │  │       Transit v  Flight v   │ │
│ └────────┘ │  │ Gate 1  19.2m/s    --      │ │
│ ┌────┬───┐ │  │ Gate 2  20.8m/s  8.1m/s   │ │
│ │SAVE│CLR│ │  │ Gate 3   --       --       │ │
│ └────┴───┘ │  └────────────────────────────┘ │
│            │  Sequence: a3f8... | Run: 3     │
└────────────┴─────────────────────────────────┘
```

### Controls

| Button    | What it does                                                |
|-----------|-------------------------------------------------------------|
| **ARM**   | Prepares the system for firing. The status indicator changes from READY to ARMED. |
| **FIRE**  | Fires coil 1 immediately. The remaining coils fire automatically when the projectile reaches each gate sensor. You can also fire using the physical trigger button on the device. |
| **SAVE**  | Ends the current test run and saves all measurements to the database. The system returns to READY so you can fire again. |
| **CLEAR** | Discards the current measurements without saving and returns to READY. Use this if something went wrong (projectile got stuck, etc.). |

### Status indicator

The colored box at the top of the controls shows the current system state:

| Color    | State      | Meaning                                    |
|----------|------------|--------------------------------------------|
| Green    | READY      | Safe to arm. System is idle.               |
| Orange   | ARMED      | Ready to fire. Press FIRE or use the trigger. |
| Red      | FIRING     | Active shot in progress. Wait for it to finish. |
| Blue     | COMPLETE   | Shot finished. Review the statistics, then SAVE or CLEAR. |

### Statistics

The right side of the screen shows measurements from the most recent shot:

- **Transit time** -- How long the projectile blocked each gate sensor's beam.
  Shorter times mean faster projectile.
- **Flight time** -- Time between adjacent gate sensors (Gate 1 to Gate 2,
  Gate 2 to Gate 3). This measures how long the projectile took to travel
  between the two sensors.
- **Transit velocity** -- Speed calculated from the projectile's length and the
  transit time through each gate.
- **Flight velocity** -- Speed calculated from the distance between gates and
  the flight time. The Gate 2 to Gate 3 flight velocity is the muzzle velocity.

A `--` means that measurement is not available (the gate was not triggered, or
the gate/coil is not connected).

---

## The Configuration Page

Tap **Configuration** in the top navigation bar to open this page. Changes
are saved automatically as you edit them.

### Parameters

**Projectile**

| Parameter    | What it means                              |
|--------------|--------------------------------------------|
| Length (mm)  | Length of the projectile. Used to calculate transit velocity. |
| Mass (g)     | Mass of the projectile. Stored for your records. |

**Voltage Thresholds**

| Parameter     | What it means                             |
|---------------|-------------------------------------------|
| V Floor (V)   | Minimum voltage for a coil to be considered charged. |
| V Ceiling (V) | Maximum expected voltage (fully charged).  |

**Stage 1 to 2 Timing**

| Parameter                  | What it means                          |
|----------------------------|----------------------------------------|
| Gate 1 to Coil 2 Delay    | After gate 1 detects the projectile, wait this many microseconds before firing coil 2. |
| Coil 1 Pulse               | How long coil 1 stays energized (microseconds). |
| Coil 2 Pulse               | How long coil 2 stays energized (microseconds). |

**Stage 2 to 3 Timing**

| Parameter                  | What it means                          |
|----------------------------|----------------------------------------|
| Gate 2 to Coil 3 Delay    | After gate 2 detects the projectile, wait this many microseconds before firing coil 3. |
| Coil 3 Pulse               | How long coil 3 stays energized (microseconds). |

**Gate Distances**

| Parameter         | What it means                              |
|-------------------|--------------------------------------------|
| Gate 1 to 2 (mm) | Physical distance between gate 1 and gate 2. Used to calculate flight velocity. |
| Gate 2 to 3 (mm) | Physical distance between gate 2 and gate 3. Used to calculate muzzle velocity. |

**Power Source**

| Parameter                | What it means                                     |
|--------------------------|---------------------------------------------------|
| Capacitor Bank (µF)      | Total installed capacitance. Recorded with each run so you can compare results across capacitor upgrades. |
| Rail Source Active       | Checkbox. Tick it when a continuous rail supply is feeding the coils (in addition to, or instead of, the capacitor bank). The app stores the current V Ceiling value when checked, or 0 when unchecked. |

**Flyback / Brake Modules**

The rig uses a dedicated SiC flyback+brake module per coil. Each module has a
different series brake resistor ({0, 1, 2, 4, 10} Ω). Higher resistance makes
coil current decay faster after switch-off (reducing projectile "suck-back"),
but produces a larger voltage spike across the switch — verify your switch's
V_CE headroom before installing a higher-Ω module.

| Parameter              | What it means                                    |
|------------------------|--------------------------------------------------|
| Coil 1 Brake R (Ω)    | Brake resistor value of the flyback module currently installed on coil 1. Update this when you swap modules. |
| Coil 2 Brake R (Ω)    | Brake resistor value of the flyback module currently installed on coil 2. Update this when you swap modules. |

### Action Buttons

| Button          | What it does                                         |
|-----------------|------------------------------------------------------|
| **SAVE RUN**    | Saves the current test run and starts a new one.     |
| **CLEAR**       | Discards the current measurements without saving.    |
| **NEW SEQUENCE**| Starts a brand-new test sequence. Use this when you change something significant about the physical setup (different projectile, different coil, etc.) so that runs done under different conditions are grouped separately in the logs. |

---

## Typical Test Session

1. **Power on** the Raspberry Pi and start the app.
2. Open the **Fire Control** page on the touchscreen.
3. Open the **Configuration** page on a laptop if you want to adjust timing.
4. Check that the status shows **READY** (green).
5. Load a projectile into the breach.
6. Tap **ARM**. Status changes to **ARMED** (orange).
7. Tap **FIRE** (or press the physical trigger). Status changes to **FIRING**
   (red), then **COMPLETE** (blue) when the shot finishes.
8. Review the statistics on the right side of the screen.
9. Tap **SAVE** to record the data, or **CLEAR** to discard.
10. The system returns to **READY**. Repeat from step 5.

If the projectile gets stuck or only some gates trigger, tap **SAVE** to
record a partial run, or **CLEAR** to discard it.

---

## Tips

- **Both pages stay in sync.** If someone changes a parameter on the
  Configuration page, the Fire Control page sees the change instantly. You do
  not need to refresh.
- **The physical trigger button** works the same as the on-screen FIRE button.
  You must ARM on the touchscreen first.
- **Sequences group your runs.** When you tap NEW SEQUENCE, future runs get a
  new group ID. This makes it easy to compare sets of runs later (all runs in
  one sequence were done under the same conditions).
- **Missing stages are handled gracefully.** If coil 3 and gate 3 are not
  connected, the system still works normally for stages 1 and 2. Gate 3
  statistics will simply show `--`.

---

## Troubleshooting

| Problem                           | What to try                             |
|-----------------------------------|-----------------------------------------|
| Status is stuck on FIRING         | The system is waiting for a gate that never triggered. Tap **SAVE** to end the run with partial data, or **CLEAR** to discard. |
| Cannot arm (ARM button grayed out)| The system is not in READY state. Tap SAVE or CLEAR first to end any active run. |
| Statistics show `--` for all gates| The projectile may not have triggered any gate sensors. Check that the beam-break sensors are aligned and powered. |
| Browser shows OFFLINE             | The connection to the app was lost. Check that the app is still running in the terminal. The browser will reconnect automatically. |
| App won't start                   | Run `./setup_host_rpi.sh` again to verify all dependencies are installed. Check the terminal for error messages. |
