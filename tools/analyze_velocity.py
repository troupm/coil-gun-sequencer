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
    "capacitor_bank_size_uf",
    "rail_source_active",
    "coil_1_brake_resistor_ohms",
    "coil_2_brake_resistor_ohms",
    "coil_1_resistance_ohms",
    "coil_1_inductance_uh",
    "coil_2_resistance_ohms",
    "coil_2_inductance_uh",
    "coil_3_resistance_ohms",
    "coil_3_inductance_uh",
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
            c.gate_2_to_gate_3_distance_mm,
            c.capacitor_bank_size_uf,
            c.rail_source_active,
            c.coil_1_brake_resistor_ohms,
            c.coil_2_brake_resistor_ohms,
            c.coil_1_resistance_ohms,
            c.coil_1_inductance_uh,
            c.coil_2_resistance_ohms,
            c.coil_2_inductance_uh,
            c.coil_3_resistance_ohms,
            c.coil_3_inductance_uh
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


def _quantile(sorted_vals, q):
    """Linear-interpolation quantile on a pre-sorted list (q in [0,1])."""
    if not sorted_vals:
        return 0.0
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    pos = q * (n - 1)
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def _median(vals):
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    if n % 2:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2.0


# ── Outlier filtering (asymmetric, trailing-window, per-metric) ──────────
#
# Design intent:
#   * Low outliers (velocity < Q1 − 1.5·IQR against the trailing window)
#     are almost always mechanical failures — projectile stuck, coil
#     misfire, sensor glitch. Drop them.
#   * High outliers (velocity > Q3 + 1.5·IQR) are exactly the thing we
#     are trying to find. Keep them in the dataset and FLAG them so the
#     skill/operator can pay extra attention.
#   * Rolling trailing window (default size 10) lets the "local normal"
#     drift as the operator tunes config over the course of a sequence.
#     A run that is fast against runs 1–10 and slow against runs 20–30
#     is correctly judged against its own contemporaneous baseline.
#   * Minimum sequence size = window size. On <10-run sequences, the
#     filter is skipped entirely (you cannot meaningfully distinguish
#     an outlier from natural variation on fewer samples than that).
#   * Filter is per-metric: a run can be an outlier under G1→G2 velocity
#     but clean under G2→G3 (e.g. gate 1 sensor misfire, muzzle fine).

_TRAILING_WINDOW_SIZE = 10


def _trailing_outlier_filter(seq_runs, vel_metric, window_size=_TRAILING_WINDOW_SIZE):
    """Return (kept, dropped_low, flagged_high) for one sequence + one metric.

    *seq_runs* must all belong to the same run_sequence_id. Runs without
    the target metric are passed through unfiltered (they can't be judged
    against a metric they don't report).
    """
    if len(seq_runs) < window_size:
        # Not enough samples for the filter to mean anything.
        return list(seq_runs), [], []

    sorted_runs = sorted(seq_runs, key=lambda r: r["run_number"])
    kept, dropped_low, flagged_high = [], [], []

    for i, run in enumerate(sorted_runs):
        vel = run["velocities"].get(vel_metric)
        if vel is None:
            kept.append(run)
            continue

        # Trailing window: up to *window_size* runs strictly BEFORE this one.
        window_start = max(0, i - window_size)
        window_vels = [
            r["velocities"][vel_metric]
            for r in sorted_runs[window_start:i]
            if vel_metric in r["velocities"]
        ]

        if len(window_vels) < window_size:
            # Not enough history yet — can't judge this run.
            kept.append(run)
            continue

        sorted_w = sorted(window_vels)
        q1 = _quantile(sorted_w, 0.25)
        q3 = _quantile(sorted_w, 0.75)
        iqr = q3 - q1
        low_fence = q1 - 1.5 * iqr
        high_fence = q3 + 1.5 * iqr

        if vel < low_fence:
            dropped_low.append({
                "run_number": run["run_number"],
                "velocity": round(vel, 4),
                "low_fence": round(low_fence, 4),
                "window_median": round(_median(window_vels), 4),
                "reason": (
                    f"velocity {vel:.3f} < trailing-{window_size} "
                    f"low fence {low_fence:.3f}"
                ),
            })
            continue

        kept.append(run)
        if vel > high_fence:
            flagged_high.append({
                "run_number": run["run_number"],
                "velocity": round(vel, 4),
                "high_fence": round(high_fence, 4),
                "window_median": round(_median(window_vels), 4),
            })

    return kept, dropped_low, flagged_high


def _filter_runs_for_metric(runs, vel_metric):
    """Group *runs* by sequence, apply the trailing outlier filter to each,
    and return (kept, dropped_low, flagged_high, stats_per_seq)."""
    by_seq = {}
    for r in runs:
        by_seq.setdefault(r["run_sequence_id"], []).append(r)

    all_kept, all_dropped, all_flagged = [], [], []
    per_seq = {}
    for sid, seq_runs in by_seq.items():
        kept, dropped, flagged = _trailing_outlier_filter(seq_runs, vel_metric)
        all_kept.extend(kept)
        all_dropped.extend(dropped)
        all_flagged.extend(flagged)
        if len(seq_runs) >= _TRAILING_WINDOW_SIZE:
            per_seq[sid[:8]] = {
                "total_runs": len(seq_runs),
                "kept": len(kept),
                "dropped_low": len(dropped),
                "flagged_high": len(flagged),
            }

    return all_kept, all_dropped, all_flagged, per_seq


# ── Top-quartile config profile (boosted-stump style) ────────────────────
#
# For each velocity metric, partition the (outlier-filtered) runs into the
# fastest 25% and the rest, and for each config parameter compute the
# mean-shift between the two groups plus a t-statistic. Ranked by |t|, the
# top rows answer "what config values do the fast runs share that the slow
# ones don't?" — directly usable as a recommendation input and far more
# robust than looking at a single peak run.

_TOP_QUARTILE_MIN_SAMPLES = 8  # need ≥2 in top quartile, ≥6 in rest


def _top_quartile_profile(runs, vel_metric, config_params):
    """Return a top-25% vs rest config profile for *vel_metric*, or None."""
    rs = [r for r in runs if vel_metric in r["velocities"]]
    if len(rs) < _TOP_QUARTILE_MIN_SAMPLES:
        return None

    rs.sort(key=lambda r: r["velocities"][vel_metric], reverse=True)
    k = max(2, len(rs) // 4)
    top, rest = rs[:k], rs[k:]
    if len(rest) < 2:
        return None

    profile = []
    for param in config_params:
        top_vals = [r["config"][param] for r in top if param in r["config"]]
        rest_vals = [r["config"][param] for r in rest if param in r["config"]]
        if len(top_vals) < 2 or len(rest_vals) < 2:
            continue
        if len(set(top_vals + rest_vals)) < 2:
            continue  # Parameter is constant — nothing to say.

        top_mean = sum(top_vals) / len(top_vals)
        rest_mean = sum(rest_vals) / len(rest_vals)

        top_var = sum((v - top_mean) ** 2 for v in top_vals) / max(1, len(top_vals) - 1)
        rest_var = sum((v - rest_mean) ** 2 for v in rest_vals) / max(1, len(rest_vals) - 1)
        pooled_se = math.sqrt(top_var / len(top_vals) + rest_var / len(rest_vals))
        if pooled_se == 0:
            continue
        t_stat = (top_mean - rest_mean) / pooled_se

        profile.append({
            "param": param,
            "top_mean": round(top_mean, 4),
            "rest_mean": round(rest_mean, 4),
            "delta": round(top_mean - rest_mean, 4),
            "t_stat": round(t_stat, 3),
            "top_n": len(top_vals),
            "rest_n": len(rest_vals),
        })

    profile.sort(key=lambda p: abs(p["t_stat"]), reverse=True)

    top_vels = [r["velocities"][vel_metric] for r in top]
    rest_vels = [r["velocities"][vel_metric] for r in rest]
    return {
        "velocity_metric": vel_metric,
        "top_count": len(top),
        "rest_count": len(rest),
        "top_velocity_range": [round(min(top_vels), 4), round(max(top_vels), 4)],
        "rest_velocity_range": [round(min(rest_vels), 4), round(max(rest_vels), 4)],
        "top_velocity_median": round(_median(top_vels), 4),
        "rest_velocity_median": round(_median(rest_vels), 4),
        "param_profiles": profile,
    }


# ── Top-N median config (noise-robust recommendation) ───────────────────
#
# Replaces the naive "best_run = single max" approach for recommendations.
# Picking the fastest single run produced demonstrably bad advice on the
# current dataset (it surfaced a 3090 µs coil_1 pulse that every A/B sweep
# disagreed with — a 1σ outlier). Median config across the top 5 runs is
# far more robust: an outlier run contributes one vote out of five, not
# the entire recommendation.

def _top_n_median_config(runs, vel_metric, config_params, n=5):
    rs = [r for r in runs if vel_metric in r["velocities"]]
    if len(rs) < n:
        return None

    rs.sort(key=lambda r: r["velocities"][vel_metric], reverse=True)
    top = rs[:n]
    vels = [r["velocities"][vel_metric] for r in top]

    median_config = {}
    for param in config_params:
        vals = [r["config"][param] for r in top if param in r["config"]]
        if vals:
            median_config[param] = round(_median(vals), 4)

    return {
        "n": n,
        "velocity_metric": vel_metric,
        "median_velocity": round(_median(vels), 4),
        "velocity_range": [round(min(vels), 4), round(max(vels), 4)],
        "median_config": median_config,
        "top_runs": [
            {
                "sequence": r["run_sequence_id"][:8],
                "run_number": r["run_number"],
                "velocity": round(r["velocities"][vel_metric], 4),
            }
            for r in top
        ],
    }


# ── Inflection points / watermark narrative ─────────────────────────────
#
# Walks the primary-metric velocity series for each sequence in run-number
# order and emits a narrative-friendly timeline of peaks, troughs, and
# high/low water-mark events, each annotated with the config parameters
# that changed since the prior event. This is the "intuitive feature
# importance" companion to the quantitative top-quartile profile: where
# top-quartile tells you *which knobs matter*, inflection points tell you
# *what tweak the operator made, and what it did next*.
#
# Smoothing: a 5-point centered rolling mean on the outlier-FILTERED series
# (kept runs only). We specifically don't use median smoothing — a 3-point
# median masks the center position with the window median, which turns
# raw peaks into "troughs" whenever the center is the local high and its
# neighbors are slightly lower. Rolling-mean smoothing on a
# post-outlier-filter series is noise-robust enough (the hard misfires
# are already gone) and preserves the local-extremum structure.
#
# Watermarks are tracked on raw velocities in the kept series: a "new
# high watermark" is an extremum position whose raw velocity exceeds
# every previously-seen kept raw velocity. That matches the operator's
# intuition of "this run set a new record" rather than a subtler
# smoothed-trend definition.

# Minimum sequence size before inflection-point analysis runs. Matches the
# outlier filter's _TRAILING_WINDOW_SIZE — on fewer samples the narrative
# is dominated by individual-run noise and isn't worth rendering.
_INFLECTION_MIN_SEQUENCE_RUNS = 10
_SMOOTH_WINDOW = 5


def _rolling_mean(vals, window=_SMOOTH_WINDOW):
    """Centered rolling mean with a shrinking window at the edges."""
    n = len(vals)
    if n == 0:
        return []
    half = window // 2
    out = [0.0] * n
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        w = vals[lo:hi]
        out[i] = sum(w) / len(w)
    return out


def _config_delta(prev_cfg, curr_cfg):
    """Return {param: {prev, curr}} for any key where prev and curr differ."""
    changed = {}
    for k in set(prev_cfg.keys()) | set(curr_cfg.keys()):
        pv = prev_cfg.get(k)
        cv = curr_cfg.get(k)
        if pv != cv:
            changed[k] = {"prev": pv, "curr": cv}
    return changed


def _inflection_points_for_sequence(seq_runs, vel_metric, kept_run_ids):
    """Build a timeline + inflection-event list for one sequence.

    seq_runs:     list of enriched run dicts (order-independent; sorted here)
    vel_metric:   velocity metric key to analyze
    kept_run_ids: set of id(run) values for runs that survived the
                  outlier filter for this metric

    Returns {
        'velocity_metric': str,
        'timeline': [{run_number, velocity, smoothed, filtered}, ...],
                    — all runs reporting the metric, in run-number order.
                    `smoothed` is None for outlier-filtered runs.
        'events':   [{run_number, velocity, smoothed, kind,
                      is_new_high_watermark, is_new_low_watermark,
                      delta_since_prior_event,
                      config_delta_since_prior}, ...],
                    — extrema of the smoothed signal on the KEPT series,
                    each annotated with raw-velocity watermark status and
                    the config delta from the previous extremum.
    }

    Returns None for sequences too small to produce a meaningful timeline.
    """
    # Full display timeline — every run reporting the metric, in order.
    all_entries = []
    for r in sorted(seq_runs, key=lambda x: x["run_number"]):
        v = r["velocities"].get(vel_metric)
        if v is None:
            continue
        all_entries.append({
            "run_number": r["run_number"],
            "velocity": round(v, 4),
            "filtered": id(r) not in kept_run_ids,
            "_config": r["config"],
        })
    if len(all_entries) < _INFLECTION_MIN_SEQUENCE_RUNS:
        return None

    # Analytical series: kept runs only. This is what gets smoothed and
    # walked for extrema. Display timeline will still show filtered runs
    # so the operator can see outliers in context.
    clean = [e for e in all_entries if not e["filtered"]]
    if len(clean) < 3:
        return None

    smoothed = _rolling_mean([e["velocity"] for e in clean])
    for e, s in zip(clean, smoothed):
        e["smoothed"] = round(s, 4)

    # Back-fill the display timeline's smoothed column: kept positions get
    # their analytical smoothed value, filtered positions stay at None.
    clean_by_rn = {e["run_number"]: e["smoothed"] for e in clean}
    for e in all_entries:
        e["smoothed"] = clean_by_rn.get(e["run_number"]) if not e["filtered"] else None

    # Local-extrema detection on the smoothed kept series.
    events = []
    running_max = clean[0]["velocity"]
    running_min = clean[0]["velocity"]
    prior_event_idx = None

    for i in range(1, len(clean) - 1):
        prev_s = clean[i - 1]["smoothed"]
        curr_s = clean[i]["smoothed"]
        next_s = clean[i + 1]["smoothed"]

        # Fold clean[i-1] into the running max/min BEFORE comparing the
        # current position, so the comparison is "does this position beat
        # the best seen so far".
        running_max = max(running_max, clean[i - 1]["velocity"])
        running_min = min(running_min, clean[i - 1]["velocity"])

        is_peak = curr_s > prev_s and curr_s > next_s
        is_trough = curr_s < prev_s and curr_s < next_s
        if not (is_peak or is_trough):
            continue

        curr_v = clean[i]["velocity"]
        new_high = is_peak and curr_v > running_max
        new_low = is_trough and curr_v < running_min

        if prior_event_idx is None:
            prior_cfg = clean[0]["_config"]
            delta_v = None
        else:
            prior = clean[prior_event_idx]
            prior_cfg = prior["_config"]
            delta_v = round(curr_s - prior["smoothed"], 4)

        events.append({
            "run_number": clean[i]["run_number"],
            "velocity": clean[i]["velocity"],
            "smoothed": clean[i]["smoothed"],
            "kind": "peak" if is_peak else "trough",
            "is_new_high_watermark": new_high,
            "is_new_low_watermark": new_low,
            "delta_since_prior_event": delta_v,
            "config_delta_since_prior": _config_delta(prior_cfg, clean[i]["_config"]),
        })
        prior_event_idx = i

    # Strip the private _config refs so the return value is JSON-safe.
    for e in all_entries:
        e.pop("_config", None)
    for e in clean:
        e.pop("_config", None)

    return {
        "velocity_metric": vel_metric,
        "timeline": all_entries,
        "events": events,
    }


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

    # ── Per-sequence summaries (on UNFILTERED data) ──────────────────────
    #     Raw stats so the operator sees what their session actually looked
    #     like, outliers and all. The filtered view drives analysis.

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

    # ── Outlier filter: one pass per velocity metric ─────────────────────
    #     Asymmetric trailing-window filter. Low outliers (mechanical
    #     failures) are dropped; high outliers (real wins) are kept and
    #     flagged. Cached once per metric so downstream analysis reuses
    #     the same filtered set. See _trailing_outlier_filter for details.

    filtered_by_metric = {}
    outlier_summary = {}
    for vm in VELOCITY_METRICS:
        avail = [r for r in runs if vm in r["velocities"]]
        if not avail:
            continue
        kept, dropped, flagged, per_seq = _filter_runs_for_metric(avail, vm)
        filtered_by_metric[vm] = kept
        if per_seq or dropped or flagged:
            outlier_summary[vm] = {
                "total_eligible_runs": len(avail),
                "kept_runs": len(kept),
                "dropped_low_count": len(dropped),
                "flagged_high_count": len(flagged),
                "per_sequence": per_seq,
                # Cap detail lists so the output stays readable on large datasets.
                "dropped_low_detail": dropped[:10],
                "flagged_high_detail": flagged[:10],
            }

    # Pick the "primary" metric for best-run / best-top5 selection: whichever
    # metric has the most runs after filtering. On the current rig that's
    # almost always gate_1_to_gate_2_velocity_ms since gate 3 is unattached.
    primary_metric = None
    if filtered_by_metric:
        primary_metric = max(
            filtered_by_metric.keys(),
            key=lambda vm: len(filtered_by_metric[vm]),
        )

    # ── Correlation: config params vs velocity ───────────────────────────
    #    Uses the per-metric filtered set. Retained for backward compat
    #    with the existing skill prompt; the top-quartile profile below
    #    is the more operator-useful view.

    correlations = {}
    for param in CONFIG_PARAMS:
        correlations[param] = {}
        for vm in VELOCITY_METRICS:
            metric_runs = filtered_by_metric.get(vm, [])
            pairs = [
                (r["config"].get(param), r["velocities"].get(vm))
                for r in metric_runs
                if r["config"].get(param) is not None and vm in r["velocities"]
            ]
            if len(pairs) >= 3:
                xs, ys = zip(*pairs)
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

    # ── Best-performing single run (backward-compat, use with caution) ──
    #    NOTE: this is the raw maximum across ALL filtered metrics. For
    #    recommendations prefer `best_top5_median` below — a single run
    #    is heavily noise-sensitive on this rig (CV ~33%).

    #    best_run is computed from the union of per-metric filtered sets
    #    so mechanical-failure outliers don't qualify.
    best_run_pool = {id(r): r for vm_runs in filtered_by_metric.values() for r in vm_runs}
    best_run = None
    best_score = -1
    for r in best_run_pool.values():
        vels = list(r["velocities"].values())
        if vels:
            score = sum(vels) / len(vels)
            if score > best_score:
                best_score = score
                best_run = r

    # ── Top-5-median config (primary recommendation input) ──────────────

    best_top5_median = None
    if primary_metric:
        best_top5_median = _top_n_median_config(
            filtered_by_metric[primary_metric],
            primary_metric,
            CONFIG_PARAMS,
            n=5,
        )

    # ── Top-quartile config profile (boosted-stump style) ───────────────
    #    Per velocity metric, compares the fastest 25% of runs to the rest
    #    for every config param. Ranked by |t-stat|, the top rows answer
    #    "what config values do the fast runs share?" — the question the
    #    operator actually cares about, with noise tolerance the single-
    #    best-run approach doesn't have.

    top_quartile_profiles = {}
    for vm, metric_runs in filtered_by_metric.items():
        prof = _top_quartile_profile(metric_runs, vm, CONFIG_PARAMS)
        if prof is not None:
            top_quartile_profiles[vm] = prof

    # ── Inflection points / watermark narrative ─────────────────────────
    #    Per-sequence walkthrough of smoothed peaks, troughs, and watermark
    #    events on the primary metric, annotated with the config deltas
    #    between events. This is the "intuitive feature importance"
    #    companion to the top-quartile profile; see
    #    _inflection_points_for_sequence for design notes.

    inflection_points = {}
    if primary_metric:
        primary_kept_ids = {id(r) for r in filtered_by_metric.get(primary_metric, [])}
        for seq in sequences:
            sid = seq["run_sequence_id"]
            seq_runs = [r for r in runs if r["run_sequence_id"] == sid]
            if len(seq_runs) < _INFLECTION_MIN_SEQUENCE_RUNS:
                continue
            ip = _inflection_points_for_sequence(
                seq_runs, primary_metric, primary_kept_ids
            )
            if ip is not None:
                inflection_points[sid[:8]] = ip

    # ── Config parameter ranges across all runs (UNFILTERED) ────────────
    #    Intentionally unfiltered — operators want to see what they
    #    actually tested, not what survived the outlier filter.

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
    #    Uses per-metric filtered runs. A run dropped by the filter won't
    #    appear in adjacent-run comparisons for that metric, so noise
    #    deltas that used to dominate this section get skipped.

    config_change_impacts = []
    for seq in sequences:
        sid = seq["run_sequence_id"]
        seq_runs = sorted(
            [r for r in runs if r["run_sequence_id"] == sid],
            key=lambda r: r["run_number"],
        )
        # Build per-metric "kept" sets so we can skip over filtered runs.
        kept_ids_by_metric = {
            vm: {id(r) for r in filtered_by_metric.get(vm, [])
                 if r["run_sequence_id"] == sid}
            for vm in VELOCITY_METRICS
        }

        for i in range(1, len(seq_runs)):
            prev_run = seq_runs[i - 1]
            curr_run = seq_runs[i]
            prev_cfg = prev_run["config"]
            curr_cfg = curr_run["config"]
            changed_params = {
                k: {"prev": prev_cfg.get(k), "curr": curr_cfg.get(k)}
                for k in CONFIG_PARAMS
                if prev_cfg.get(k) != curr_cfg.get(k)
                and prev_cfg.get(k) is not None
                and curr_cfg.get(k) is not None
            }
            if not changed_params:
                continue

            prev_vels = prev_run["velocities"]
            curr_vels = curr_run["velocities"]
            vel_deltas = {}
            for vm in VELOCITY_METRICS:
                if vm not in prev_vels or vm not in curr_vels:
                    continue
                # Skip the delta if either end was filtered out for this metric.
                if (id(prev_run) not in kept_ids_by_metric[vm]
                        or id(curr_run) not in kept_ids_by_metric[vm]):
                    continue
                vel_deltas[vm] = round(curr_vels[vm] - prev_vels[vm], 4)

            if vel_deltas:
                config_change_impacts.append({
                    "sequence": sid[:8],
                    "from_run": prev_run["run_number"],
                    "to_run": curr_run["run_number"],
                    "param_changes": changed_params,
                    "velocity_deltas": vel_deltas,
                })

    # ── Assemble output ──────────────────────────────────────────────────

    return {
        "dataset_summary": {
            "sequences_analyzed": len(sequences),
            "total_runs": len(runs),
            "primary_velocity_metric": primary_metric,
        },
        "sequence_summaries": seq_summaries,
        "outlier_filter_summary": outlier_summary,
        "correlations": correlations,
        "feature_importance": importance,
        "top_quartile_profiles": top_quartile_profiles,
        "inflection_points": inflection_points,
        "config_change_impacts": config_change_impacts,
        "param_ranges": param_ranges,
        "best_run": {
            "config": best_run["config"] if best_run else {},
            "velocities": best_run["velocities"] if best_run else {},
            "run_number": best_run["run_number"] if best_run else None,
            "sequence": best_run["run_sequence_id"][:8] if best_run else None,
        },
        "best_top5_median": best_top5_median,
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
