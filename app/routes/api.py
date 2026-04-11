"""REST API + SSE stream for the sequencer."""

import json
import logging
import queue
import uuid

from flask import Blueprint, Response, jsonify, request

from app.models import db, ConfigSnapshot, EventLog

log = logging.getLogger(__name__)

api_bp = Blueprint("api", __name__, url_prefix="/api")


def _seq():
    from app import sequencer
    return sequencer


def _pub():
    from app import publisher
    return publisher


# ── SSE stream ──────────────────────────────────────────────────────────

@api_bp.route("/stream")
def stream():
    """Server-Sent Events endpoint.  Each connected client gets full state
    snapshots pushed whenever the sequencer state changes."""
    pub = _pub()
    q = pub.subscribe()

    def event_stream():
        try:
            # Send an initial snapshot immediately so the client doesn't
            # have to wait for the next state change.
            yield f"data: {json.dumps(_seq().snapshot())}\n\n"
            while True:
                try:
                    data = q.get(timeout=15)
                    yield f"data: {json.dumps(data)}\n\n"
                except queue.Empty:
                    # Keepalive comment to prevent proxy/browser timeout
                    yield ": keepalive\n\n"
        except GeneratorExit:
            pub.unsubscribe(q)

    resp = Response(event_stream(), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


# ── State queries ───────────────────────────────────────────────────────

@api_bp.route("/state")
def get_state():
    return jsonify(_seq().snapshot())


# ── Arm / Fire / Disarm ─────────────────────────────────────────────────

@api_bp.route("/arm", methods=["POST"])
def arm():
    ok = _seq().arm()
    if not ok:
        return jsonify({"error": "Cannot arm in current state"}), 409
    return jsonify({"status": "armed"})


@api_bp.route("/fire", methods=["POST"])
def fire():
    ok = _seq().fire()
    if not ok:
        return jsonify({"error": "Cannot fire in current state"}), 409
    return jsonify({"status": "firing"})


@api_bp.route("/save", methods=["POST"])
def save_run():
    """End the current run, persist to DB, return to READY."""
    seq = _seq()
    run_data = seq.save_run()
    if run_data is None:
        return jsonify({"status": "nothing_to_save"})

    # Persist event log
    ev = EventLog(
        run_sequence_id=run_data["run_sequence_id"],
        run_number=run_data["run_number"],
        config_snapshot_id=run_data["config_snapshot_id"],
    )
    for field_name, value in run_data["timestamps"].items():
        setattr(ev, field_name, value)
    db.session.add(ev)
    db.session.commit()

    log.info(f"Saved run #{ev.run_number} (event_log id={ev.id})")
    return jsonify({"status": "saved", "event_log_id": ev.id, "stats": run_data["stats"]})


@api_bp.route("/clear", methods=["POST"])
def clear_run():
    """Abort current run without saving."""
    _seq().clear_run()
    return jsonify({"status": "cleared"})


# ── Configuration ───────────────────────────────────────────────────────

@api_bp.route("/config", methods=["GET"])
def get_config():
    return jsonify(_seq().config)


@api_bp.route("/config", methods=["POST"])
def update_config():
    """Update one or more config parameters.  Saves a new config snapshot."""
    seq = _seq()
    payload = request.get_json(force=True)

    current = seq.config
    changed = False
    for key in ConfigSnapshot.PARAM_KEYS:
        if key in payload:
            val = float(payload[key])
            if val != current.get(key):
                current[key] = val
                changed = True

    if not changed:
        return jsonify({"status": "unchanged"})

    seq.config = current

    # Persist snapshot
    snap = ConfigSnapshot(
        run_sequence_id=seq.run_sequence_id,
        **{k: current[k] for k in ConfigSnapshot.PARAM_KEYS},
    )
    db.session.add(snap)
    db.session.commit()
    seq.config_snapshot_id = snap.id

    log.info(f"Config updated → snapshot #{snap.id}")
    # Push new state so both UI pages see the update
    _pub().publish(seq.snapshot())
    return jsonify({"status": "updated", "snapshot_id": snap.id})


# ── Run sequence management ─────────────────────────────────────────────

@api_bp.route("/sequence/new", methods=["POST"])
def new_sequence():
    seq = _seq()
    # Save current run if active
    if seq.state.value != "ready":
        seq.save_run()
    new_id = seq.new_run_sequence()
    return jsonify({"run_sequence_id": new_id})


@api_bp.route("/sequence", methods=["GET"])
def get_sequence():
    return jsonify({
        "run_sequence_id": _seq().run_sequence_id,
        "run_number": _seq().run_number,
    })


# ── History ─────────────────────────────────────────────────────────────

@api_bp.route("/history")
def history():
    """Return recent event logs for the current sequence."""
    seq = _seq()
    logs = (
        EventLog.query
        .filter_by(run_sequence_id=seq.run_sequence_id)
        .order_by(EventLog.run_number.desc())
        .limit(50)
        .all()
    )
    return jsonify([l.to_dict() for l in logs])


# ── Mock-only: simulate trigger (development helper) ────────────────────

@api_bp.route("/mock/trigger", methods=["POST"])
def mock_trigger():
    """Simulate the external trigger press (mock hardware only)."""
    hw = _seq().hw
    if hasattr(hw, "simulate_trigger_press"):
        hw.simulate_trigger_press()
        return jsonify({"status": "simulated"})
    return jsonify({"error": "Not using mock hardware"}), 400
