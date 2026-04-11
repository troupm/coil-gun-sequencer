# Velocity Optimization History

Persistent log of `/optimize-coil-gun-velocity` skill invocations tracking overall tuning progress.

| Date | Sequences | Runs | Best Muzzle v | Key Finding | Trend |
|------|-----------|------|---------------|-------------|-------|
| 2026-04-11 | 1 | 5 | 6.877 m/s | G1→C2 delay and Coil 1 pulse strongly correlated with G1→G2 flight velocity; config changes so far degraded muzzle velocity vs baseline. Priority: isolate parameter effects. | Baseline |
| 2026-04-11 | 1 | 7 | 6.877 m/s (G2→G3) / 7.029 m/s (G1→G2) | Muzzle v and mid-barrel v optimized by **opposite** configs (shorter pulses help G1→G2 but hurt muzzle). Isolated C2 pulse changes had small, noisy effects — delay/C1 pulse are the dominant levers. Stage 2→3 timing completely untested. | Plateauing (muzzle best unchanged, but key trade-off identified) |
