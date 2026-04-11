#!/usr/bin/env python3
"""Velocity optimization analysis for the coil-gun sequencer.

Reads the SQLite database, computes per-run velocities, correlates config
parameters with velocity outcomes, and outputs structured JSON for the
/optimize-coil-gun-velocity skill.

Usage:
    python tools/analyze_velocity.py [--limit N]

Outputs JSON to stdout.  No external dependencies beyond Python stdlib.
"""

import argparse
import json
import math
import os
import sqlite3
import sys

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "sequencer.db")

CONFIG_PARAMS = [
    "projectile_length_mm",
    "projectile_mass_grams",
    "v_coil_floor",
    "v_coil_ceiling",
    "gate_1_coil_2_delay_us",
    "gate_2_coil_3_delay_us",
    "coil_1_pulse_duration_us",
    "coil_2_pulse_duration_us",
    "coil_3_pulse_duration_us",
    "gate_1_to_gate_2_distance_mm",
    "gate_2_to_gate_3_distance_mm",
]

VELOCITY_METRICS = [
    "gate_1_transit_velocity_ms",
    "gate_2_transit_velocity_ms",
    "gate_3_transit_velocity_ms",
    "gate_1_to_gate_2_velocity_ms",
    "gate_2_to_gate_3_velocity_ms",
]

VELOCITY_LABELS = {
    "gate_1_transit_velocity_ms": "G1 Transit",
    "gate_2_transit_velocity_ms": "G2 Transit",
    "gate_3_transit_velocity_ms": "G3 Transit",
    "gate_1_to_gate_2_velocity_ms": "G1→G2 Flight",
    "gate_2_to_gate_3_velocity_ms": "G2→G3 Muzzle",
}


# ── Database queries ─────────────────────────────────────────────────────

def get_sequences(conn, limit=5):
    """Return the most recent *limit* sequences with run counts."""
    return conn.execute("""
        SELECT run_sequence_id,
               COUNT(*)          AS run_count,
               MIN(created_at)   AS first_run,
               MAX(created_at)   AS last_run
        FROM event_logs
        GROUP BY run_sequence_id
        ORDER BY MAX(created_at) DESC
        LIMIT ?
    """, (limit,)).fetchall()


def get_runs_with_config(conn, sequence_ids):
    """Return all runs for the given sequences, joined with their config."""
    placeholders = ",".join("?" for _ in sequence_ids)
    return conn.execute(f"""
        SELECT
            e.id            AS event_id,
            e.run_sequence_id,
            e.run_number,
            e.created_at,
            e.t_gate_1_on,  e.t_gate_1_off,
            e.t_gate_2_on,  e.t_gate_2_off,
            e.t_gate_3_on,  e.t_gate_3_off,
            c.projectile_length_mm,
            c.projectile_mass_grams,
            c.v_coil_floor,
            c.v_coil_ceiling,
            c.gate_1_coil_2_delay_us,
            c.gate_2_coil_3_delay_us,
            c.coil_1_pulse_duration_us,
            c.coil_2_pulse_duration_us,
            c.coil_3_pulse_duration_us,
            c.gate_1_to_gate_2_distance_mm,
            c.gate_2_to_gate_3_distance_mm
        FROM event_logs e
        LEFT JOIN config_snapshots c ON e.config_snapshot_id = c.id
        WHERE e.run_sequence_id IN ({placeholders})
        ORDER BY e.created_at ASC
    """, sequence_ids).fetchall()


# ── Velocity computation ─────────────────────────────────────────────────

def compute_velocities(row):
    """Compute velocity metrics from a joined run+config row."""
    vels = {}
    proj_len = row["projectile_length_mm"] or 0

    for g in (1, 2, 3):
        on = row[f"t_gate_{g}_on"]
        off = row[f"t_gate_{g}_off"]
        if on is not None and off is not None:
            transit_us = (off - on) / 1_000.0
            if transit_us > 0 and proj_len > 0:
                vels[f"gate_{g}_transit_velocity_ms"] = round(
                    proj_len * 1_000.0 / transit_us, 4
                )

    pairs = [
        (1, 2, row["gate_1_to_gate_2_distance_mm"]),
        (2, 3, row["gate_2_to_gate_3_distance_mm"]),
    ]
    for ga, gb, dist in pairs:
        on_a = row[f"t_gate_{ga}_on"]
        on_b = row[f"t_gate_{gb}_on"]
        if on_a is not None and on_b is not None:
            flight_us = (on_b - on_a) / 1_000.0
            if flight_us > 0 and dist and dist > 0:
                vels[f"gate_{ga}_to_gate_{gb}_velocity_ms"] = round(
                    dist * 1_000.0 / flight_us, 4
                )

    return vels


# ── Statistical helpers ──────────────────────────────────────────────────

def pearson_r(xs, ys):
    """Pearson correlation coefficient.  Returns None if insufficient data."""
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return None
    return round(num / (dx * dy), 4)


def agg(vals):
    """Min / max / mean / stdev for a list of numbers."""
    if not vals:
        return None
    n = len(vals)
    mn = min(vals)
    mx = max(vals)
    avg = sum(vals) / n
    var = sum((v - avg) ** 2 for v in vals) / n if n > 1 else 0
    return {"min": round(mn, 4), "max": round(mx, 4),
            "avg": round(avg, 4), "stdev": round(math.sqrt(var), 4), "n": n}


# ── Core analysis ────────────────────────────────────────────────────────

def analyze(db_path, seq_limit=5):
    if not os.path.exists(db_path):
        return {"error": "Database not found at " + db_path}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    sequences = get_sequences(conn, seq_limit)
    if not sequences:
        conn.close()
        return {"error": "No sequences found in database"}

    seq_ids = [s["run_sequence_id"] for s in sequences]
    raw_runs = get_runs_with_config(conn, seq_ids)
    conn.close()

    # ── Build enriched run records ───────────────────────────────────────

    runs = []
    for row in raw_runs:
        vels = compute_velocities(row)
        cfg = {p: row[p] for p in CONFIG_PARAMS if row[p] is not None}
        runs.append({
            "run_sequence_id": row["run_sequence_id"],
            "run_number": row["run_number"],
            "created_at": row["created_at"],
            "config": cfg,
            "velocities": vels,
        })

    # ── Per-sequence summaries ───────────────────────────────────────────

    seq_summaries = []
    for seq in sequences:
        sid = seq["run_sequence_id"]
        seq_runs = [r for r in runs if r["run_sequence_id"] == sid]
        vel_aggs = {}
        for vm in VELOCITY_METRICS:
            vals = [r["velocities"][vm] for r in seq_runs if vm in r["velocities"]]
            if vals:
                vel_aggs[vm] = agg(vals)
        seq_summaries.append({
            "run_sequence_id": sid,
            "run_count": seq["run_count"],
            "first_run": seq["first_run"],
            "last_run": seq["last_run"],
            "velocity_summary": vel_aggs,
        })

    # ── Correlation: config params vs velocity ───────────────────────────
    #    For each (param, velocity_metric) pair, compute Pearson r across
    #    all runs that have both values.

    correlations = {}
    for param in CONFIG_PARAMS:
        correlations[param] = {}
        for vm in VELOCITY_METRICS:
            pairs = [
                (r["config"].get(param), r["velocities"].get(vm))
                for r in runs
                if r["config"].get(param) is not None and vm in r["velocities"]
            ]
            if len(pairs) >= 3:
                xs, ys = zip(*pairs)
                # Only meaningful if the param actually varies
                if len(set(xs)) > 1:
                    correlations[param][vm] = pearson_r(list(xs), list(ys))

    # ── Feature importance ranking ───────────────────────────────────────
    #    Rank params by their max |correlation| with any velocity metric.

    importance = []
    for param in CONFIG_PARAMS:
        cors = correlations.get(param, {})
        abs_cors = [abs(v) for v in cors.values() if v is not None]
        if abs_cors:
            importance.append({
                "param": param,
                "max_abs_correlation": round(max(abs_cors), 4),
                "correlations": {k: v for k, v in cors.items() if v is not None},
            })
    importance.sort(key=lambda x: x["max_abs_correlation"], reverse=True)

    # ── Best-performing config ───────────────────────────────────────────
    #    Find the run with the highest average velocity across all available
    #    metrics, and report its config.

    best_run = None
    best_score = -1
    for r in runs:
        vels = list(r["velocities"].values())
        if vels:
            score = sum(vels) / len(vels)
            if score > best_score:
                best_score = score
                best_run = r

    # ── Config parameter ranges across all runs ──────────────────────────

    param_ranges = {}
    for param in CONFIG_PARAMS:
        vals = [r["config"][param] for r in runs if param in r["config"]]
        if vals:
            param_ranges[param] = {
                "min": min(vals),
                "max": max(vals),
                "unique_values": sorted(set(vals)),
            }

    # ── Delta analysis: before/after config changes ──────────────────────
    #    Within each sequence, find runs where config changed and measure
    #    the velocity delta.

    config_change_impacts = []
    for seq in sequences:
        sid = seq["run_sequence_id"]
        seq_runs = sorted(
            [r for r in runs if r["run_sequence_id"] == sid],
            key=lambda r: r["run_number"],
        )
        for i in range(1, len(seq_runs)):
            prev_cfg = seq_runs[i - 1]["config"]
            curr_cfg = seq_runs[i]["config"]
            changed_params = {
                k: {"prev": prev_cfg.get(k), "curr": curr_cfg.get(k)}
                for k in CONFIG_PARAMS
                if prev_cfg.get(k) != curr_cfg.get(k)
                and prev_cfg.get(k) is not None
                and curr_cfg.get(k) is not None
            }
            if not changed_params:
                continue

            prev_vels = seq_runs[i - 1]["velocities"]
            curr_vels = seq_runs[i]["velocities"]
            vel_deltas = {}
            for vm in VELOCITY_METRICS:
                if vm in prev_vels and vm in curr_vels:
                    vel_deltas[vm] = round(curr_vels[vm] - prev_vels[vm], 4)

            if vel_deltas:
                config_change_impacts.append({
                    "sequence": sid[:8],
                    "from_run": seq_runs[i - 1]["run_number"],
                    "to_run": seq_runs[i]["run_number"],
                    "param_changes": changed_params,
                    "velocity_deltas": vel_deltas,
                })

    # ── Assemble output ──────────────────────────────────────────────────

    return {
        "dataset_summary": {
            "sequences_analyzed": len(sequences),
            "total_runs": len(runs),
        },
        "sequence_summaries": seq_summaries,
        "correlations": correlations,
        "feature_importance": importance,
        "config_change_impacts": config_change_impacts,
        "param_ranges": param_ranges,
        "best_run": {
            "config": best_run["config"] if best_run else {},
            "velocities": best_run["velocities"] if best_run else {},
            "run_number": best_run["run_number"] if best_run else None,
            "sequence": best_run["run_sequence_id"][:8] if best_run else None,
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Coil-gun velocity analysis")
    parser.add_argument("--limit", type=int, default=5,
                        help="Number of most-recent sequences to analyze")
    parser.add_argument("--db", default=DB_PATH,
                        help="Path to SQLite database")
    args = parser.parse_args()

    result = analyze(args.db, args.limit)
    json.dump(result, sys.stdout, indent=2)
    print()
