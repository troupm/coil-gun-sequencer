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

5. **Feature Importance / Correlations**: Pearson values. Use these only as a sanity check on the top-quartile profile, not as a primary signal. A param with high top-quartile |t| but near-zero Pearson r is still a real finding — it just means the relationship is non-monotonic (sweet spot).

6. **Under-explored Parameters**: Which parameters have only been tested at 1-2 unique values in `param_ranges`? These are opportunities for the next field test.

7. **Sequence Trend**: Are average velocities improving across sequences, plateauing, or regressing? Compare against the historical entries in `velocity_optimization_history.md` to see the longer arc.

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
