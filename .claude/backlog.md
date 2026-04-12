# "Just in case" Backlog

Known issues that are real but not worth fixing now — either the window to
hit them is vanishingly small, the consequences are mild, or the fix costs
more than the bug. Kept here so we don't rediscover them from scratch later.

Promote an item out of this file when: (a) it actually bites someone, (b) the
surrounding code changes in a way that widens the window, or (c) the system
gains a multi-user / remote-control mode that makes concurrent lifecycle
calls realistic.

---

## TOCTOU hazards in the sequencer lifecycle (noted 2026-04-12)

Audit during the gate-1 double-fire fix surfaced four TOCTOU issues in
`app/sequencer.py`. The `seen_leading` / `seen_trailing` dedup itself is
race-free; these all live in adjacent code. None is reachable without a
human deliberately racing the firmware at µs timescales, which isn't the
threat model for a single-operator field tool.

1. **Delayed coil fires after disarm/clear/save.** `_delayed_coil_fire`
   busy-waits hundreds of µs and then calls `hw.set_coil(..., True)` with no
   re-check. If the operator hits Save/Clear during that window, the coil
   still fires. Safety-relevant in principle; in practice nobody clears
   mid-run. **Fix sketch:** pass `gen` and the `run` reference into the
   worker, re-validate under `_lock` right before the GPIO write, hold the
   lock across `set_coil` (one fast write).

2. **`_delayed_coil_fire` / `_coil_pulse_thread` `_current_run` TOCTOU.**
   `if self._current_run: self._current_run.record(...)` — classic two-read
   hazard. Produces `AttributeError` on `NoneType.record` if another thread
   nulls `_current_run` between the two reads. Dissolves naturally under fix
   #1 (local-bind `run` at schedule time).

3. **`snapshot()` `_current_run` TOCTOU.** Reads `self._current_run` four
   times unlocked. Same `NoneType` crash, but on a SocketIO broadcast
   instead of a timing path. One-shot local-bind fixes it.

4. **`_all_expected_gates_done()` unlocked read.** Reads `self._current_run`
   and then `.timestamps` outside the lock. Same local-bind fix, or fold
   into the trailing handler's locked section.

**When to promote:** if the sequencer ever grows a "remote disarm" or
scripted test-runner mode where Save/Clear can be issued mid-run by
something other than a human finger, revisit #1 first — that's the only one
with hardware-safety consequences.

## Config dict mutation during a run (noted 2026-04-12)

`Sequencer._config` is a plain dict mutated by the routes layer without any
lock. A config update during an in-flight run could interleave unrelated
reads elsewhere. Operators don't tune mid-run, so this is theoretical.

**Fix when needed:** snapshot the config into the `RunData` at `arm()` time
and have the timing path read exclusively from the snapshot.
