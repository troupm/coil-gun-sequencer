You are analyzing coil-gun velocity test data to recommend optimal configuration settings and design the next field test iteration.

## Step 1 — Extract & Analyze Data

Run the analysis script from the repository root:

```
python tools/analyze_velocity.py --limit 5
```

This queries the SQLite database (`data/sequencer.db`) and outputs JSON containing:
- **sequence_summaries**: per-sequence velocity statistics (min/max/avg/stdev)
- **feature_importance**: config parameters ranked by their correlation strength with velocity
- **correlations**: Pearson r between each config param and each velocity metric
- **config_change_impacts**: before/after velocity deltas when config changed between runs
- **param_ranges**: the range of values tested for each parameter
- **best_run**: the config from the single highest-performing run

If the script reports an error (no data, DB not found), tell the user and stop.

## Step 2 — Interpret Results

Analyze the JSON output and reason about:

1. **Feature Importance**: Which config parameters have the strongest influence on velocity? Are correlations positive or negative? Does the physics make sense (e.g., longer pulse durations might help up to a point then hurt via back-EMF)?

2. **Config Change Impacts**: When specific parameters were changed between runs, what happened to velocity? Look for consistent patterns — did increasing a delay always help, or is there a sweet spot?

3. **Best Configuration Found**: What config produced the highest velocities? How does it compare to the defaults and to the ranges tested?

4. **Under-explored Parameters**: Which parameters have only been tested at 1-2 values? These are opportunities for the next field test.

5. **Sequence Trend**: Are average velocities improving across sequences (suggesting the tuning process is working), plateauing, or regressing?

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
