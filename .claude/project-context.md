# Coil-Gun Sequencer — Project Context for Claude

Persistent notes for future Claude sessions working in this repo. Keep terse;
prefer linking to code/specs over duplicating them.

## What this is

A multi-stage electromagnetic accelerator ("coil gun") sequencer and field-test
tool. Flask + Flask-SocketIO backend, touchscreen-friendly web UI, SQLite for
run history. Runs on a Raspberry Pi 5 in the field; development on Windows
uses a mock hardware backend.

## Authoritative specs

- `coil_gun_sequencer.md` — behavioural spec, state machine, event log fields
- `coil_gun_sequencer_configuration.md` — config keys and their units
- `developer_guide.md` — architecture, module layout, testing notes
- `user_guide.md` — operator-facing docs

If those documents disagree with this file, the `.md` specs win — update this
file to match, don't the other way round.

## Architecture at a glance

- `app/sequencer.py` — state machine + timing-critical engine. Gate callbacks
  run on hardware interrupt threads; coil pulses run on dedicated threads
  using `perf_counter_ns` busy-waits (no `time.sleep` for µs-scale timing).
- `app/hardware/base.py` — abstract hardware interface.
  - `mock.py` — dev backend (Windows / Linux without GPIO)
  - `real.py`  — `gpiozero` + ADC backend on the Pi
- `app/routes/` — Flask blueprints (REST + SocketIO)
- `app/models.py` — SQLAlchemy models for runs, configs, event log
- `run.py` — entry point

## Timing / hardware ground truth

- Gate inputs: **idle LOW, active HIGH**. Beam break drives the line HIGH
  (rising edge = leading edge); beam restore falls back to LOW (falling
  edge = trailing edge). Polarity was flipped on 2026-04-16 after
  replacing the mismatched gate 1 sensor — prior to that, gate 1 was
  idle-HIGH and gate 2 was idle-LOW and 93 %+ of persisted runs had
  negative `gate_N_transit_us`. The spec file may still describe the
  old convention; the code is correct.
- `GATE_1_COIL_2_DELAY_US` — gate 1 fires coil **2** (not coil 1)
- `GATE_2_COIL_3_DELAY_US` — gate 2 fires coil 3
- `COIL_3_PULSE_DURATION_US` — exists in code, was missing from original spec
- Coil 1: fires immediately on `fire()` command, no gate involved
- Coil 3 and Gate 3: not physically attached yet; app handles missing
  hardware gracefully
- ADC for voltage monitoring: not purchased yet; interface is stubbed and
  `read_coil_voltage()` may return `None`
- One dedicated `SiC flyback diode + brake resistor` module per coil.
  Brake-R is a hardware swap (discrete modules: `{0, 1, 2, 4, 10} Ω`), logged
  per-snapshot as `coil_N_brake_resistor_ohms`. Metadata only — the firing
  path doesn't read it. Trades V_CE spike magnitude for faster freewheel
  decay (shorter projectile suck-back tail). Verify switch V_CE headroom
  before installing a higher-Ω module: spike ≈ V_rail + I_coil × R_brake.
- No debounce >10 µs anywhere in the timing path

## State machine

```
READY → arm() → ARMED → fire() → FIRING → (all gates done) → COMPLETE
                  ↓                  ↓                            ↓
              disarm()          disarm()                 save_run() / clear_run()
                  ↓                  ↓                            ↓
                READY              READY                        READY
```

`_run_generation` is bumped on every arm so stale gate callbacks from a
previous run get dropped.

## Run lifecycle & dedup (per-run state)

`RunData` is created fresh in `arm()` and destroyed in `save_run()` /
`clear_run()`. Any dedup/guard state that should reset between runs belongs
**on `RunData`**, not the `Sequencer` — that way reset is automatic and there
is no risk of forgetting to clear it in one of the lifecycle methods.

Current per-run state:
- `timestamps` — raw event timestamps (ns, from `perf_counter_ns`)
- `seen_leading` / `seen_trailing` — gate-edge dedup sets, mutated only while
  the `Sequencer._lock` is held

## Threading model

- Main thread: Flask request handling, state transitions
- Hardware interrupt threads: gate/trigger callbacks (`_on_gate_leading`,
  `_on_gate_trailing`, `_on_external_trigger`)
- Short-lived daemon threads: `_delayed_coil_fire`, `_coil_pulse_thread`

`Sequencer._lock` guards: state transitions, `_current_run` swaps,
`_run_generation` reads/writes, and per-run dedup set check-and-add. Side
effects (thread spawns, socket publishes, logging) happen **outside** the
lock — holding the lock across a thread spawn or socketio emit risks
deadlocks and latency spikes that matter at µs timescales.

## Dev workflow (Windows)

- `python run.py` — starts Flask + SocketIO on mock hardware
- `python -m pytest tests/` — test suite
- Deployment to Pi uses `setup_host_rpi.sh`

See `.claude/lessons-learned.md` for environment quirks and debugging gotchas.
