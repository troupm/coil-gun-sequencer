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
    # Rows recorded while gate edge mapping was inverted have off < on
    # (negative transit); magnitude is still the real beam-break duration.
    # Use abs() for the velocity computation so those rows salvage-decode
    # cleanly, but keep the signed `_us` field so an operator/analyst can
    # still see the inversion. A lower bound filters out sub-10-µs glitches.
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
            args=(1, pulse_us),
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

            # Gates are normally HIGH / active LOW: beam-break pulls the line
            # LOW (falling edge = leading), beam-restore returns it HIGH
            # (rising edge = trailing). The downstream coil delay is anchored
            # to _on_gate_leading, so this mapping is safety-critical.
            self.hw.register_gate_callback(gate_num, "falling", _on_leading)
            self.hw.register_gate_callback(gate_num, "rising", _on_trailing)

    def _on_gate_leading(self, gate_num: int, gen: int) -> None:
        """Handle beam-break leading edge (falling edge on GPIO).

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

        # Side effects outside the lock.
        log.info(f"Gate {gate_num} LEADING edge @ {t}")
        self._publish()

        if next_coil is None:
            return

        # Fire next coil after precise delay in a dedicated thread.
        threading.Thread(
            target=self._delayed_coil_fire,
            args=(next_coil, t, delay_us, pulse_us),
            daemon=True,
        ).start()

    def _on_gate_trailing(self, gate_num: int, gen: int) -> None:
        """Handle beam-restore trailing edge (rising edge on GPIO).

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

        # If this is the last active gate, mark the run complete
        if gate_num == 3 or self._all_expected_gates_done():
            with self._lock:
                if self._state == State.FIRING:
                    self._state = State.COMPLETE
                    log.info("RUN COMPLETE")
            self._publish()

    def _all_expected_gates_done(self) -> bool:
        """Check if all gates that received a leading edge also have a trailing edge."""
        if self._current_run is None:
            return False
        ts = self._current_run.timestamps
        for g in (1, 2, 3):
            on = ts.get(f"t_gate_{g}_on")
            off = ts.get(f"t_gate_{g}_off")
            if on is not None and off is None:
                return False  # still waiting for a trailing edge
        return True

    # -- internal: precise coil timing ------------------------------------

    def _delayed_coil_fire(
        self,
        coil_num: int,
        reference_ns: int,
        delay_us: float,
        pulse_us: float,
    ) -> None:
        """Busy-wait for *delay_us* after *reference_ns*, then pulse the coil."""
        target_ns = reference_ns + int(delay_us * 1_000)

        # Busy-wait for µs-level precision
        while time.perf_counter_ns() < target_ns:
            pass

        self.hw.set_coil(coil_num, True)
        if self._current_run:
            self._current_run.record(f"t_coil_{coil_num}_on")
        log.info(f"Coil {coil_num} ON (after {delay_us} µs delay)")
        self._publish()

        # Now hold for pulse duration
        self._coil_pulse_thread(coil_num, pulse_us)

    def _coil_pulse_thread(self, coil_num: int, pulse_us: float) -> None:
        """Hold coil HIGH for *pulse_us*, then turn off."""
        target_ns = time.perf_counter_ns() + int(pulse_us * 1_000)
        while time.perf_counter_ns() < target_ns:
            pass

        self.hw.set_coil(coil_num, False)
        if self._current_run:
            self._current_run.record(f"t_coil_{coil_num}_off")
        log.info(f"Coil {coil_num} OFF (after {pulse_us} µs pulse)")
        self._publish()

    # -- internal: external trigger ---------------------------------------

    def _on_external_trigger(self) -> None:
        """Callback for the physical fire button."""
        log.info("External trigger pressed")
        self.fire()

    # -- manual test controls ---------------------------------------------
    # Used exclusively by the Manual page to exercise individual coils and
    # gates outside of a normal firing sequence. Requires ARMED (or FIRING)
    # state; transitions ARMED → FIRING on first action so the run can be
    # saved/cleared through the existing lifecycle.

    MANUAL_GATE_COIL_DELAY_US = 500.0

    def manual_fire_coil(self, coil_num: int) -> str:
        """Energise *coil_num* directly, using its configured pulse duration.

        Does not wait for gate events. Intended for bench-testing that the
        coil driver fires correctly. Valid while ARMED / FIRING / COMPLETE —
        the operator can chain multiple manual actions in the same run;
        READY rejects because the run hasn't been set up yet.

        Returns:
            "ok"          – coil was fired
            "bad_coil"    – invalid coil number
            "wrong_state" – sequencer not armed
        """
        if coil_num not in (1, 2, 3):
            return "bad_coil"
        with self._lock:
            if self._state == State.READY:
                log.warning(
                    f"Cannot manual-fire coil {coil_num}: state is {self._state}"
                )
                return "wrong_state"
            if self._state != State.FIRING:
                self._state = State.FIRING
            run = self._current_run
            if run is None:
                return "wrong_state"
            pulse_us = self._config[f"coil_{coil_num}_pulse_duration_us"]
            if run.timestamps.get("t_coil_0") is None:
                run.record("t_coil_0")

        self.hw.set_coil(coil_num, True)
        run.record(f"t_coil_{coil_num}_on")
        log.info(f"MANUAL FIRE – coil {coil_num} energised")
        self._publish()

        threading.Thread(
            target=self._coil_pulse_thread,
            args=(coil_num, pulse_us),
            daemon=True,
        ).start()
        return "ok"

    def manual_trigger_gate(self, gate_num: int) -> str:
        """Simulate a gate-1/2/3 leading edge by hand.

        Records ``t_gate_N_on`` and, for gate 1 or 2, schedules the mapped
        downstream coil after a fixed ``MANUAL_GATE_COIL_DELAY_US`` (500 µs)
        — overriding the configured gate→coil delay so the manual path is
        predictable regardless of live config. No trailing-edge timestamp
        is recorded: there's no real beam to restore, and synthesising a
        ``t_gate_N_off`` would pollute transit-velocity analysis.

        Returns:
            "ok"            – gate event was recorded (and downstream coil scheduled)
            "bad_gate"      – invalid gate number
            "wrong_state"   – sequencer not armed
            "already_fired" – this gate's leading edge was already recorded this run
        """
        if gate_num not in (1, 2, 3):
            return "bad_gate"
        with self._lock:
            if self._state == State.READY:
                log.warning(
                    f"Cannot manual-trigger gate {gate_num}: state is {self._state}"
                )
                return "wrong_state"
            if self._state != State.FIRING:
                self._state = State.FIRING
            run = self._current_run
            if run is None:
                return "wrong_state"
            if gate_num in run.seen_leading:
                log.info(
                    f"Manual gate {gate_num} trigger ignored (already fired this run)"
                )
                return "already_fired"
            run.seen_leading.add(gate_num)
            t = run.record(f"t_gate_{gate_num}_on")

            next_coil = {1: 2, 2: 3}.get(gate_num)
            pulse_us: Optional[float] = None
            if next_coil is not None:
                pulse_us = self._config[f"coil_{next_coil}_pulse_duration_us"]

        log.info(
            f"MANUAL GATE {gate_num} trigger @ {t}"
            + (f" → coil {next_coil} in {self.MANUAL_GATE_COIL_DELAY_US} µs"
               if next_coil else "")
        )
        self._publish()

        if next_coil is None:
            return "ok"

        threading.Thread(
            target=self._delayed_coil_fire,
            args=(next_coil, t, self.MANUAL_GATE_COIL_DELAY_US, pulse_us),
            daemon=True,
        ).start()
        return "ok"
