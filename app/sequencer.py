"""Core sequencer engine – state machine, precise timing, gate callbacks.

Design priorities (from spec):
  * Capture every event with as little latency as possible.
  * Use dedicated threads for gate monitoring (via HW interrupt callbacks).
  * No blind sleep loops – busy-wait with perf_counter_ns for µs precision.
  * No debounce >10 µs.
"""

import enum
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from app.hardware.base import HardwareInterface

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

class State(str, enum.Enum):
    READY = "ready"
    ARMED = "armed"
    FIRING = "firing"
    COMPLETE = "complete"


# ---------------------------------------------------------------------------
# Run data (in-memory representation of a single firing test)
# ---------------------------------------------------------------------------

TIMESTAMP_FIELDS = [
    "t_coil_0",
    "t_gate_1_on", "t_gate_1_off",
    "t_gate_2_on", "t_gate_2_off",
    "t_gate_3_on", "t_gate_3_off",
    "t_coil_1_on", "t_coil_1_off",
    "t_coil_2_on", "t_coil_2_off",
    "t_coil_3_on", "t_coil_3_off",
]


@dataclass
class RunData:
    run_sequence_id: str
    run_number: int
    timestamps: Dict[str, Optional[int]] = field(default_factory=lambda: {
        f: None for f in TIMESTAMP_FIELDS
    })
    # Gate-edge dedup: which gates have already had their leading/trailing
    # edge handled in *this* run. Prevents sensor bounce or jitter from
    # re-firing a downstream coil or overwriting a captured timestamp.
    # Mutated only while the Sequencer's _lock is held.
    seen_leading: set = field(default_factory=set)
    seen_trailing: set = field(default_factory=set)
    # Count of coil pulses that have been scheduled but have not yet
    # finished their OFF phase. Incremented when a pulse is scheduled
    # (fire / gate-leading / manual fire); decremented when the pulse
    # thread turns the coil OFF. The run is not "complete" while any
    # coil is still mid-pulse — without this counter, a gate-2 trailing
    # edge that arrived before gate 3 was reached would flip state to
    # COMPLETE during the gate_2_coil_3_delay_us window, and any
    # downstream save/disarm could interrupt coil 3 before it fired.
    pending_coils: int = 0
    # Set True by a gate-trailing event that *would* normally complete
    # the run (gate 3 trailing, or all triggered gates trailed). The
    # actual FIRING → COMPLETE transition is deferred until pending_coils
    # also drops to zero, so a pulse scheduled but not yet fired can't
    # be interrupted by a save/disarm triggered off a premature COMPLETE.
    completion_ready: bool = False

    def record(self, event: str) -> int:
        """Record *event* at the current instant. Returns the ns timestamp."""
        t = time.perf_counter_ns()
        self.timestamps[event] = t
        return t


# ---------------------------------------------------------------------------
# Calculations
# ---------------------------------------------------------------------------

def _ns_to_us(ns: int) -> float:
    return ns / 1_000.0


def compute_stats(run: RunData, cfg: dict) -> dict:
    """Derive transit times, flight times, and velocities from raw timestamps."""
    ts = run.timestamps
    stats: Dict[str, Any] = {}

    proj_len_mm = cfg["projectile_length_mm"]

    # --- Gate transit times (trailing - leading = beam-break duration) ----
    # Rows from before the 2026-04-16 polarity fix have off < on (negative
    # transit); magnitude is still the real beam-break duration. Use abs()
    # for the velocity computation so those rows salvage-decode cleanly,
    # but keep the signed `_us` field so an operator/analyst can still see
    # the inversion. A lower bound filters out sub-10-µs noise/glitches.
    for g in (1, 2, 3):
        on = ts.get(f"t_gate_{g}_on")
        off = ts.get(f"t_gate_{g}_off")
        if on is not None and off is not None:
            transit_ns = off - on
            transit_us = _ns_to_us(transit_ns)
            stats[f"gate_{g}_transit_us"] = round(transit_us, 2)
            if abs(transit_us) >= 10.0:
                # velocity = length / time  →  (mm/1000) / (µs/1e6) = mm*1000/µs
                vel = proj_len_mm * 1_000.0 / abs(transit_us)
                stats[f"gate_{g}_transit_velocity_ms"] = round(vel, 3)

    # --- Flight times (leading edge to leading edge between adjacent gates)
    gate_pairs = [
        (1, 2, "gate_1_to_gate_2_distance_mm"),
        (2, 3, "gate_2_to_gate_3_distance_mm"),
    ]
    for g_a, g_b, dist_key in gate_pairs:
        on_a = ts.get(f"t_gate_{g_a}_on")
        on_b = ts.get(f"t_gate_{g_b}_on")
        if on_a is not None and on_b is not None:
            flight_ns = on_b - on_a
            flight_us = _ns_to_us(flight_ns)
            stats[f"gate_{g_a}_to_gate_{g_b}_flight_us"] = round(flight_us, 2)
            dist_mm = cfg.get(dist_key, 0.0)
            if flight_us > 0 and dist_mm > 0:
                vel = dist_mm * 1_000.0 / flight_us
                stats[f"gate_{g_a}_to_gate_{g_b}_velocity_ms"] = round(vel, 3)

    return stats


# ---------------------------------------------------------------------------
# State publisher (SocketIO fan-out)
# ---------------------------------------------------------------------------

class StatePublisher:
    """Pushes state snapshots and targeted events to clients via SocketIO."""

    def __init__(self) -> None:
        self._socketio = None

    def init_socketio(self, socketio) -> None:
        self._socketio = socketio

    def publish(self, data: dict) -> None:
        """Broadcast a full state snapshot to all connected clients."""
        if self._socketio:
            self._socketio.emit("state_update", data)

    def emit(self, event: str, data: dict) -> None:
        """Emit a named event (e.g. run_saved, config_updated)."""
        if self._socketio:
            self._socketio.emit(event, data)


# ---------------------------------------------------------------------------
# Sequencer
# ---------------------------------------------------------------------------

class Sequencer:
    """Manages the full coil-gun firing lifecycle."""

    def __init__(self, hardware: HardwareInterface, publisher: StatePublisher) -> None:
        self.hw = hardware
        self.publisher = publisher

        # Current state
        self._state = State.READY
        self._lock = threading.Lock()

        # Run tracking
        self._run_sequence_id: str = str(uuid.uuid4())
        self._run_number: int = 0
        self._current_run: Optional[RunData] = None
        self._run_generation: int = 0  # incremented on each arm; guards late callbacks

        # Active config (loaded from DB or defaults on startup)
        self._config: dict = {}

        # Config snapshot id for current config
        self._config_snapshot_id: Optional[int] = None

        # Latest computed statistics (persisted across state transitions
        # so the UI can display last-run stats even in READY state)
        self._last_stats: dict = {}

    # -- properties -------------------------------------------------------

    @property
    def state(self) -> State:
        return self._state

    @property
    def run_sequence_id(self) -> str:
        return self._run_sequence_id

    @property
    def run_number(self) -> int:
        return self._run_number

    @property
    def config(self) -> dict:
        return dict(self._config)

    @config.setter
    def config(self, value: dict) -> None:
        self._config = dict(value)

    @property
    def config_snapshot_id(self) -> Optional[int]:
        return self._config_snapshot_id

    @config_snapshot_id.setter
    def config_snapshot_id(self, value: int) -> None:
        self._config_snapshot_id = value

    # -- state snapshot (sent to SSE clients) -----------------------------

    def snapshot(self) -> dict:
        """Build a full state snapshot for the frontend."""
        ts = {}
        stats = {}
        run_num = self._run_number
        if self._current_run:
            ts = dict(self._current_run.timestamps)
            stats = compute_stats(self._current_run, self._config)
            run_num = self._current_run.run_number

        # Read coil voltages (non-blocking)
        voltages = {}
        for c in (1, 2, 3):
            voltages[f"coil_{c}"] = self.hw.read_coil_voltage(c)

        return {
            "state": self._state.value,
            "run_sequence_id": self._run_sequence_id,
            "run_number": run_num,
            "timestamps": ts,
            "stats": stats if stats else self._last_stats,
            "config": dict(self._config),
            "coil_voltages": voltages,
        }

    def _publish(self) -> None:
        self.publisher.publish(self.snapshot())

    # -- lifecycle --------------------------------------------------------

    def set_run_sequence(self, seq_id: str) -> None:
        self._run_sequence_id = seq_id
        self._run_number = 0
        self._publish()

    def new_run_sequence(self) -> str:
        seq_id = str(uuid.uuid4())
        self.set_run_sequence(seq_id)
        return seq_id

    # -- state transitions ------------------------------------------------

    def arm(self) -> bool:
        """Arm the system. Returns True on success."""
        with self._lock:
            if self._state != State.READY:
                log.warning(f"Cannot arm: state is {self._state}")
                return False
            self._state = State.ARMED
            self._run_number += 1
            self._run_generation += 1
            self._current_run = RunData(
                run_sequence_id=self._run_sequence_id,
                run_number=self._run_number,
            )

        # Register gate callbacks
        self._register_gate_callbacks()

        # Register external trigger
        self.hw.register_trigger_callback(self._on_external_trigger)

        log.info(f"ARMED – run #{self._run_number}")
        self._publish()
        return True

    def fire(self) -> bool:
        """Fire coil 1 immediately. Returns True on success."""
        with self._lock:
            if self._state != State.ARMED:
                log.warning(f"Cannot fire: state is {self._state}")
                return False
            self._state = State.FIRING
            # Capture run ref and config inside lock so concurrent
            # disarm/clear cannot null them before we use them.
            run = self._current_run
            pulse_us = self._config["coil_1_pulse_duration_us"]
            run.pending_coils += 1

        # Record the fire command timestamp
        run.record("t_coil_0")

        # Energise coil 1 immediately
        self.hw.set_coil(1, True)
        run.record("t_coil_1_on")

        log.info("FIRE – coil 1 energised")
        self._publish()

        # Coil 1 pulse duration handled in a dedicated thread
        threading.Thread(
            target=self._coil_pulse_thread,
            args=(1, pulse_us, run),
            daemon=True,
        ).start()

        return True

    def disarm(self) -> None:
        """Disarm all coils and return to READY."""
        self.hw.set_coil(1, False)
        self.hw.set_coil(2, False)
        self.hw.set_coil(3, False)
        self.hw.unregister_gate_callbacks()
        self.hw.unregister_trigger_callback()
        with self._lock:
            self._state = State.READY
        log.info("DISARMED – state READY")
        self._publish()

    def save_run(self) -> Optional[dict]:
        """Finalise the current run and return its data for DB persistence.

        Disarms coils and transitions to READY.  The run reference is
        claimed atomically under the lock so concurrent save/clear calls
        cannot produce duplicates.
        """
        with self._lock:
            run = self._current_run
            if run is None:
                return None
            # Claim the run — no other thread can save it after this.
            self._current_run = None

        # Compute final stats (safe: we own the only ref to *run*)
        stats = compute_stats(run, self._config)
        self._last_stats = stats

        result = {
            "run_sequence_id": run.run_sequence_id,
            "run_number": run.run_number,
            "config_snapshot_id": self._config_snapshot_id,
            "timestamps": dict(run.timestamps),
            "stats": stats,
        }

        self.disarm()
        return result

    def clear_run(self) -> None:
        """Abort the current run without saving. Return to READY."""
        with self._lock:
            self._current_run = None
        self._last_stats = {}
        self.disarm()

    # -- internal: gate callbacks -----------------------------------------

    def _register_gate_callbacks(self) -> None:
        gen = self._run_generation  # capture for closure
        for g in (1, 2, 3):
            gate_num = g

            def _on_leading(gn=gate_num, g=gen):
                self._on_gate_leading(gn, g)

            def _on_trailing(gn=gate_num, g=gen):
                self._on_gate_trailing(gn, g)

            # Gates are idle-LOW active-HIGH: beam-break drives the line HIGH
            # (rising edge = leading), beam-restore lets it fall back to LOW
            # (falling edge = trailing). See .claude/lessons-learned.md
            # 2026-04-16 for how this was diagnosed.
            self.hw.register_gate_callback(gate_num, "rising", _on_leading)
            self.hw.register_gate_callback(gate_num, "falling", _on_trailing)

    def _on_gate_leading(self, gate_num: int, gen: int) -> None:
        """Handle beam-break leading edge (rising edge on GPIO).

        The downstream coil's firing delay is measured from *this* edge —
        the leading (beam-break) edge of the gate — not the trailing
        edge. gate_N_coil_(N+1)_delay_us is the time from beam-break to
        coil ON.

        First-edge-wins: repeat callbacks for the same gate within a run
        (sensor bounce, EMI jitter) are dropped under the lock so coil
        pulses and timestamps can't be double-counted.
        """
        # Claim the edge atomically: only the first caller for this gate
        # gets to record the timestamp and schedule the downstream coil.
        with self._lock:
            run = self._current_run
            if run is None or gen != self._run_generation:
                return  # stale callback from a previous run
            if gate_num in run.seen_leading:
                # Bounced/repeat edge — ignore.
                log.debug(f"Gate {gate_num} LEADING edge ignored (already handled)")
                return
            run.seen_leading.add(gate_num)
            t = run.record(f"t_gate_{gate_num}_on")

            # Read everything we need for the downstream pulse while still
            # holding the lock, so a concurrent clear/save can't race us.
            next_coil = {1: 2, 2: 3}.get(gate_num)
            delay_us: Optional[float] = None
            pulse_us: Optional[float] = None
            if next_coil is not None:
                delay_key = {1: "gate_1_coil_2_delay_us",
                             2: "gate_2_coil_3_delay_us"}[gate_num]
                delay_us = self._config[delay_key]
                pulse_us = self._config[f"coil_{next_coil}_pulse_duration_us"]
                # Mark the pulse as in-flight before releasing the lock,
                # so a gate trailing edge observed in the interim can't
                # flip state to COMPLETE while the pulse is still pending.
                run.pending_coils += 1

        # Side effects outside the lock.
        log.info(f"Gate {gate_num} LEADING edge @ {t}")
        self._publish()

        if next_coil is None:
            return

        # Fire next coil after precise delay in a dedicated thread.
        # *t* above is the LEADING-edge timestamp; the delay is measured
        # from that point, not from the trailing edge.
        threading.Thread(
            target=self._delayed_coil_fire,
            args=(next_coil, t, delay_us, pulse_us, run),
            daemon=True,
        ).start()

    def _on_gate_trailing(self, gate_num: int, gen: int) -> None:
        """Handle beam-restore trailing edge (falling edge on GPIO).

        Only records the timestamp — the downstream coil firing is driven
        off the *leading* edge, so this handler never schedules a pulse.

        First-edge-wins dedup, same rationale as _on_gate_leading: without
        this, a bouncing sensor would overwrite t_gate_N_off with the last
        bounce and silently corrupt transit-velocity calculations.
        """
        with self._lock:
            run = self._current_run
            if run is None or gen != self._run_generation:
                return  # stale callback from a previous run
            if gate_num in run.seen_trailing:
                log.debug(f"Gate {gate_num} TRAILING edge ignored (already handled)")
                return
            run.seen_trailing.add(gate_num)
            run.record(f"t_gate_{gate_num}_off")

        log.info(f"Gate {gate_num} TRAILING edge")
        self._publish()

        # Only gate 3's trailing edge is the cascade's "done" signal.
        # Treating "gate N trailed + no other gate waiting" (the old
        # _all_expected_gates_done check) as completion is unsafe — at
        # the moment gate 1 trails, gate 2/3 simply haven't fired yet
        # (their `on` is None), so the old check trivially passed and
        # flipped state to COMPLETE before the downstream coils even ran.
        # Now the only trailing edge that can complete a run is gate 3's,
        # and the actual transition additionally requires pending_coils
        # == 0 (see `_check_and_complete`).
        if gate_num == 3:
            with self._lock:
                run = self._current_run
                if run is not None and self._state == State.FIRING:
                    run.completion_ready = True
        self._check_and_complete()

    def _check_and_complete(self) -> None:
        """Perform the FIRING → COMPLETE transition if all conditions hold.

        Conditions:
          1. Gate trailing has signalled readiness (completion_ready).
          2. No coil pulse is still in-flight (pending_coils == 0).

        Called from both `_on_gate_trailing` (where condition 1 may
        become true) and the coil pulse thread (where condition 2 may
        become true). Either edge can be the one that completes the run.
        """
        should_publish = False
        with self._lock:
            if self._state != State.FIRING:
                return
            run = self._current_run
            if run is None:
                return
            if not run.completion_ready:
                return
            if run.pending_coils > 0:
                return
            self._state = State.COMPLETE
            log.info("RUN COMPLETE")
            should_publish = True
        if should_publish:
            self._publish()

    # -- internal: precise coil timing ------------------------------------

    def _delayed_coil_fire(
        self,
        coil_num: int,
        reference_ns: int,
        delay_us: float,
        pulse_us: float,
        run_ref: "RunData",
    ) -> None:
        """Busy-wait *delay_us* past the gate LEADING-edge timestamp, then pulse.

        *reference_ns* must be the leading-edge timestamp captured by
        `_on_gate_leading` (not the trailing edge) — the firing delay is
        measured from beam-break, per the configuration semantics.

        *run_ref* is the RunData for the run that scheduled this pulse.
        We pair the pulse against this reference so that a concurrent
        clear/save (which nulls `_current_run`) doesn't cause us to stop
        short or fire into a stale run.
        """
        target_ns = reference_ns + int(delay_us * 1_000)

        # Busy-wait for µs-level precision
        while time.perf_counter_ns() < target_ns:
            pass

        self.hw.set_coil(coil_num, True)
        with self._lock:
            # Record into the run we were scheduled against. If save/clear
            # already rotated `_current_run`, still fire the coil (the
            # caller committed to this pulse when it scheduled us) but
            # skip the timestamp — it would land in a stale run.
            if self._current_run is run_ref:
                run_ref.record(f"t_coil_{coil_num}_on")
        log.info(f"Coil {coil_num} ON (after {delay_us} µs delay)")
        self._publish()

        # Now hold for pulse duration and release.
        self._coil_pulse_thread(coil_num, pulse_us, run_ref)

    def _coil_pulse_thread(
        self,
        coil_num: int,
        pulse_us: float,
        run_ref: "RunData",
    ) -> None:
        """Hold coil HIGH for *pulse_us*, then turn off.

        Decrements the pending-coils counter on *run_ref* under the lock
        once the coil is released, then drives a completion check — this
        is what lets `_check_and_complete` safely transition to COMPLETE
        only after every scheduled pulse has finished.
        """
        target_ns = time.perf_counter_ns() + int(pulse_us * 1_000)
        while time.perf_counter_ns() < target_ns:
            pass

        self.hw.set_coil(coil_num, False)
        with self._lock:
            if self._current_run is run_ref:
                run_ref.record(f"t_coil_{coil_num}_off")
            # Always decrement — the pending count is on *run_ref*, not
            # `self._current_run`, so a rotated run doesn't leak it.
            run_ref.pending_coils -= 1
        log.info(f"Coil {coil_num} OFF (after {pulse_us} µs pulse)")
        self._publish()

        # A pulse ending can be the last step needed to complete the run.
        self._check_and_complete()

    # -- internal: external trigger ---------------------------------------

    def _on_external_trigger(self) -> None:
        """Callback for the physical fire button."""
        log.info("External trigger pressed")
        self.fire()

    # -- manual component tests (Manual page) -----------------------------
    #
    # These two methods exist so an operator can exercise coil 2, coil 3,
    # gate 1, and gate 2 individually for pre-flight checks — e.g. to
    # confirm coil 3's wiring after it was physically attached without
    # having to send a real projectile through the full cascade.
    #
    # Both require the sequencer to be ARMED (or already FIRING); they
    # will NOT auto-arm, because arm() is what allocates the RunData
    # that captures the resulting timestamps and enforces dedup. After
    # a manual test the operator is expected to CLEAR or SAVE, same as
    # a normal run.

    def manual_fire_coil(self, coil_num: int) -> bool:
        """Fire coil 2 or 3 for its configured pulse duration, right now.

        Bypasses the gate cascade — the coil energises immediately,
        holds for coil_N_pulse_duration_us, and releases. Records
        t_coil_N_on / t_coil_N_off into the current run so the pulse
        is visible on the UI and auditable afterwards.

        Returns False if not armed or if coil_num is not 2 or 3. Coil 1
        has its own entry point (`fire()`).
        """
        if coil_num not in (2, 3):
            log.warning(f"manual_fire_coil: coil {coil_num} not supported")
            return False
        with self._lock:
            if self._state not in (State.ARMED, State.FIRING):
                log.warning(
                    f"manual_fire_coil: wrong state {self._state}"
                )
                return False
            run = self._current_run
            if run is None:
                return False
            # Transition to FIRING so the UI reflects an active pulse
            # and _check_and_complete can later flip to COMPLETE.
            self._state = State.FIRING
            pulse_us = self._config[f"coil_{coil_num}_pulse_duration_us"]
            run.pending_coils += 1

        log.info(f"MANUAL FIRE – coil {coil_num}")
        self._publish()
        threading.Thread(
            target=self._manual_coil_fire_thread,
            args=(coil_num, pulse_us, run),
            daemon=True,
        ).start()
        return True

    def _manual_coil_fire_thread(
        self,
        coil_num: int,
        pulse_us: float,
        run_ref: "RunData",
    ) -> None:
        """Worker for manual_fire_coil: energise, record, hold, release."""
        self.hw.set_coil(coil_num, True)
        with self._lock:
            if self._current_run is run_ref:
                run_ref.record(f"t_coil_{coil_num}_on")
        log.info(f"Coil {coil_num} ON (manual)")
        self._publish()
        self._coil_pulse_thread(coil_num, pulse_us, run_ref)

    def manual_simulate_gate(
        self,
        gate_num: int,
        transit_us: float = 500.0,
    ) -> bool:
        """Simulate a gate 1 or 2 trigger: leading edge, then trailing after
        *transit_us*.

        Runs the same code path as a real beam break, so the downstream
        coil (coil 2 for gate 1, coil 3 for gate 2) fires after the
        configured delay just as it would in a live run. Use this to
        verify gate→coil wiring and config without a projectile.

        Returns False if not armed or gate_num is not 1 or 2. Gate 3 is
        not supported because it has no downstream coil to exercise.
        """
        if gate_num not in (1, 2):
            log.warning(f"manual_simulate_gate: gate {gate_num} not supported")
            return False
        with self._lock:
            if self._state not in (State.ARMED, State.FIRING):
                log.warning(
                    f"manual_simulate_gate: wrong state {self._state}"
                )
                return False
            if self._current_run is None:
                return False
            # Transition to FIRING for the same reasons as manual_fire_coil.
            self._state = State.FIRING
            gen = self._run_generation

        log.info(f"MANUAL SIMULATE – gate {gate_num} (transit {transit_us} µs)")
        self._publish()
        # Drive the leading edge inline; the scheduling of the downstream
        # coil happens inside _on_gate_leading under the lock.
        self._on_gate_leading(gate_num, gen)
        # Trailing edge fires at leading_ts + transit_us. Capture the
        # leading-edge timestamp from the run dict (not from this
        # thread's current clock), because the coil-fire busy-wait
        # _on_gate_leading just spawned can starve the trailing thread
        # by several milliseconds. Without this anchor, transit_us
        # would be measured from "whenever the trailing thread got a
        # chance to run" — not from the actual beam-break event.
        leading_ns = None
        with self._lock:
            run = self._current_run
            if run is not None:
                leading_ns = run.timestamps.get(f"t_gate_{gate_num}_on")
        if leading_ns is None:
            # Dedup dropped the leading edge — nothing to trail.
            return True
        target_ns = leading_ns + int(transit_us * 1_000)
        threading.Thread(
            target=self._delayed_gate_trailing,
            args=(gate_num, gen, target_ns),
            daemon=True,
        ).start()
        return True

    def _delayed_gate_trailing(
        self,
        gate_num: int,
        gen: int,
        target_ns: int,
    ) -> None:
        """Worker for manual_simulate_gate: fire the trailing edge at
        *target_ns* (absolute perf_counter_ns time). Using an absolute
        anchor, not a relative delay-from-now, is essential — see
        `manual_simulate_gate` for why."""
        while time.perf_counter_ns() < target_ns:
            pass
        self._on_gate_trailing(gate_num, gen)
