# Velocity Optimization History — Baseline 0

Persistent log of `/optimize-coil-gun-velocity` skill invocations tracking
overall tuning progress.

**Reset 2026-04-29:** prior history archived to
`./archive/velocity_optimization_history_pre-baseline-0.md` and prior
per-session reports to `./archive/analysis_*.md`. The pre-reset history
spans 2026-04-11 → 2026-04-29 across multiple coil swaps, regime changes
(pull → push-push → push-push-push), gate-polarity corrections, and a
suspected projectile-length data-entry anomaly in the final session.
Treat any analysis below this line as starting from a clean slate — do
not anchor recommendations on archived runs without re-reading the
archive intentionally.

| Date | Sequences | Runs | Best Muzzle v | Key Finding | Trend |
|------|-----------|------|---------------|-------------|-------|
| 2026-05-02 | 5 (76 runs) | G1→G2 5.72 m/s (`5d80dc4a`); G2→G3 7.01 m/s (`8d125567`) | Both flight-metric top-quartiles say *shorter* gate→coil delays win (G1→C2 ~6100 vs 7600; G2→C3 ~4550 vs 6000) — high confidence, data + A/B agree. All coils massively over-driven (5–18τ) with operator-current specs; pulse durations not yet tested in the 1–3τ window. Primary metric `gate_2_transit_velocity_ms` contaminated by yardstick sequence; negative-transit issue under field investigation. | Baseline 0 first entry |
