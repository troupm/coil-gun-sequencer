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


if __name__ == "__main__":
    unittest.main()
