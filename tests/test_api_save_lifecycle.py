"""Regression + unit tests for the save-and-persist API lifecycle.

Pinned regressions:
  * 2026-04-30 — `/sequence/new` swallowed the active run instead of
    persisting it. Operator workflow: arm -> fire -> click "NEW SEQUENCE"
    on the configuration page expecting the just-fired run to be saved
    and a fresh sequence to start. The route called `seq.save_run()`,
    which atomically claims the in-flight RunData from the sequencer,
    but threw away the returned dict instead of writing it to
    `event_logs`. Saved runs vanished without a trace. Fix: factor the
    persist logic into `_persist_run` and call it from both `/save` and
    the mid-run branch of `/sequence/new`.

These tests exercise the full Flask + SQLAlchemy stack so any future
regression that re-introduces the discard-on-rotate behaviour, or any
schema/route change that breaks the round-trip from RunData -> EventLog
row, will fail loudly here before it reaches the rig.

Run with:
    python -m pytest tests/test_api_save_lifecycle.py
"""

import os
import shutil
import tempfile
import time
import unittest

# Force the mock hardware backend on every platform so the test
# behaviour doesn't depend on which OS the suite is invoked from.
os.environ.setdefault("COILGUN_HW", "mock")

from app.config import FlaskConfig
from app.sequencer import State


def _wait_until(predicate, timeout_s: float = 1.0, poll_s: float = 0.005) -> bool:
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        if predicate():
            return True
        time.sleep(poll_s)
    return False


class _APITestBase(unittest.TestCase):
    """Spin up a fresh Flask app + isolated SQLite file per test.

    Using a real on-disk SQLite (rather than `:memory:`) avoids the
    multi-connection isolation issues that bite SQLAlchemy when several
    sessions / threads each end up with their own private in-memory DB.
    """

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="coilgun-test-")
        db_path = os.path.join(self.tmpdir, "test.db")

        self._orig_uri = FlaskConfig.SQLALCHEMY_DATABASE_URI
        FlaskConfig.SQLALCHEMY_DATABASE_URI = "sqlite:///" + db_path

        # Import lazily so the URI override above takes effect on first
        # create_app() call.
        from app import create_app
        self.app = create_app()
        self.client = self.app.test_client()

        from app import sequencer as _seq
        self.seq = _seq

        from app.models import db, EventLog
        self.db = db
        self.EventLog = EventLog

    def tearDown(self) -> None:
        FlaskConfig.SQLALCHEMY_DATABASE_URI = self._orig_uri

        # Cancel any pending mock-hardware timers and force the sequencer
        # back to READY. Skipping this leaves daemon Timer threads alive
        # across tests, where they perturb the µs-scale timing the
        # sequencer happy-path tests depend on (coil-3 fires only ~1.5 ms
        # after state goes COMPLETE — extra background load can push that
        # window past the next pytest poll).
        try:
            if self.seq.state != State.READY:
                self.seq.disarm()
        finally:
            self.seq.hw.cleanup()

        # Drop the SQLAlchemy session and engine so the file isn't held
        # open by a stale connection on Windows when we try to remove it.
        with self.app.app_context():
            self.db.session.remove()
            self.db.engine.dispose()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # -- helpers --------------------------------------------------------

    def _arm_fire_complete(self) -> None:
        """Drive the sequencer through arm -> fire -> COMPLETE.

        Waits for the coil 3 timestamp to land before returning so all
        cascade threads spawned by fire() have exited by the time the
        test calls /save or /sequence/new. Skipping this leaves coil-
        pulse busy-wait threads running across tearDown, which steals
        CPU from later tests and turns µs-scale timing flaky.
        """
        self.assertEqual(self.client.post("/api/arm").status_code, 200)
        self.assertEqual(self.client.post("/api/fire").status_code, 200)
        self.assertTrue(
            _wait_until(lambda: (
                self.seq.state == State.COMPLETE
                and self.seq._current_run is not None
                and self.seq._current_run.timestamps.get("t_coil_3_off") is not None
            )),
            f"cascade did not finish; state={self.seq.state}",
        )

    def _event_log_count(self) -> int:
        with self.app.app_context():
            return self.EventLog.query.count()


# ---------------------------------------------------------------------------
# /api/save — baseline behaviour
# ---------------------------------------------------------------------------

class SaveEndpointTests(_APITestBase):

    def test_save_persists_event_log_after_full_run(self) -> None:
        self._arm_fire_complete()
        prev_seq_id = self.seq.run_sequence_id
        prev_run_num = self.seq.run_number

        rsp = self.client.post("/api/save").get_json()

        self.assertEqual(rsp["status"], "saved")
        self.assertIn("event_log_id", rsp)
        self.assertIn("stats", rsp)

        with self.app.app_context():
            row = self.db.session.get(self.EventLog, rsp["event_log_id"])
            self.assertIsNotNone(row)
            self.assertEqual(row.run_sequence_id, prev_seq_id)
            self.assertEqual(row.run_number, prev_run_num)
            # A full mock run lights up every gate and coil — make sure
            # the row carries the timestamps end-to-end, not just the
            # bookkeeping fields.
            self.assertIsNotNone(row.t_coil_0)
            self.assertIsNotNone(row.t_gate_1_on)
            self.assertIsNotNone(row.t_gate_1_off)
            self.assertIsNotNone(row.t_coil_1_on)

    def test_save_with_no_active_run_writes_nothing(self) -> None:
        # Fresh app, never armed.
        self.assertEqual(self.seq.state, State.READY)
        rsp = self.client.post("/api/save").get_json()
        self.assertEqual(rsp["status"], "nothing_to_save")
        self.assertEqual(self._event_log_count(), 0)

    def test_double_save_only_persists_one_row(self) -> None:
        """Second save claims nothing — no duplicate row."""
        self._arm_fire_complete()
        first = self.client.post("/api/save").get_json()
        second = self.client.post("/api/save").get_json()
        self.assertEqual(first["status"], "saved")
        self.assertEqual(second["status"], "nothing_to_save")
        self.assertEqual(self._event_log_count(), 1)

    def test_save_returns_to_ready_state(self) -> None:
        self._arm_fire_complete()
        self.client.post("/api/save")
        self.assertEqual(self.seq.state, State.READY)


# ---------------------------------------------------------------------------
# /api/sequence/new — REGRESSION: must persist the active run
# ---------------------------------------------------------------------------

class SequenceNewPersistsRunRegression(_APITestBase):
    """Regression: 2026-04-30 — `/sequence/new` was discarding the run.

    The route called `seq.save_run()` (which atomically claims the
    RunData from memory) but never wrote the returned dict to
    `event_logs`, so the run was lost the moment an operator clicked
    NEW SEQUENCE while a run was in flight.
    """

    def test_sequence_new_mid_run_persists_active_run(self) -> None:
        self._arm_fire_complete()
        prev_seq_id = self.seq.run_sequence_id
        prev_run_num = self.seq.run_number
        self.assertNotEqual(self.seq.state, State.READY)

        rsp = self.client.post("/api/sequence/new").get_json()

        # New sequence id is rotated.
        self.assertIn("run_sequence_id", rsp)
        self.assertNotEqual(rsp["run_sequence_id"], prev_seq_id)

        # The active run that was in flight must have been written to
        # the log before the rotation.
        with self.app.app_context():
            rows = self.EventLog.query.filter_by(
                run_sequence_id=prev_seq_id,
                run_number=prev_run_num,
            ).all()
        self.assertEqual(
            len(rows), 1,
            "active run was not persisted before sequence rotation — "
            "this is the 2026-04-30 silent-drop regression",
        )

    def test_sequence_new_from_ready_writes_nothing(self) -> None:
        """No run in flight — rotate cleanly, no row inserted."""
        self.assertEqual(self.seq.state, State.READY)
        prev_seq_id = self.seq.run_sequence_id

        rsp = self.client.post("/api/sequence/new").get_json()

        self.assertNotEqual(rsp["run_sequence_id"], prev_seq_id)
        self.assertEqual(self._event_log_count(), 0)

    def test_sequence_new_after_explicit_save_writes_only_one_row(self) -> None:
        """Operator saves explicitly, then rotates — exactly one EventLog.

        Catches the inverse failure mode: if a future refactor double-
        persists (once from /save, once from /sequence/new noticing the
        sequencer is already READY but trying to "resave" anyway), this
        test fails.
        """
        self._arm_fire_complete()
        self.client.post("/api/save")
        self.assertEqual(self._event_log_count(), 1)

        self.client.post("/api/sequence/new")
        self.assertEqual(self._event_log_count(), 1)

    def test_sequence_new_mid_run_run_number_resets_for_next_run(self) -> None:
        """After persisting + rotating, a fresh arm starts at run_number=1.

        Belt-and-braces: confirms the rotation actually happened on the
        sequencer side, not just in the response payload.
        """
        self._arm_fire_complete()
        self.client.post("/api/sequence/new")
        self.assertEqual(self.seq.state, State.READY)
        self.assertEqual(self.seq.run_number, 0)

        self.client.post("/api/arm")
        self.assertEqual(self.seq.run_number, 1)


if __name__ == "__main__":
    unittest.main()
