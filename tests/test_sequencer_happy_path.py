"""Happy-path regression tests for the sequencer lifecycle.

These tests exist to protect one specific invariant the operator cares
about most:

    A run MUST return to State.READY via any of
    save_run / clear_run / disarm / new_run_sequence, and calling any of
    those a second time must be a no-op, not a crash.

If a future change sneaks in that breaks "Return to Ready" behaviour, these
tests will catch it before the code goes live on the physical rig.

Also includes a regression test for the 2026-04-12 gate-1 double-fire bug.

Run with:
    python -m unittest discover tests
or:
    python -m unittest tests.test_sequencer_happy_path
"""

import time
import unittest

from app.config import DEFAULTS
from app.hardware.mock import MockHardware
from app.sequencer import Sequencer, State, StatePublisher


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

class CountingMockHardware(MockHardware):
    """MockHardware that counts how many times each coil is energised.

    Used by the bounce-regression tests to assert that dedup prevents
    multiple coil pulses from a single run.
    """

    def __init__(self) -> None:
        super().__init__()
        self.coil_on_counts = {1: 0, 2: 0, 3: 0}

    def set_coil(self, coil_num: int, state: bool) -> None:
        if state:
            self.coil_on_counts[coil_num] = self.coil_on_counts.get(coil_num, 0) + 1
        super().set_coil(coil_num, state)


def _wait_until(predicate, timeout_s: float = 2.0, poll_s: float = 0.005) -> bool:
    """Block until *predicate* returns truthy, or *timeout_s* elapses."""
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        if predicate():
            return True
        time.sleep(poll_s)
    return False


# ---------------------------------------------------------------------------
# Lifecycle happy-path tests
# ---------------------------------------------------------------------------

class SequencerHappyPathTests(unittest.TestCase):
    """Full arm -> fire -> complete -> terminate -> READY coverage."""

    def setUp(self) -> None:
        self.hw = CountingMockHardware()
        self.hw.setup()
        # StatePublisher with no SocketIO attached → publishes are no-ops,
        # so we don't need a Flask app context for these tests.
        self.publisher = StatePublisher()
        self.seq = Sequencer(self.hw, self.publisher)
        self.seq.config = dict(DEFAULTS)

    def tearDown(self) -> None:
        # Make sure no mock timers or coils leak into the next test.
        try:
            if self.seq.state != State.READY:
                self.seq.disarm()
        finally:
            self.hw.cleanup()

    def _run_full_sequence(self) -> None:
        """Arm, fire, and wait for the sequence to reach State.COMPLETE.

        The mock hardware auto-simulates gate events when coils fire, so
        a single fire() call drives the full coil_1 → gate_1 → coil_2 →
        gate_2 → coil_3 → gate_3 pipeline without any test-side poking.
        """
        self.assertEqual(self.seq.state, State.READY)
        self.assertTrue(self.seq.arm(), "arm() should succeed from READY")
        self.assertEqual(self.seq.state, State.ARMED)

        self.assertTrue(self.seq.fire(), "fire() should succeed from ARMED")
        # fire() transitions ARMED → FIRING synchronously; gate cascade is async.
        self.assertTrue(
            _wait_until(lambda: self.seq.state == State.COMPLETE, timeout_s=1.0),
            f"sequence did not reach COMPLETE; stuck at {self.seq.state}",
        )

    # -- each termination path must land us in READY -----------------------

    def test_save_run_returns_to_ready(self) -> None:
        self._run_full_sequence()
        result = self.seq.save_run()
        self.assertIsNotNone(result, "save_run should return a payload after a run")
        self.assertEqual(self.seq.state, State.READY)
        # Payload should carry timestamps and derived stats
        self.assertIn("timestamps", result)
        self.assertIn("stats", result)
        # All three coils should have fired exactly once during the happy path
        self.assertEqual(self.hw.coil_on_counts[1], 1)
        self.assertEqual(self.hw.coil_on_counts[2], 1)
        self.assertEqual(self.hw.coil_on_counts[3], 1)

    def test_clear_run_returns_to_ready(self) -> None:
        self._run_full_sequence()
        self.seq.clear_run()
        self.assertEqual(self.seq.state, State.READY)

    def test_disarm_returns_to_ready(self) -> None:
        self._run_full_sequence()
        self.seq.disarm()
        self.assertEqual(self.seq.state, State.READY)

    def test_new_run_sequence_after_save_resets_counter(self) -> None:
        """new_run_sequence rotates the sequence id and zeroes run_number.

        It is not itself a termination path (the caller is expected to
        save_run / clear_run first), but the end state after the full
        flow must still be READY with a fresh sequence id.
        """
        self._run_full_sequence()
        first_seq_id = self.seq.run_sequence_id
        first_run_num = self.seq.run_number
        self.assertEqual(first_run_num, 1)

        self.seq.save_run()
        self.assertEqual(self.seq.state, State.READY)

        new_id = self.seq.new_run_sequence()
        self.assertNotEqual(new_id, first_seq_id)
        self.assertEqual(self.seq.run_sequence_id, new_id)
        self.assertEqual(self.seq.run_number, 0)
        self.assertEqual(self.seq.state, State.READY)

    # -- idempotency: second termination call must be a no-op --------------

    def test_save_run_twice_is_idempotent(self) -> None:
        self._run_full_sequence()
        self.seq.save_run()
        self.assertEqual(self.seq.state, State.READY)
        # Second save has no run to claim → returns None, stays READY.
        second = self.seq.save_run()
        self.assertIsNone(second)
        self.assertEqual(self.seq.state, State.READY)

    def test_clear_run_twice_is_idempotent(self) -> None:
        self._run_full_sequence()
        self.seq.clear_run()
        self.seq.clear_run()
        self.assertEqual(self.seq.state, State.READY)

    def test_disarm_twice_is_idempotent(self) -> None:
        self._run_full_sequence()
        self.seq.disarm()
        self.seq.disarm()
        self.assertEqual(self.seq.state, State.READY)

    def test_mixed_termination_calls_all_land_in_ready(self) -> None:
        """Any combination of save/clear/disarm calls must end in READY."""
        self._run_full_sequence()
        self.seq.save_run()
        self.seq.clear_run()   # no-op
        self.seq.disarm()      # no-op
        self.seq.clear_run()   # no-op
        self.assertEqual(self.seq.state, State.READY)

    # -- must be able to arm again after every termination path -----------

    def test_can_rearm_after_save_run(self) -> None:
        self._run_full_sequence()
        self.seq.save_run()
        self.assertTrue(self.seq.arm())
        self.assertEqual(self.seq.state, State.ARMED)

    def test_can_rearm_after_clear_run(self) -> None:
        self._run_full_sequence()
        self.seq.clear_run()
        self.assertTrue(self.seq.arm())
        self.assertEqual(self.seq.state, State.ARMED)

    def test_can_rearm_after_disarm(self) -> None:
        self._run_full_sequence()
        self.seq.disarm()
        self.assertTrue(self.seq.arm())
        self.assertEqual(self.seq.state, State.ARMED)

    def test_run_number_increments_across_runs_in_same_sequence(self) -> None:
        self._run_full_sequence()
        self.assertEqual(self.seq.run_number, 1)
        self.seq.save_run()

        self.assertTrue(self.seq.arm())
        self.assertEqual(self.seq.run_number, 2)
        self.assertTrue(self.seq.fire())
        self.assertTrue(
            _wait_until(lambda: self.seq.state == State.COMPLETE, timeout_s=1.0)
        )
        self.seq.save_run()
        self.assertEqual(self.seq.state, State.READY)


# ---------------------------------------------------------------------------
# Gate-bounce dedup regression (2026-04-12)
# ---------------------------------------------------------------------------

class GateBounceRegressionTests(unittest.TestCase):
    """Repeat gate-edge callbacks must not double-fire downstream coils."""

    def setUp(self) -> None:
        self.hw = CountingMockHardware()
        self.hw.setup()
        self.seq = Sequencer(self.hw, StatePublisher())
        self.seq.config = dict(DEFAULTS)

    def tearDown(self) -> None:
        try:
            if self.seq.state != State.READY:
                self.seq.disarm()
        finally:
            self.hw.cleanup()

    def test_bouncing_gate_1_fires_coil_2_exactly_once(self) -> None:
        """Original bug: 10 bounced gate-1 edges fired coil 2 ten times.

        Drives the handler directly rather than through fire() so that the
        mock's own auto-simulation can't confound the count — we want to
        observe only what our 10 bounced callbacks produce.
        """
        self.assertTrue(self.seq.arm())
        gen = self.seq._run_generation

        # Simulate sensor bounce: 10 rapid leading-edge callbacks on gate 1.
        for _ in range(10):
            self.seq._on_gate_leading(1, gen)

        # The first call spawns a delayed_coil_fire thread that waits
        # gate_1_coil_2_delay_us (500 µs) before energising coil 2. Wait
        # for that to land, then give extra slack to catch any late fires.
        self.assertTrue(
            _wait_until(lambda: self.hw.coil_on_counts[2] >= 1, timeout_s=0.5),
            "coil 2 was never fired after gate-1 leading edge",
        )
        time.sleep(0.05)

        self.assertEqual(
            self.hw.coil_on_counts[2], 1,
            f"coil 2 fired {self.hw.coil_on_counts[2]}x — dedup is broken",
        )
        # The dedup set must show gate 1 was claimed. (2 and 3 may also be
        # present because the mock cascade runs through the whole sequence
        # once coil 2 energises — that's fine, we only care about gate 1.)
        self.assertIn(1, self.seq._current_run.seen_leading)

    def test_bouncing_gate_1_trailing_edge_is_deduped(self) -> None:
        """Trailing-edge dedup protects t_gate_N_off from being overwritten."""
        self.assertTrue(self.seq.arm())
        gen = self.seq._run_generation

        for _ in range(10):
            self.seq._on_gate_trailing(1, gen)

        # No coil fires were driven (we didn't call fire()), so the mock
        # auto-sim is idle and seen_trailing should be exactly {1}.
        self.assertEqual(self.seq._current_run.seen_trailing, {1})

    def test_stale_callback_from_previous_run_is_rejected(self) -> None:
        """A callback fired with an old generation must be a no-op."""
        self.assertTrue(self.seq.arm())
        old_gen = self.seq._run_generation
        self.seq.disarm()

        # New run — generation has been bumped.
        self.assertTrue(self.seq.arm())
        self.assertNotEqual(self.seq._run_generation, old_gen)

        # Fire a callback with the stale generation.
        self.seq._on_gate_leading(1, old_gen)

        # The new run must not have been affected.
        self.assertNotIn(1, self.seq._current_run.seen_leading)

    def test_gate_transit_is_positive_after_beam_break_then_restore(self) -> None:
        """Polarity regression (2026-04-16).

        On the real rig the gates are idle-LOW: beam-break is a rising edge
        and beam-restore is a falling edge. A past version of the code
        registered the leading handler on 'falling' and trailing on 'rising',
        which produced negative transit times once the new gate sensors were
        installed and lost gate_N_transit_velocity_ms for every run.

        This test asserts that the full cascade — driven through the mock's
        beam-break/beam-restore simulation — yields t_gate_N_off > t_gate_N_on
        for every gate. If someone flips the mapping in either the sequencer
        or the mock without flipping the other, this test breaks.
        """
        self.assertTrue(self.seq.arm())
        self.assertTrue(self.seq.fire())

        # Wait for every gate's trailing edge to land.  State reaches
        # COMPLETE as soon as _all_expected_gates_done() returns true,
        # which can happen before gate 3 fires (since its `_on` timestamp
        # is still None at that moment and the method doesn't wait for
        # unpopulated gates) — so checking `state == COMPLETE` isn't
        # enough to guarantee gate 3's timestamps are populated.
        def _all_trailing_populated():
            ts = self.seq._current_run.timestamps
            return all(ts.get(f"t_gate_{g}_off") is not None for g in (1, 2, 3))
        self.assertTrue(
            _wait_until(_all_trailing_populated, timeout_s=1.0),
            "not all gate trailing edges fired within timeout",
        )

        ts = self.seq._current_run.timestamps
        for g in (1, 2, 3):
            on = ts.get(f"t_gate_{g}_on")
            off = ts.get(f"t_gate_{g}_off")
            self.assertIsNotNone(on, f"gate {g} leading edge not captured")
            self.assertIsNotNone(off, f"gate {g} trailing edge not captured")
            self.assertGreater(
                off, on,
                f"gate {g} transit is non-positive "
                f"(on={on}, off={off}) — polarity mapping is inverted",
            )

    def test_fresh_run_data_after_rearm_has_empty_dedup_sets(self) -> None:
        """The whole point of per-RunData state: reset is automatic."""
        self.assertTrue(self.seq.arm())
        gen1 = self.seq._run_generation
        self.seq._on_gate_leading(1, gen1)
        self.seq._on_gate_trailing(1, gen1)
        self.assertIn(1, self.seq._current_run.seen_leading)
        self.assertIn(1, self.seq._current_run.seen_trailing)

        self.seq.clear_run()
        self.assertTrue(self.seq.arm())
        # Fresh RunData → empty sets, no bookkeeping required.
        self.assertEqual(self.seq._current_run.seen_leading, set())
        self.assertEqual(self.seq._current_run.seen_trailing, set())


# ---------------------------------------------------------------------------
# Coil 3 firing reliability (2026-04-21)
# ---------------------------------------------------------------------------

class Coil3ReliabilityTests(unittest.TestCase):
    """Coil 3 MUST fire after gate_2_coil_3_delay_us once gate 2 triggers,
    regardless of other gate/coil state, as long as the system was armed."""

    def setUp(self) -> None:
        self.hw = CountingMockHardware()
        self.hw.setup()
        self.seq = Sequencer(self.hw, StatePublisher())
        self.seq.config = dict(DEFAULTS)

    def tearDown(self) -> None:
        try:
            if self.seq.state != State.READY:
                self.seq.disarm()
        finally:
            self.hw.cleanup()

    def test_gate2_leading_alone_fires_coil3(self) -> None:
        """Only gate 2's leading edge is needed — no gate 1, no coil 1."""
        self.assertTrue(self.seq.arm())
        gen = self.seq._run_generation

        # Deliver gate 2 leading straight to the handler. No fire(),
        # no gate 1, nothing else — this is the "irrespective of other
        # gate & coil states" invariant.
        self.seq._on_gate_leading(2, gen)

        self.assertTrue(
            _wait_until(
                lambda: self.hw.coil_on_counts[3] >= 1, timeout_s=0.5
            ),
            "coil 3 did not fire after gate 2 leading edge alone",
        )

    def test_coil3_delay_is_measured_from_gate2_leading_edge(self) -> None:
        """Firing delay must be from the LEADING edge, not the trailing."""
        # Use a big delay so timing measurement is robust against jitter.
        self.seq.config = dict(DEFAULTS, gate_2_coil_3_delay_us=5000.0)
        self.assertTrue(self.seq.arm())
        gen = self.seq._run_generation

        t_leading = time.perf_counter_ns()
        self.seq._on_gate_leading(2, gen)

        self.assertTrue(
            _wait_until(
                lambda: self.seq._current_run.timestamps.get("t_coil_3_on")
                is not None,
                timeout_s=0.5,
            )
        )
        t_coil_on = self.seq._current_run.timestamps["t_coil_3_on"]
        delay_us = (t_coil_on - t_leading) / 1_000.0
        # Allow generous slack for Python scheduling jitter; the point is
        # that we saw ~5000 µs, not ~5500 µs (which would indicate the
        # trailing-edge timestamp was used as the reference instead).
        self.assertGreater(delay_us, 4500.0, f"delay={delay_us}µs — fired too early")
        self.assertLess(delay_us, 7000.0, f"delay={delay_us}µs — reference was likely the trailing edge")

    def test_gate2_trailing_before_coil3_fires_does_not_prevent_coil3(self) -> None:
        """The original concern: gate 2 trailing arrives before the delay
        window elapses. State must NOT flip to COMPLETE until coil 3 has
        finished, and coil 3 must actually fire."""
        self.assertTrue(self.seq.arm())
        self.assertTrue(self.seq.fire())
        gen = self.seq._run_generation

        # Drive gate 2 leading directly so we control timing, then
        # immediately trail it — this emulates a fast projectile where
        # trailing arrives inside the coil-3 delay window.
        self.seq._on_gate_leading(2, gen)
        self.seq._on_gate_trailing(2, gen)

        # State must not have prematurely transitioned to COMPLETE
        # (pending_coils > 0 because of coil 3).
        self.assertNotEqual(
            self.seq.state, State.COMPLETE,
            "state flipped to COMPLETE while coil 3 was still pending",
        )

        # Coil 3 eventually fires.
        self.assertTrue(
            _wait_until(lambda: self.hw.coil_on_counts[3] >= 1, timeout_s=0.5),
            "coil 3 never fired despite gate 2 leading edge",
        )


# ---------------------------------------------------------------------------
# Manual component-test entry points (Manual page backend)
# ---------------------------------------------------------------------------

class ManualControlTests(unittest.TestCase):
    """Sanity tests for manual_fire_coil / manual_simulate_gate."""

    def setUp(self) -> None:
        self.hw = CountingMockHardware()
        self.hw.setup()
        self.seq = Sequencer(self.hw, StatePublisher())
        self.seq.config = dict(DEFAULTS)

    def tearDown(self) -> None:
        try:
            if self.seq.state != State.READY:
                self.seq.disarm()
        finally:
            self.hw.cleanup()

    def test_manual_fire_coil_requires_armed(self) -> None:
        self.assertFalse(self.seq.manual_fire_coil(2))
        self.assertFalse(self.seq.manual_fire_coil(3))
        self.assertEqual(self.hw.coil_on_counts[2], 0)
        self.assertEqual(self.hw.coil_on_counts[3], 0)

    def test_manual_fire_coil_rejects_coil_1(self) -> None:
        """Coil 1 goes through fire(), not this path."""
        self.assertTrue(self.seq.arm())
        self.assertFalse(self.seq.manual_fire_coil(1))

    def test_manual_fire_coil_2_fires_coil_2(self) -> None:
        self.assertTrue(self.seq.arm())
        self.assertTrue(self.seq.manual_fire_coil(2))

        self.assertTrue(
            _wait_until(
                lambda: self.seq._current_run.timestamps.get("t_coil_2_off")
                is not None,
                timeout_s=0.5,
            )
        )
        self.assertEqual(self.hw.coil_on_counts[2], 1)
        # Coil 1 must not have been touched.
        self.assertEqual(self.hw.coil_on_counts[1], 0)

    def test_manual_fire_coil_3_fires_coil_3(self) -> None:
        self.assertTrue(self.seq.arm())
        self.assertTrue(self.seq.manual_fire_coil(3))

        self.assertTrue(
            _wait_until(
                lambda: self.seq._current_run.timestamps.get("t_coil_3_off")
                is not None,
                timeout_s=0.5,
            )
        )
        self.assertEqual(self.hw.coil_on_counts[3], 1)

    def test_manual_simulate_gate_1_fires_coil_2(self) -> None:
        self.assertTrue(self.seq.arm())
        self.assertTrue(self.seq.manual_simulate_gate(1))

        self.assertTrue(
            _wait_until(
                lambda: self.hw.coil_on_counts[2] >= 1, timeout_s=0.5
            ),
            "coil 2 never fired after simulated gate 1",
        )
        # The 500 µs transit means trailing should also land.
        self.assertTrue(
            _wait_until(
                lambda: self.seq._current_run.timestamps.get("t_gate_1_off")
                is not None,
                timeout_s=0.5,
            )
        )
        ts = self.seq._current_run.timestamps
        transit_us = (ts["t_gate_1_off"] - ts["t_gate_1_on"]) / 1_000.0
        # Trailing must land *after* leading — a non-positive transit
        # would mean the trailing handler is using a different clock
        # or getting called before leading. We don't upper-bound the
        # transit: Python thread startup under GIL contention from
        # concurrent coil busy-waits (both in this test and in earlier
        # tests that leave daemon threads running past tearDown) can
        # push the measured transit well past the requested 500 µs
        # even though the request was honoured.
        self.assertGreater(transit_us, 0.0)

    def test_manual_simulate_gate_2_fires_coil_3(self) -> None:
        self.assertTrue(self.seq.arm())
        self.assertTrue(self.seq.manual_simulate_gate(2))

        self.assertTrue(
            _wait_until(
                lambda: self.hw.coil_on_counts[3] >= 1, timeout_s=0.5
            ),
            "coil 3 never fired after simulated gate 2",
        )

    def test_manual_simulate_gate_rejects_gate_3(self) -> None:
        """Gate 3 has no downstream coil — no manual entry point."""
        self.assertTrue(self.seq.arm())
        self.assertFalse(self.seq.manual_simulate_gate(3))


if __name__ == "__main__":
    unittest.main()
