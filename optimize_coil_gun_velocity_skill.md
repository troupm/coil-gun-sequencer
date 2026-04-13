## `/optimize-coil-gun-velocity` skill
### Motivation
The `coil_gun_sequencer` app has logs of Configuration Settings and run-over-run Velocity Performance segmented by `sequence` cohorts. Using that data, I want Claude to recommend optimal configuration settings for to achieve maximum velocity, and identify the most impactful configuration parameters wrt Velocity (eg, Feature Importance)

### Requirements
- Implement a Claude Skill `/optimize-coil-gun-velocity` that analyzes  `coil_gun_sequencer` logs with the goal of recommending the optimal configuration settings to increase velocity, and provide specs for new field testing to further refine the dataset & find the "sweet spot"
- This skill will look at the most recent five (5) sequences only when performing analysis
- This skill will persist results & advice to the `./optimize_coil_gun_velocity_skill_results/` (create if missing) as a markdown file, using the current timestamp in the name for tracking purposes
- Nice to Have: Add a persistent `velocity_optimization_history.md` file to `./optimize_coil_gun_velocity_skill_results/`, and update/append a summary of  `/optimize_coil_gun_velocity`
skill results to this file each time it is used, toward the goal of painting the "big picture" of overall testing & optimization progress
- **Inflection points narrative**: each skill run must produce an "intuitive feature importance" section in the report — a monospace bar chart of velocity vs run number for the most active sequence, annotated with local peaks/troughs (d/dx zeros of the smoothed series) and high/low water marks, followed by a short event table showing the config parameter(s) that changed at each inflection with a one-sentence causal read. This narrative should be cross-checked against the quantitative top-quartile profile; contradictions should be flagged as next-test candidates rather than findings. The analyzer (`tools/analyze_velocity.py`) pre-computes the inflection events and timeline; the skill prompt handles the rendering.


