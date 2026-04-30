"""REST API + SocketIO event emissions for the sequencer."""

import glob
import logging
import os
from collections import defaultdict

from flask import Blueprint, jsonify, request
from sqlalchemy import func

from app.models import db, ConfigSnapshot, EventLog, SequenceNote

log = logging.getLogger(__name__)

api_bp = Blueprint("api", __name__, url_prefix="/api")


def _seq():
    from app import sequencer
    return sequencer


def _pub():
    from app import publisher
    return publisher


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


def _persist_run(run_data: dict) -> EventLog:
    """Write a sequencer run_data dict to event_logs and broadcast run_saved.

    Single source of truth for "the active run was claimed from the
    sequencer; now make it durable". Used by both `/save` and
    `/sequence/new` — the latter previously dropped the in-flight run on
    the floor when an operator started a new sequence mid-run.
    """
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

    # Notify all clients (especially the Analysis page) that a run was saved
    _pub().emit("run_saved", {
        "run_sequence_id": run_data["run_sequence_id"],
        "run_number": run_data["run_number"],
        "event_log_id": ev.id,
        "stats": run_data["stats"],
    })
    return ev


@api_bp.route("/save", methods=["POST"])
def save_run():
    """End the current run, persist to DB, return to READY."""
    seq = _seq()
    run_data = seq.save_run()
    if run_data is None:
        return jsonify({"status": "nothing_to_save"})
    ev = _persist_run(run_data)
    return jsonify({"status": "saved", "event_log_id": ev.id, "stats": run_data["stats"]})


@api_bp.route("/clear", methods=["POST"])
def clear_run():
    """Abort current run without saving."""
    _seq().clear_run()
    return jsonify({"status": "cleared"})


# ── Manual test controls (Manual page) ──────────────────────────────────

@api_bp.route("/manual/coil/<int:coil_num>/fire", methods=["POST"])
def manual_fire_coil(coil_num):
    """Directly energise a coil for bench testing. Requires ARMED state."""
    result = _seq().manual_fire_coil(coil_num)
    if result == "ok":
        return jsonify({"status": "fired", "coil": coil_num})
    if result == "bad_coil":
        return jsonify({"error": "coil_num must be 1, 2, or 3"}), 400
    return jsonify({"error": "Cannot manual-fire in current state"}), 409


@api_bp.route("/manual/gate/<int:gate_num>/trigger", methods=["POST"])
def manual_trigger_gate(gate_num):
    """Simulate a gate leading edge for bench testing. Requires ARMED state."""
    result = _seq().manual_trigger_gate(gate_num)
    if result == "ok":
        return jsonify({"status": "triggered", "gate": gate_num})
    if result == "bad_gate":
        return jsonify({"error": "gate_num must be 1, 2, or 3"}), 400
    if result == "already_fired":
        return jsonify({
            "error": f"Gate {gate_num} leading edge already recorded this run",
        }), 409
    return jsonify({"error": "Cannot manual-trigger in current state"}), 409


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
        if key not in payload:
            continue
        raw = payload[key]

        # Special-case convenience: rail_source_active accepts a JSON bool
        # from simple clients and resolves to the current v_coil_ceiling
        # (or 0.0) server-side, so callers don't need to know the encoding.
        # Numeric values pass through as-is — the UI already computes the
        # right float locally, so the bool path is primarily for scripted
        # / external callers.
        if key == "rail_source_active" and isinstance(raw, bool):
            val = float(current.get("v_coil_ceiling", 0.0)) if raw else 0.0
        else:
            val = float(raw)

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
    # Push new state so all UI pages see the update
    _pub().publish(seq.snapshot())
    _pub().emit("config_updated", {
        "snapshot_id": snap.id,
        "run_sequence_id": seq.run_sequence_id,
    })
    return jsonify({"status": "updated", "snapshot_id": snap.id})


# ── Run sequence management ─────────────────────────────────────────────

@api_bp.route("/sequence/new", methods=["POST"])
def new_sequence():
    seq = _seq()
    # If a run is in flight, persist it before rotating the sequence id —
    # otherwise the active run is lost (regression: 2026-04-30, runs were
    # silently dropped when the operator hit "NEW SEQUENCE" mid-run).
    if seq.state.value != "ready":
        run_data = seq.save_run()
        if run_data is not None:
            _persist_run(run_data)
    new_id = seq.new_run_sequence()
    _pub().emit("sequence_changed", {"run_sequence_id": new_id})
    return jsonify({"run_sequence_id": new_id})


@api_bp.route("/sequence", methods=["GET"])
def get_sequence():
    return jsonify({
        "run_sequence_id": _seq().run_sequence_id,
        "run_number": _seq().run_number,
    })


# ── Sequence notes ─────────────────────────────────────────────────────

@api_bp.route("/sequence/notes", methods=["GET"])
def get_sequence_notes():
    """Return the note for the current sequence (or empty string)."""
    seq_id = _seq().run_sequence_id
    note = SequenceNote.query.filter_by(run_sequence_id=seq_id).first()
    return jsonify({
        "run_sequence_id": seq_id,
        "notes": note.notes if note else "",
    })


@api_bp.route("/sequence/notes", methods=["PUT"])
def update_sequence_notes():
    """Create or update the note for the current sequence."""
    seq_id = _seq().run_sequence_id
    payload = request.get_json(force=True)
    text_val = str(payload.get("notes", "")).strip()

    note = SequenceNote.query.filter_by(run_sequence_id=seq_id).first()
    if note:
        note.notes = text_val
    else:
        note = SequenceNote(run_sequence_id=seq_id, notes=text_val)
        db.session.add(note)
    db.session.commit()
    log.info("Updated sequence notes for %s", seq_id[:8])
    return jsonify(note.to_dict())


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


# ── Analysis / Trend endpoints ──────────────────────────────────────────

VELOCITY_KEYS = [
    "gate_1_transit_velocity_ms",
    "gate_2_transit_velocity_ms",
    "gate_3_transit_velocity_ms",
    "gate_1_to_gate_2_velocity_ms",
    "gate_2_to_gate_3_velocity_ms",
]

TIMING_KEYS = [
    "gate_1_transit_us",
    "gate_2_transit_us",
    "gate_3_transit_us",
    "gate_1_to_gate_2_flight_us",
    "gate_2_to_gate_3_flight_us",
]

_TREND_THRESHOLD = 0.01  # 1% change required to register as improving/declining


def _compute_run_velocities(ev, cfg):
    """Compute velocities from an EventLog row + a ConfigSnapshot row."""
    stats = {}
    proj_len = cfg.projectile_length_mm

    # See app/sequencer.py:compute_stats for the abs()/10-µs rationale:
    # rows recorded while gate edge mapping was inverted have off<on, and
    # magnitude is still the real transit duration. Salvage velocity via
    # abs() while keeping the signed `_us` field visible to the UI.
    for g in (1, 2, 3):
        on = getattr(ev, f"t_gate_{g}_on")
        off = getattr(ev, f"t_gate_{g}_off")
        if on is not None and off is not None:
            transit_us = (off - on) / 1_000.0
            stats[f"gate_{g}_transit_us"] = round(transit_us, 2)
            if abs(transit_us) >= 10.0:
                stats[f"gate_{g}_transit_velocity_ms"] = round(
                    proj_len * 1_000.0 / abs(transit_us), 3
                )

    pairs = [
        (1, 2, cfg.gate_1_to_gate_2_distance_mm),
        (2, 3, cfg.gate_2_to_gate_3_distance_mm),
    ]
    for ga, gb, dist in pairs:
        on_a = getattr(ev, f"t_gate_{ga}_on")
        on_b = getattr(ev, f"t_gate_{gb}_on")
        if on_a is not None and on_b is not None:
            flight_us = (on_b - on_a) / 1_000.0
            stats[f"gate_{ga}_to_gate_{gb}_flight_us"] = round(flight_us, 2)
            if flight_us > 0 and dist and dist > 0:
                stats[f"gate_{ga}_to_gate_{gb}_velocity_ms"] = round(
                    dist * 1_000.0 / flight_us, 3
                )

    return stats


def _trend(cur, prev):
    """Return 'improving', 'declining', 'level', or None."""
    if cur is None or prev is None or prev == 0:
        return None
    ratio = cur / prev
    if ratio > 1 + _TREND_THRESHOLD:
        return "improving"
    if ratio < 1 - _TREND_THRESHOLD:
        return "declining"
    return "level"


@api_bp.route("/sequences")
def list_sequences():
    """All unique sequences with run count, time range, and notes."""
    rows = (
        db.session.query(
            EventLog.run_sequence_id,
            func.count(EventLog.id).label("run_count"),
            func.min(EventLog.created_at).label("first_run"),
            func.max(EventLog.created_at).label("last_run"),
        )
        .group_by(EventLog.run_sequence_id)
        .order_by(func.max(EventLog.created_at).desc())
        .all()
    )
    # Pre-load all sequence notes in one query
    seq_ids = [r.run_sequence_id for r in rows]
    notes_map = {}
    if seq_ids:
        for sn in SequenceNote.query.filter(
            SequenceNote.run_sequence_id.in_(seq_ids)
        ).all():
            notes_map[sn.run_sequence_id] = sn.notes

    return jsonify([
        {
            "run_sequence_id": r.run_sequence_id,
            "run_count": r.run_count,
            "first_run": r.first_run.isoformat() if r.first_run else None,
            "last_run": r.last_run.isoformat() if r.last_run else None,
            "notes": notes_map.get(r.run_sequence_id, ""),
        }
        for r in rows
    ])


@api_bp.route("/analysis/runs")
def analysis_runs():
    """Runs for a sequence with computed velocities, trends, and aggregates.

    Query params:
      sequence_id  – required
    """
    seq_id = request.args.get("sequence_id")
    if not seq_id:
        return jsonify({"error": "sequence_id required"}), 400

    events = (
        EventLog.query
        .filter_by(run_sequence_id=seq_id)
        .order_by(EventLog.run_number.asc())
        .all()
    )
    if not events:
        return jsonify({"summary": {}, "runs": []})

    # Pre-load all referenced config snapshots in one query
    snap_ids = {ev.config_snapshot_id for ev in events if ev.config_snapshot_id}
    snaps = {}
    if snap_ids:
        for s in ConfigSnapshot.query.filter(ConfigSnapshot.id.in_(snap_ids)).all():
            snaps[s.id] = s

    # Fallback config: most recent snapshot for this sequence
    fallback = (
        ConfigSnapshot.query
        .filter_by(run_sequence_id=seq_id)
        .order_by(ConfigSnapshot.id.desc())
        .first()
    )

    # Build run records (ascending order for trend computation)
    runs = []
    prev_stats = {}
    prev_cfg = None
    for ev in events:
        cfg = snaps.get(ev.config_snapshot_id, fallback)
        if cfg is None:
            continue

        stats = _compute_run_velocities(ev, cfg)

        # Trends vs previous run
        trends = {}
        for vk in VELOCITY_KEYS:
            trends[vk] = _trend(stats.get(vk), prev_stats.get(vk))

        # Config deltas vs previous run
        config_deltas = {}
        if prev_cfg is not None and cfg.id != prev_cfg.id:
            for key in ConfigSnapshot.PARAM_KEYS:
                curr_val = getattr(cfg, key)
                prev_val = getattr(prev_cfg, key)
                if curr_val != prev_val:
                    config_deltas[key] = {"prev": prev_val, "curr": curr_val}

        runs.append({
            "id": ev.id,
            "run_number": ev.run_number,
            "created_at": ev.created_at.isoformat(),
            **{k: stats.get(k) for k in TIMING_KEYS},
            **{k: stats.get(k) for k in VELOCITY_KEYS},
            "trends": trends,
            "config_deltas": config_deltas,
        })
        prev_stats = stats
        prev_cfg = cfg

    # Aggregates over the whole sequence
    summary = {}
    for vk in VELOCITY_KEYS:
        vals = [r[vk] for r in runs if r.get(vk) is not None]
        if vals:
            summary[vk] = {
                "min": round(min(vals), 3),
                "max": round(max(vals), 3),
                "avg": round(sum(vals) / len(vals), 3),
                "count": len(vals),
            }

    # Reverse to timestamp DESC for the response
    runs.reverse()

    return jsonify({"summary": summary, "runs": runs})


@api_bp.route("/analysis/overview")
def analysis_overview():
    """Per-sequence average velocities for the cross-sequence trend chart.

    Returns the most recent five sequences ordered by first_run ASC
    (chronological) so the chart reads left-to-right in time. Older
    sequences are omitted so calibration / yardstick / wrong-config
    sessions don't dominate the y-axis indefinitely.
    """
    seq_rows = (
        db.session.query(
            EventLog.run_sequence_id,
            func.count(EventLog.id).label("run_count"),
            func.min(EventLog.created_at).label("first_run"),
        )
        .group_by(EventLog.run_sequence_id)
        .order_by(func.min(EventLog.created_at).desc())
        .limit(5)
        .all()
    )
    # We pulled DESC to apply LIMIT; flip back to chronological for the chart.
    seq_rows = list(reversed(seq_rows))

    if not seq_rows:
        return jsonify([])

    result = []
    for sr in seq_rows:
        events = (
            EventLog.query
            .filter_by(run_sequence_id=sr.run_sequence_id)
            .all()
        )
        snap_ids = {ev.config_snapshot_id for ev in events if ev.config_snapshot_id}
        snaps = {}
        if snap_ids:
            for s in ConfigSnapshot.query.filter(ConfigSnapshot.id.in_(snap_ids)).all():
                snaps[s.id] = s
        fallback = (
            ConfigSnapshot.query
            .filter_by(run_sequence_id=sr.run_sequence_id)
            .order_by(ConfigSnapshot.id.desc())
            .first()
        )

        # Accumulate velocities
        accum = defaultdict(list)
        for ev in events:
            cfg = snaps.get(ev.config_snapshot_id, fallback)
            if cfg is None:
                continue
            stats = _compute_run_velocities(ev, cfg)
            for vk in VELOCITY_KEYS:
                v = stats.get(vk)
                if v is not None:
                    accum[vk].append(v)

        entry = {
            "run_sequence_id": sr.run_sequence_id,
            "run_count": sr.run_count,
            "first_run": sr.first_run.isoformat() if sr.first_run else None,
        }
        for vk in VELOCITY_KEYS:
            vals = accum[vk]
            entry["avg_" + vk] = round(sum(vals) / len(vals), 3) if vals else None
        result.append(entry)

    return jsonify(result)


# ── Skill results viewer ────────────────────────────────────────────────

_RESULTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "optimize_coil_gun_velocity_skill_results",
)


@api_bp.route("/skill-results")
def list_skill_results():
    """List available analysis result files, sorted newest first."""
    if not os.path.isdir(_RESULTS_DIR):
        return jsonify([])

    files = []
    for name in sorted(os.listdir(_RESULTS_DIR), reverse=True):
        if name.startswith("analysis_") and name.endswith(".md"):
            path = os.path.join(_RESULTS_DIR, name)
            # Extract timestamp from filename: analysis_YYYY-MM-DD_HH-MM-SS.md
            ts = name.replace("analysis_", "").replace(".md", "").replace("_", " ", 1)
            files.append({
                "filename": name,
                "timestamp": ts,
                "size": os.path.getsize(path),
            })
    return jsonify(files)


@api_bp.route("/skill-results/file/<filename>")
def get_skill_result(filename):
    """Return the raw markdown content of a result file."""
    # Sanitize: only allow filenames matching expected pattern
    if not filename.endswith(".md") or "/" in filename or "\\" in filename:
        return jsonify({"error": "Invalid filename"}), 400

    path = os.path.join(_RESULTS_DIR, filename)
    if not os.path.isfile(path):
        return jsonify({"error": "File not found"}), 404

    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    return jsonify({"filename": filename, "content": content})


@api_bp.route("/skill-results/history")
def get_skill_history():
    """Return the persistent optimization history file."""
    path = os.path.join(_RESULTS_DIR, "velocity_optimization_history.md")
    if not os.path.isfile(path):
        return jsonify({"content": None})

    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    return jsonify({"content": content})
