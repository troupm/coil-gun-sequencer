# Velocity Optimization History

Persistent log of `/optimize-coil-gun-velocity` skill invocations tracking overall tuning progress.

| Date | Sequences | Runs | Best Muzzle v | Key Finding | Trend |
|------|-----------|------|---------------|-------------|-------|
| 2026-04-11 | 1 | 5 | 6.877 m/s | G1→C2 delay and Coil 1 pulse strongly correlated with G1→G2 flight velocity; config changes so far degraded muzzle velocity vs baseline. Priority: isolate parameter effects. | Baseline |
| 2026-04-11 | 1 | 7 | 6.877 m/s (G2→G3) / 7.029 m/s (G1→G2) | Muzzle v and mid-barrel v optimized by **opposite** configs (shorter pulses help G1→G2 but hurt muzzle). Isolated C2 pulse changes had small, noisy effects — delay/C1 pulse are the dominant levers. Stage 2→3 timing completely untested. | Plateauing (muzzle best unchanged, but key trade-off identified) |
| 2026-04-12 | 5 | 56 | 6.097 m/s (G1→G2 only; coil 3 unattached) | Active sequence has 35 runs, all A/B point-comparisons dominated by noise (CV 33–41%, stdev 1.25 m/s on mean 3.84). `gate_1_coil_2_delay_us` sweet spot confirmed near 100 µs; every `coil_1_pulse` A/B says shorter is better but hasn't been tested below 1090 µs. **Priority 1: establish repeatability (≥3 runs/config) before further tuning.** | Regressing (G1→G2 peak 7.029 → 6.097 m/s vs previous skill run, within noise envelope) |
