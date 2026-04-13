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

## Step 2 — Interpret Results

Analyze the JSON output and reason about, **in this priority order**:

1. **Top-Quartile Profile** (anchor your analysis here): For the primary velocity metric, which parameters have the largest |t-stat| between the fastest 25% of runs and the rest? Strong positive *t* means the fast runs tend to use higher values of that parameter; strong negative *t* means the fast runs prefer lower values. This is much more trustworthy than Pearson on a high-CV rig. Cross-check against the physics: back-EMF, projectile-entry timing, capacitor recharge, etc.

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

### Next Field Test Plan
Specific parameter changes to try in the next testing session, designed to:
- Explore under-tested parameters
- Refine parameters that show strong correlation with velocity
- Test beyond the current best values to see if there's more headroom
- Include at least 3-5 suggested runs with specific config values

### Confidence Assessment
For each recommendation, state your confidence (High/Medium/Low) and why. Flag any recommendations that are speculative vs. data-backed.

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
