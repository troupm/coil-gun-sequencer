You are analyzing coil-gun velocity test data to recommend optimal configuration settings and design the next field test iteration.

## Step 1 — Extract & Analyze Data

Run the analysis script from the repository root:

```
python tools/analyze_velocity.py --limit 5
```

This queries the SQLite database (`data/sequencer.db`) and outputs JSON containing:
- **dataset_summary**: sequence/run counts plus `primary_velocity_metric` (the metric with the most coverage after filtering — usually what you should anchor recommendations on)
- **sequence_summaries**: per-sequence velocity statistics (min/max/avg/stdev) on the **unfiltered** data, so the operator sees what their session actually looked like
- **outlier_filter_summary**: per-metric counts of runs dropped as low outliers (mechanical failures) and kept-but-flagged as high outliers (candidate wins) using a trailing-10 rolling Tukey-IQR fence, asymmetric. Only runs on sequences with ≥10 samples.
- **top_quartile_profiles** ⭐ **primary analysis input**: for each velocity metric, partitions the filtered runs into top-25% vs rest and reports per-parameter mean-shift and t-statistic, ranked by |t|. Effectively a one-step gradient-boosted stump per feature. This is far more noise-tolerant than Pearson r on a ~33% CV rig and should drive most of your reasoning.
- **correlations**: Pearson r between each config param and each velocity metric (on filtered data). Retained for reference but **prefer top_quartile_profiles** — linear correlation badly undersells non-monotonic relationships and gets dominated by the slow/mediocre bulk of runs.
- **feature_importance**: parameters ranked by max |Pearson r|. Same caveats as `correlations`.
- **config_change_impacts**: before/after velocity deltas when config changed between runs (skipping any run that was outlier-filtered for the relevant metric).
- **inflection_points** ⭐ **intuitive feature-importance narrative**: per sequence (sequences with ≥10 runs), a `timeline` of `{run_number, velocity, smoothed, filtered}` for every run reporting the primary metric, plus an `events` list of local extrema in the 5-point-smoothed kept series. Each event is tagged with:
  - `kind`: `"peak"` or `"trough"`
  - `is_new_high_watermark` / `is_new_low_watermark`: True when the raw velocity at that position beat/undercut every prior kept run in the sequence (a literal "new record")
  - `delta_since_prior_event`: smoothed-velocity change since the previous extremum (None on the first event)
  - `config_delta_since_prior`: the exact config parameters that differ between this extremum's run and the previous extremum's run — i.e., **what knob the operator turned between the two trend-reversal points**
  
  This is the *causal narrative* companion to the quantitative `top_quartile_profiles`: top-quartile tells you **which knobs matter**, inflection points tell you **what tweak the operator made, and what happened next**. Note: smoothing is a 5-point rolling mean on the outlier-filtered series, so a "peak" reflects local *trend* topping out, not a raw-velocity single-run max — a peak can legitimately sit at a position whose raw velocity is unremarkable if its neighbors are fast. Always cross-check the event's raw `velocity` against its `smoothed` value when narrating.
- **param_ranges**: the range of values tested for each parameter (unfiltered).
- **best_top5_median** ⭐ **primary recommendation input**: the median configuration across the top 5 runs by primary velocity. Noise-robust — an outlier run contributes 1-of-5 votes instead of determining the entire recommendation. Use this as your optimal-config starting point.
- **best_run**: the single highest-scoring run across all filtered metrics. **Treat as an anecdote, not a recommendation.** A single run on this rig is 1σ-ish from its own neighbors, so config values that only appear here are likely noise. Cross-check against `best_top5_median` and `top_quartile_profiles` before recommending anything from it.

If the script reports an error (no data, DB not found), tell the user and stop.

## Coil Electromagnetics — Physics Context

Each coil stage is characterised by two electrical ratings logged per snapshot:
**DC winding resistance** (`coil_N_resistance_ohms`, Ω) and **air-core
inductance** (`coil_N_inductance_uh`, µH). These are metadata — the firing
path doesn't read them — but they are statistically significant for velocity
because they govern the magnetic-force pulse shape.

### Key relationships

- **L/R time-constant:** `τ = L / R`. This is how fast current (and therefore
  magnetic field) ramps up after the MOSFET turns on. A coil with L = 476 µH
  and R = 1.3 Ω has τ ≈ 366 µs; one with L = 1900 µH and R = 2.8 Ω has
  τ ≈ 679 µs. Current reaches ~63% of its steady-state value after 1τ,
  ~95% after 3τ.

- **Peak current:** `I_peak = V_rail / R` (at steady state). Lower resistance
  → higher peak current → stronger field, but also longer decay tail after
  turn-off (more suck-back risk without adequate braking).

- **Pulse duration vs τ:** The `coil_N_pulse_duration_us` setting interacts
  directly with τ. If pulse ≪ τ, the coil barely magnetises before turn-off
  (wasted energy). If pulse ≫ 3τ, the field is already saturated and extra
  on-time just heats the winding and extends the suck-back window. The sweet
  spot is typically 1–3τ, but the optimal point shifts with projectile
  velocity (faster projectiles spend less time in the bore, so the field must
  ramp faster).

- **Inductance vs field strength:** Higher L produces more flux per amp
  (stronger pull per unit current) but ramps slower. There is a
  design-level trade-off: a coil wound for high L may never reach peak
  current within a short pulse.

- **Brake resistor coupling:** After turn-off, stored energy decays through
  the flyback path with time-constant `τ_decay = L / (R_winding + R_brake)`.
  Higher `R_brake` → faster decay → shorter suck-back tail, but the V_CE
  spike at the switch is `≈ V_rail + I_coil × R_brake`. The coil's L and R
  determine I_coil at turn-off, so brake-resistor analysis must consider the
  coil ratings together.

### How to use in analysis

When interpreting correlations and top-quartile profiles:

1. **Compute τ for each stage** from the logged R and L values so you can
   express pulse durations in units of τ (e.g., "coil 1 pulse = 4.1τ").
   This normalised view reveals whether a pulse is under-driving or
   over-driving the coil independent of the absolute µs value.

2. **Flag pulse/τ mismatches:** If the top-quartile profile shows fast runs
   favour a specific pulse duration AND the coil ratings are constant across
   runs, note the implied τ ratio. If coil ratings vary across runs,
   check whether the velocity gain tracks pulse_us or pulse_us/τ — the
   latter suggests the operator should tune pulse duration relative to the
   coil's time-constant, not to a fixed µs value.

3. **Cross-stage comparison:** Different stages fire at different projectile
   velocities. A later stage (higher projectile speed) may need a coil with
   a shorter τ (lower L/R) to deliver its field pulse before the projectile
   exits the bore. Note whether the installed coil ratings match this
   expectation and flag any anomalies.

4. **Recommendation context:** When recommending pulse duration changes,
   always state the current τ and the resulting pulse/τ ratio so the
   operator can judge whether the recommendation is physically reasonable.
   A recommendation to double pulse duration on a coil that's already at
   5τ is almost certainly chasing noise.

## Step 2 — Interpret Results

Analyze the JSON output and reason about, **in this priority order**:

1. **Top-Quartile Profile** (anchor your analysis here): For the primary velocity metric, which parameters have the largest |t-stat| between the fastest 25% of runs and the rest? Strong positive *t* means the fast runs tend to use higher values of that parameter; strong negative *t* means the fast runs prefer lower values. This is much more trustworthy than Pearson on a high-CV rig. Cross-check against the physics: L/R time-constant vs pulse duration (see "Coil Electromagnetics" above), back-EMF, projectile-entry timing, capacitor recharge, etc. When pulse-duration params appear in the profile, always compute the pulse/τ ratio for that stage and reason about whether the statistical signal is consistent with the electromagnetic model.

2. **Best Top-5 Median**: What config values emerged as the consensus across the top 5 runs? These should form the backbone of your "optimal config" recommendation. If `best_top5_median` disagrees with `best_run` on a parameter, **prefer the median** — the single-best run is a noise datapoint, not a signal. Call out any such disagreements explicitly.

3. **Outlier Filter Summary**: How many runs were dropped as low outliers? If a sequence has many drops, the rig may have had mechanical issues during that session (worth noting). If high outliers were flagged, those are your candidate real wins — look at what's different about their config compared to `best_top5_median`.

4. **Config Change Impacts**: A/B comparisons between adjacent runs. **Important:** A single A/B ΔV on a ~33% CV rig can easily be pure noise. Only trust a trend from these deltas if it's consistent across multiple comparisons in the same direction, OR if the top-quartile profile agrees. If a single A/B says "X helped" but the top-quartile profile says the fast runs don't use X, trust the top-quartile profile.

5. **Inflection Points Narrative**: Walk the `inflection_points.events` list for the most active sequence (usually the one with the most runs) in chronological order. At each event, read the `config_delta_since_prior` to see which knob the operator just turned, and pair it with `delta_since_prior_event` (smoothed velocity change since the last extremum). Distinguish:
   - **New high water marks** (`is_new_high_watermark: true`) — "here's what broke through the ceiling"
   - **New low water marks** (`is_new_low_watermark: true`) — "here's what broke the rig" (or caused a mechanical regression)
   - **Non-watermark peaks/troughs** — attempted improvements that didn't clear the running best, or dips the sequence recovered from. These are still informative: a streak of non-watermark peaks is a plateau.
   
   The point is to tell a *causal* story: knob X was turned, then velocity did Y. **Cross-check every causal claim against the `top_quartile_profiles`**: if an inflection story says "shorter X helped", but top-quartile says the fast runs don't prefer shorter X, the inflection story is probably noise — flag it as such. Trust narratives that agree with the statistics; flag contradictory ones as candidates for the next A/B test rather than as findings.

6. **Feature Importance / Correlations**: Pearson values. Use these only as a sanity check on the top-quartile profile, not as a primary signal. A param with high top-quartile |t| but near-zero Pearson r is still a real finding — it just means the relationship is non-monotonic (sweet spot).

7. **Under-explored Parameters**: Which parameters have only been tested at 1-2 unique values in `param_ranges`? These are opportunities for the next field test.

8. **Sequence Trend**: Are average velocities improving across sequences, plateauing, or regressing? Compare against the historical entries in `velocity_optimization_history.md` to see the longer arc.

## Step 3 — Generate Recommendations

Produce three categories of recommendations:

### Optimal Config (Best Known)
The configuration settings most likely to produce maximum velocity, based on all evidence.

### Coil Electromagnetics Summary
For each coil stage, report a table with: R (Ω), L (µH), τ = L/R (µs),
current pulse duration (µs), pulse/τ ratio, and I_peak = V_rail/R (A).
Note which stages appear under-driven (pulse < τ) or over-driven (pulse > 3τ)
and whether the data supports adjusting pulse duration toward the 1–3τ window.
If coil ratings varied across runs, note whether velocity correlated better
with raw pulse_us or with the pulse/τ ratio.

### Next Field Test Plan
Specific parameter changes to try in the next testing session, designed to:
- Explore under-tested parameters (coil ratings that have only one value in the dataset are prime candidates for a coil swap experiment)
- Refine parameters that show strong correlation with velocity
- Test beyond the current best values to see if there's more headroom
- Include at least 3-5 suggested runs with specific config values
- When suggesting pulse duration changes, state the target pulse/τ ratio and the physics rationale

### Confidence Assessment
For each recommendation, state your confidence (High/Medium/Low) and why. Flag any recommendations that are speculative vs. data-backed. Recommendations grounded in both statistical signal AND electromagnetic theory (e.g., "top-quartile profile favours shorter coil-2 pulse AND the current pulse/τ = 1.8 is in the expected sweet spot") deserve higher confidence than either signal alone.

## Step 4 — Save Results

1. Create the output directory if it doesn't exist:
   ```
   mkdir -p optimize_coil_gun_velocity_skill_results
   ```

2. Save the full analysis report as a markdown file with a timestamp in the name:
   ```
   optimize_coil_gun_velocity_skill_results/analysis_YYYY-MM-DD_HH-MM-SS.md
   ```

   The report should include:
   - **Header**: date, dataset size (sequences, runs)
   - **Dataset Overview**: sequence summaries with velocity stats
   - **Coil Electromagnetics Table**: per-stage R, L, τ, pulse duration, pulse/τ ratio, I_peak
   - **Feature Importance**: ranked table of parameters and their impact
   - **Config Change Impact Log**: what happened when specific knobs were turned
   - **Inflection Points Chart & Narrative**: render a monospace bar chart of velocity vs run number for the most active sequence (the one the analyzer produces the largest `inflection_points.timeline` for), then an annotated event table. Format guidance:
     * **Chart**: one row per run, with a horizontal bar whose width is proportional to velocity. Mark filtered runs visibly (e.g., a `✗` or `!` suffix), and annotate inflection events inline — at least `▲` for peaks, `▼` for troughs, and an extra `*` for events that set a new high/low watermark. If a sequence has more than ~40 runs, you may collapse quiet stretches ("runs 18–24: uneventful plateau near 3.4 m/s"), but every inflection event must appear as its own row.
     * **Annotated event table**: pick the 5–10 most informative events across the session (not just the active sequence — the all-time high and low watermarks matter even if they happened earlier). For each, show: run number + sequence shortcode, raw velocity, event type + watermark flag, smoothed velocity, `delta_since_prior_event`, the config parameter(s) that changed since the prior event, and **a one-sentence causal read** — what knob moved and what it did. Cross-check each causal read against `top_quartile_profiles`: if a narrative says "X helped" but top-quartile disagrees on direction, flag it as "likely noise" inline.
     * **Purpose**: this is the operator-facing "intuitive feature importance" view. It should be readable even by someone who skips the top-quartile and correlation sections, and should tell a coherent story about how the session unfolded knob-by-knob. If the narrative contradicts the quantitative analysis anywhere, call that out explicitly — it's a candidate for the next A/B test.
   - **Best Known Configuration**: table of parameter values
   - **Recommendations**: optimal config + next test plan + confidence
   - **Raw Correlation Matrix**: for reference

3. Update (or create) the persistent history file:
   ```
   optimize_coil_gun_velocity_skill_results/velocity_optimization_history.md
   ```

   This file tracks the big picture across all skill invocations. Append a new entry with:
   - Date
   - Number of sequences/runs analyzed
   - Best velocity achieved (name the metric and value)
   - Key finding or recommendation (1-2 sentences)
   - Trend indicator: are we improving, plateauing, or regressing vs. the previous entry?

   If the file doesn't exist yet, create it with a header and the first entry.

## Additional context from the user

$ARGUMENTS
