# Lessons Learned

Running log of non-obvious things that bit us, organised newest-first. Each
entry should be short: what happened, why it mattered, and the rule it
produced. If a lesson becomes obsolete (fixed at the root), strike it through
rather than deleting — the history is the point.

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
