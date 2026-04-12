# Developer Guide

## Architecture Overview

The sequencer is a Flask application with a threaded real-time backend. It
controls a three-stage electromagnetic accelerator via GPIO and reports
measurements through a browser-based UI over Server-Sent Events (SSE).

```
                       ┌──────────────────────────────┐
                       │         Flask App             │
                       │  (waitress, 8 threads)        │
                       ├──────────┬───────────────────┤
                       │ REST API │   SSE /api/stream  │
                       └────┬─────┴────────┬──────────┘
                            │              │
              ┌─────────────▼──────┐  ┌────▼────────────┐
              │     Sequencer      │  │  StatePublisher  │
              │  (state machine,   │──│  (fan-out queue  │
              │   timing threads)  │  │   per client)    │
              └────────┬───────────┘  └─────────────────┘
                       │
              ┌────────▼───────────┐
              │  HardwareInterface │
              ├────────────────────┤
              │  MockHardware  OR  │
              │  RealHardware      │
              └────────────────────┘
```

### Module Map

```
app/
├── __init__.py            App factory, startup config loading
├── config.py              GPIO pins, parameter defaults, FlaskConfig
├── models.py              SQLAlchemy models (ConfigSnapshot, EventLog)
├── sequencer.py           State machine, timing engine, StatePublisher, calculations
├── hardware/
│   ├── __init__.py        create_hardware() — auto-selects backend
│   ├── base.py            Abstract HardwareInterface
│   ├── mock.py            Simulated GPIO + gate events for off-Pi development
│   └── real.py            gpiozero/lgpio implementation for RPi 5
├── routes/
│   ├── api.py             REST endpoints + SSE stream
│   ├── touchscreen.py     Serves /
│   └── configuration.py   Serves /config
├── templates/             Jinja2 HTML (base, touchscreen, configuration)
└── static/
    ├── css/style.css      Dark theme, 800x480 touchscreen layout
    └── js/
        ├── common.js      SSE client, API helpers, formatters
        ├── touchscreen.js  Fire-control page logic
        └── configuration.js  Config page logic + auto-save
```

---

## State Machine

The sequencer progresses through four states:

```
  READY ──arm()──▶ ARMED ──fire()──▶ FIRING ──(all gates done)──▶ COMPLETE
    ▲                │                  │                            │
    │    disarm()    │     save/clear   │         save/clear         │
    └────────────────┴──────────────────┴────────────────────────────┘
```

| State      | Meaning                                               | Allowed actions        |
|------------|-------------------------------------------------------|------------------------|
| `READY`    | Idle. Can be armed.                                   | `arm()`                |
| `ARMED`    | Gate callbacks registered, external trigger active.   | `fire()`, `save()`, `clear()` |
| `FIRING`   | Coil 1 pulsed; awaiting gate events and auto-firing subsequent coils. | `save()`, `clear()` |
| `COMPLETE` | All detected gates have both leading and trailing edges. | `save()`, `clear()` |

State transitions and the current `RunData` are guarded by `self._lock`
(a `threading.Lock`).

---

## Firing Sequence (Timing Detail)

Physical layout: `coil_1 → gate_1 → coil_2 → gate_2 → coil_3 → gate_3`

```
User presses FIRE
  │
  ├─ record t_coil_0 (fire-command timestamp)
  ├─ GPIO coil 1 HIGH → record t_coil_1_on
  ├─ [Thread A] busy-wait COIL_1_PULSE_DURATION_US → GPIO coil 1 LOW → record t_coil_1_off
  │
  ▼ (projectile traverses coil 1, breaks gate 1 beam)
Gate 1 falling-edge interrupt
  ├─ record t_gate_1_on
  ├─ [Thread B] busy-wait GATE_1_COIL_2_DELAY_US → GPIO coil 2 HIGH → record t_coil_2_on
  │             busy-wait COIL_2_PULSE_DURATION_US → GPIO coil 2 LOW → record t_coil_2_off
  │
Gate 1 rising-edge interrupt
  └─ record t_gate_1_off
  │
  ▼ (projectile traverses coil 2, breaks gate 2 beam)
Gate 2 falling-edge interrupt
  ├─ record t_gate_2_on
  ├─ [Thread C] busy-wait GATE_2_COIL_3_DELAY_US → fire coil 3 → record on/off
  │
Gate 2 rising-edge interrupt
  └─ record t_gate_2_off
  │
  ▼ (repeat for gate 3 — final stage)
Gate 3 trailing edge → state = COMPLETE
```

### Why busy-wait?

`time.sleep()` has ~1 ms granularity on Linux. The coil delays are 500--2000 us,
and pulse widths down to 1000 us. `time.perf_counter_ns()` busy-wait gives
sub-microsecond precision at the cost of one CPU core per active wait. Each
wait is brief (< 2 ms) and runs in its own daemon thread, so the main Flask
threads and interrupt callbacks remain responsive.

### Threading model

| Thread           | Lifetime          | Purpose                                    |
|------------------|-------------------|--------------------------------------------|
| Main (waitress)  | Process lifetime  | HTTP request handling                      |
| SSE generator    | Per SSE client    | Blocks on `queue.get()`, yields events     |
| Gate callback    | Per gate edge     | gpiozero interrupt thread; records timestamp, spawns coil thread |
| Coil pulse       | Per coil fire     | Busy-waits for delay + pulse, then exits   |

Gate callbacks must return quickly so subsequent edge interrupts are not
delayed. The callback records the timestamp, then immediately spawns a
daemon thread for the coil delay + pulse.

---

## Hardware Abstraction

`app/hardware/base.py` defines `HardwareInterface` with these methods:

| Method                        | Purpose                              |
|-------------------------------|--------------------------------------|
| `setup()` / `cleanup()`      | Init/release pins and ADC            |
| `set_coil(num, state)`       | Drive coil output HIGH/LOW           |
| `register_gate_callback(num, edge, fn)` | Attach interrupt for falling/rising edge |
| `unregister_gate_callbacks()` | Remove all gate callbacks            |
| `register_trigger_callback(fn)` / `unregister_trigger_callback()` | External fire button |
| `read_coil_voltage(num)`     | ADC read (returns `float` or `None`) |

### Backend selection

`create_hardware()` in `app/hardware/__init__.py` picks the backend:

1. `COILGUN_HW=real` env var → `RealHardware`
2. `COILGUN_HW=mock` env var → `MockHardware`
3. Auto-detect: try `RealHardware` on Linux (imports gpiozero), fall back to `MockHardware`

### Mock backend

`MockHardware` simulates the full projectile pass. When `set_coil(n, True)`
is called, it schedules a simulated gate event for gate *n* after a fixed
delay (`_SIM_COIL_TO_GATE_DELAY_S = 3 ms`), followed by a trailing edge
after `_SIM_GATE_TRANSIT_S = 500 us`. This means the full ARM → FIRE →
COMPLETE cycle runs automatically on any platform.

Two additional methods are exposed for development use:

- `simulate_trigger_press()` — fires the external-trigger callback
- `simulate_gate_break(gate_num)` — manually triggers a gate event

These are wired up to `POST /api/mock/trigger` in the API.

### Real backend

`RealHardware` uses gpiozero on RPi 5:

- **Coils**: `OutputDevice` on GPIO 17, 27, 22
- **Gates**: `DigitalInputDevice` with `pull_up=True`, no bounce. `when_activated` = falling edge (beam break), `when_deactivated` = rising edge (beam restore).
- **External trigger**: `Button` with `pull_up=False` (active HIGH), 10 ms bounce.
- **ADC**: Stub returning `None`. See "Adding an ADC" below.

---

## SSE (Server-Sent Events)

`StatePublisher` maintains a list of `queue.Queue` objects, one per connected
SSE client. On every state change the sequencer calls `self._publish()`, which
builds a full snapshot and enqueues it to all subscribers.

The SSE endpoint (`GET /api/stream`) sends an initial snapshot immediately on
connect, then blocks on `queue.get(timeout=15)`. On timeout it emits a
`: keepalive` comment to prevent proxy/browser disconnects.

On the client side, `common.js` opens a single `EventSource` and dispatches
parsed snapshots to callbacks registered with `onStateUpdate(fn)`.

**Why full snapshots?** Both the touchscreen and configuration pages may be
open simultaneously. Sending the complete state (config + timestamps + stats +
voltages) on every event means either page always has current data with no
risk of drift.

---

## Database

SQLite via Flask-SQLAlchemy. DB file: `data/sequencer.db` (auto-created on
first run).

### config_snapshots

A new row is inserted on every parameter change, preserving full history.

| Column                      | Type    | Notes                     |
|-----------------------------|---------|---------------------------|
| `id`                        | Integer | PK, auto-increment        |
| `run_sequence_id`           | String  | Groups related runs        |
| `created_at`                | DateTime| UTC                        |
| `projectile_length_mm`      | Float   |                           |
| `projectile_mass_grams`     | Float   |                           |
| `v_coil_floor`              | Float   |                           |
| `v_coil_ceiling`            | Float   |                           |
| `gate_1_coil_2_delay_us`    | Float   |                           |
| `gate_2_coil_3_delay_us`    | Float   |                           |
| `coil_1_pulse_duration_us`  | Float   |                           |
| `coil_2_pulse_duration_us`  | Float   |                           |
| `coil_3_pulse_duration_us`  | Float   |                           |
| `gate_1_to_gate_2_distance_mm` | Float|                           |
| `gate_2_to_gate_3_distance_mm` | Float|                           |
| `capacitor_bank_size_uf`    | Float   | Metadata only — not read by firing path |
| `rail_source_active`        | Float   | Continuous rail voltage: 0 when off, `v_coil_ceiling` when on (not boolean) |
| `coil_1_brake_resistor_ohms`| Float   | Installed flyback brake resistor for coil 1 (Ω). Metadata only |
| `coil_2_brake_resistor_ohms`| Float   | Installed flyback brake resistor for coil 2 (Ω). Metadata only |

### event_logs

One row per saved firing run. All `t_*` fields are nanosecond timestamps from
`time.perf_counter_ns()` (session-relative, not wall-clock).

| Column              | Type       | Notes                        |
|---------------------|------------|------------------------------|
| `id`                | Integer    | PK                           |
| `run_sequence_id`   | String     | FK to sequence grouping      |
| `run_number`        | Integer    | Increments per sequence      |
| `config_snapshot_id`| Integer    | FK to config_snapshots.id    |
| `created_at`        | DateTime   | UTC                          |
| `t_coil_0`          | BigInteger | Fire command issued          |
| `t_gate_N_on/off`   | BigInteger | Beam-break leading/trailing  |
| `t_coil_N_on/off`   | BigInteger | Coil energise/de-energise    |

---

## REST API Reference

All endpoints are under `/api`.

| Method | Path              | Purpose                                    |
|--------|-------------------|--------------------------------------------|
| GET    | `/stream`         | SSE stream of state snapshots              |
| GET    | `/state`          | One-shot state snapshot (JSON)             |
| POST   | `/arm`            | Arm the system (READY → ARMED)             |
| POST   | `/fire`           | Fire coil 1 (ARMED → FIRING)              |
| POST   | `/save`           | Save current run to DB, return to READY    |
| POST   | `/clear`          | Abort run without saving, return to READY  |
| GET    | `/config`         | Current config parameters (JSON)           |
| POST   | `/config`         | Update parameters (JSON body), saves snapshot |
| GET    | `/sequence`       | Current run_sequence_id and run_number     |
| POST   | `/sequence/new`   | Generate new sequence UUID                 |
| GET    | `/history`        | Last 50 event logs for current sequence    |
| POST   | `/mock/trigger`   | Simulate external trigger (mock HW only)   |

---

## Velocity Calculations

Defined in `compute_stats()` (`app/sequencer.py`).

**Transit velocity** — projectile speed through a single gate's beam:

```
velocity (m/s) = projectile_length_mm * 1000 / transit_time_us
```

Where `transit_time_us = (t_gate_N_off - t_gate_N_on) / 1000`.

**Flight velocity** — average speed between two adjacent gates:

```
velocity (m/s) = gate_distance_mm * 1000 / flight_time_us
```

Where `flight_time_us = (t_gate_B_on - t_gate_A_on) / 1000` (leading edge
to leading edge).

---

## Development Workflow

### Windows (mock hardware)

```
pip install flask flask-sqlalchemy waitress
python run.py
```

Open `http://localhost:5000`. The mock backend auto-simulates projectile
passes when coils fire. Click ARM, then FIRE in the browser to run a full
simulated sequence.

### Raspberry Pi (real hardware)

```
./setup_host_rpi.sh
source .venv/bin/activate
python run.py
```

To force mock mode on the Pi (e.g. for testing without wiring):

```
COILGUN_HW=mock python run.py
```

### Resetting the database

Delete `data/sequencer.db` and restart the app. Tables are recreated
automatically, and default config is seeded.

---

## Extending the Code

### Adding an ADC

1. Install the appropriate library (e.g. `pip install adafruit-circuitpython-mcp3xxx`
   or use the `spidev` + `MCP3008` module from gpiozero).
2. Implement `read_coil_voltage()` in `app/hardware/real.py`. The method
   receives `coil_num` (1, 2, or 3) and should return voltage as a `float`.
   Map coil numbers to ADC channels using `config.ADC_CHANNELS`.
3. The returned values will automatically appear in SSE snapshots under
   `coil_voltages` and can be used by the frontend for the "Ready" indicator.

### Adding a fourth stage

1. Add GPIO pins in `app/config.py` (`GPIO_COIL_4`, `GPIO_GATE_4`).
2. Add timestamp fields to `EventLog` in `models.py` (`t_gate_4_on`, etc.)
   and update `TIMESTAMP_FIELDS` in `sequencer.py`.
3. Extend `_on_gate_leading()` so gate 3 triggers coil 4 (add entry to the
   `next_coil` dict and the `delay_key` dict).
4. Add the new config parameter (`GATE_3_COIL_4_DELAY_US`, etc.) to `DEFAULTS`,
   `ConfigSnapshot.PARAM_KEYS`, and the configuration page HTML/JS.
5. Add the new gate row in the touchscreen stats tables.
6. Delete `data/sequencer.db` to recreate tables with the new columns.

### Changing the UI

The frontend is plain HTML/CSS/JS with no build step. Edit the templates and
static files directly. The SSE snapshot format is defined by
`Sequencer.snapshot()` — if you add new fields there, they become available
to the JS callbacks registered via `onStateUpdate(fn)`.

The CSS is designed for an 800x480 display. Key layout classes:
- `.ts-layout` — 2-column grid (220px controls | remaining stats)
- `.cfg-layout` — 2-column grid for config sections
- `.btn` — minimum 52px height for touch targets
