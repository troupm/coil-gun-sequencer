# Lessons Learned

Running log of non-obvious things that bit us, organised newest-first. Each
entry should be short: what happened, why it mattered, and the rule it
produced. If a lesson becomes obsolete (fixed at the root), strike it through
rather than deleting — the history is the point.

---

## 2026-04-16 — Mismatched gate sensor polarity produced negative transit times

**Symptom:** `gate_1_transit_us` and `gate_2_transit_us` were negative on
~95 % of persisted runs — 427/443 for gate 1 and 372/399 for gate 2.
Because `compute_stats` only emitted a `gate_N_transit_velocity_ms` when
`transit_us > 0`, per-gate transit velocity was silently missing from
almost every row, and the velocity-optimisation analysis had to fall back
on flight-time-only metrics.

**Cause:** Two things stacked:

1. The sequencer assumed *idle-HIGH* gate sensors (`pull_up=True` with
   falling edge = beam break). Gate 2's actual sensor was idle-LOW
   (rising edge = beam break). Operator belief about idle state didn't
   match what the GPIO pin saw.
2. Gates 1 and 2 were different sensor types — but the operator only
   checked one with a multimeter and assumed both matched. Once gate 1
   was replaced with the gate-2 type, **both** gates started producing
   negative transit times; that simultaneous flip is what confirmed the
   polarity diagnosis.

**Fix:**

- `app/sequencer.py`: registered `_on_gate_leading` on the `"rising"`
  callback and `_on_gate_trailing` on `"falling"` — beam-break is now
  correctly read as the rising edge.
- `app/hardware/real.py`: `pull_up=False` on the gate inputs, and the
  `when_activated`/`when_deactivated` mapping swapped so the `"rising"`
  and `"falling"` strings still name the physical edge direction.
- `app/hardware/mock.py`: beam-break simulation now fires the `"rising"`
  callback (was `"falling"`) so the test cascade mirrors real hardware.
- `compute_stats` and `_compute_run_velocities` now use
  `abs(transit_us)` with a 10-µs noise floor, so the 800+ historic rows
  with negative transit unlock their `gate_N_transit_velocity_ms`
  metric for analysis. Signed `_us` stays so an analyst can still see
  which rows were recorded under the old polarity.
- Added
  `test_gate_transit_is_positive_after_beam_break_then_restore` to pin
  down the convention; it drives the full mock cascade and asserts
  `off > on` on all three gates.

**Rules:**

1. When swapping a sensor, **always verify polarity with a scope on the
   GPIO header pin itself**, not the sensor board's output — any
   buffering/inversion between them can silently flip the signal the Pi
   actually sees.
2. If more than one sensor of the "same role" is installed, confirm all
   of them are the same type *and* idle-state before trusting either.
3. Asymmetry across gates (e.g. gate 1 behaving differently from gate 2)
   is a polarity-mismatch signal, not run-to-run noise.
4. For any bi-directional edge event: if operator belief and persisted
   data disagree on which direction is "normal", trust the persisted
   data. A 95 % sign-inversion rate across hundreds of runs is not
   noise.

---

## 2026-04-12 — Sensor bounce double-fired coil 2

**Symptom:** Coil 2 energised multiple times per test run on hardware, even
though `fire()` and gate 1 were only triggered once by the operator.

**Cause:** `_on_gate_leading` spawned a `_delayed_coil_fire` thread on *every*
falling-edge callback with no dedup. Gate 1's beam-break sensor produced more
than one falling edge per projectile pass (likely jitter around the comparator
threshold as the projectile's edge crosses the beam). Each edge scheduled
another coil-2 pulse. `record()` also silently overwrote `t_gate_1_on` to the
*last* bounce, skewing any velocity math that happened to look OK visually.

**Fix:** First-edge-wins dedup via `RunData.seen_leading` / `seen_trailing`
sets. Check-and-add happens atomically under `Sequencer._lock`; downstream
thread spawn happens outside the lock. Per-run state on `RunData` means reset
is automatic (the whole `RunData` is replaced in `arm()`).

**Rule:** Any interrupt-driven edge event in this system needs explicit first-
edge dedup. Sensors bounce. Don't rely on hardware debounce alone — the spec
caps debounce at 10 µs and a bounce that wide still fits inside a 300 µs
coil-2 delay.

**Related rule:** Per-run lifecycle state (flags, sets, counters that should
reset between runs) goes on `RunData`, not `Sequencer`. `arm()` creates a
fresh `RunData`, so you get reset-on-new-run for free and can't forget to
clear it in `save_run` / `clear_run` / `disarm`.

---

## Windows environment quirks

- Use forward slashes in paths when invoking Python from bash; the repo's
  bash shell is MSYS-flavoured and mixing `\` and `/` in the same argument
  breaks path parsing in some tools.
- Kill a stuck dev server with `taskkill //F //IM python.exe` (note the
  doubled slashes — MSYS-bash would otherwise rewrite `/F` as a path).
- `gpiozero` isn't installable on Windows; `app/hardware/mock.py` is the only
  hardware backend that works during dev. `create_app()` selects the backend
  automatically based on platform.
- SQLite file locks survive process crashes on Windows more stubbornly than
  on Linux — if `data/*.db` becomes unopenable after a hard kill, wait a few
  seconds before retrying or delete the `-journal` file.

---

## Timing / hardware rules of thumb

- Never use `time.sleep()` in the timing path. Always `perf_counter_ns` busy-
  wait in a dedicated daemon thread.
- Don't hold `Sequencer._lock` across a `socketio.emit()` or a
  `threading.Thread(...).start()` — both can take unpredictable time and the
  lock is on the hot path for gate callbacks.
- Prefer capturing the reference timestamp *inside* the lock and passing it
  to the worker thread, rather than having the worker call `perf_counter_ns`
  itself — this keeps the "when did gate N fire" anchor consistent with the
  recorded timestamp even if thread startup is delayed.
