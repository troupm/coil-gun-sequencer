/**
 * Analysis page – sequence filter, summary cards, trend charts, log table,
 * config delta display, and SocketIO live updates.
 */

(function () {
  // ── Constants ──────────────────────────────────────────────────────────

  const VEL_KEYS = [
    'gate_1_transit_velocity_ms',
    'gate_2_transit_velocity_ms',
    'gate_3_transit_velocity_ms',
    'gate_1_to_gate_2_velocity_ms',
    'gate_2_to_gate_3_velocity_ms',
  ];

  const SUMMARY_MAP = {
    'gate_1_transit_velocity_ms':      { min: 'sum-g1t-min',  avg: 'sum-g1t-avg',  max: 'sum-g1t-max'  },
    'gate_2_transit_velocity_ms':      { min: 'sum-g2t-min',  avg: 'sum-g2t-avg',  max: 'sum-g2t-max'  },
    'gate_3_transit_velocity_ms':      { min: 'sum-g3t-min',  avg: 'sum-g3t-avg',  max: 'sum-g3t-max'  },
    'gate_1_to_gate_2_velocity_ms':    { min: 'sum-g12f-min', avg: 'sum-g12f-avg', max: 'sum-g12f-max' },
    'gate_2_to_gate_3_velocity_ms':    { min: 'sum-g23f-min', avg: 'sum-g23f-avg', max: 'sum-g23f-max' },
  };

  const SERIES_COLORS = {
    'gate_1_transit_velocity_ms':   '#ff9100',
    'gate_2_transit_velocity_ms':   '#ffd600',
    'gate_3_transit_velocity_ms':   '#b388ff',
    'gate_1_to_gate_2_velocity_ms': '#00e5ff',
    'gate_2_to_gate_3_velocity_ms': '#00c853',
  };
  const SERIES_LABELS = {
    'gate_1_transit_velocity_ms':   'G1 Transit',
    'gate_2_transit_velocity_ms':   'G2 Transit',
    'gate_3_transit_velocity_ms':   'G3 Transit',
    'gate_1_to_gate_2_velocity_ms': 'G1\u2192G2 Flight',
    'gate_2_to_gate_3_velocity_ms': 'Muzzle (G2\u2192G3)',
  };

  const TREND_HTML = {
    improving: '<span class="trend-up" title="Improving">\u25B2</span>',
    declining: '<span class="trend-down" title="Declining">\u25BC</span>',
    level:     '<span class="trend-flat" title="Level">\u25C6</span>',
  };

  // Human-readable parameter labels for config deltas
  const PARAM_LABELS = {
    'projectile_length_mm':        'Proj Length',
    'projectile_mass_grams':       'Proj Mass',
    'v_coil_floor':                'V Floor',
    'v_coil_ceiling':              'V Ceiling',
    'gate_1_coil_2_delay_us':      'G1\u2192C2 Delay',
    'gate_2_coil_3_delay_us':      'G2\u2192C3 Delay',
    'coil_1_pulse_duration_us':    'Coil 1 Pulse',
    'coil_2_pulse_duration_us':    'Coil 2 Pulse',
    'coil_3_pulse_duration_us':    'Coil 3 Pulse',
    'gate_1_to_gate_2_distance_mm':'G1\u2192G2 Dist',
    'gate_2_to_gate_3_distance_mm':'G2\u2192G3 Dist',
    'capacitor_bank_size_uf':      'Cap Bank',
    'rail_source_active':          'Rail Src',
    'coil_1_capacitor_uf':         'C1 Cap',
    'coil_2_capacitor_uf':         'C2 Cap',
    'coil_3_capacitor_uf':         'C3 Cap',
    'projectile_start_offset_mm':  'Start Offset',
  };

  // ── DOM refs ───────────────────────────────────────────────────────────

  const seqSelect  = document.getElementById('seq-select');
  const seqInfo    = document.getElementById('seq-info');
  const tbody      = document.getElementById('runs-tbody');
  const emptyMsg   = document.getElementById('empty-msg');
  const cfgPanel   = document.getElementById('cfg-changes');
  const cfgTimeline= document.getElementById('cfg-timeline');

  // ── Chart instances ────────────────────────────────────────────────────

  let runChart = null;
  let seqChart = null;

  // ── Init ───────────────────────────────────────────────────────────────

  loadSequences();
  loadOverviewChart();

  seqSelect.addEventListener('change', () => {
    const id = seqSelect.value;
    if (id) loadSequenceData(id);
    else clearView();
  });

  // ── SocketIO live updates ──────────────────────────────────────────────

  onEvent('run_saved', (data) => {
    // Refresh the run list if we're viewing the same sequence
    const sel = seqSelect.value;
    if (sel && sel === data.run_sequence_id) {
      loadSequenceData(sel);
    }
    // Always refresh the sequence dropdown and overview chart
    loadSequences(sel);
    loadOverviewChart();
  });

  onEvent('config_updated', () => {
    // Config change may affect velocity calculations — refresh
    const sel = seqSelect.value;
    if (sel) loadSequenceData(sel);
  });

  onEvent('sequence_changed', () => {
    const sel = seqSelect.value;
    loadSequences(sel);
    loadOverviewChart();
  });

  // ── Load sequence list ─────────────────────────────────────────────────

  async function loadSequences(preserveSelection) {
    const data = await apiGet('/sequences');
    const prevVal = preserveSelection || seqSelect.value;
    seqSelect.innerHTML = '<option value="">-- select sequence --</option>';
    data.forEach(s => {
      const opt = document.createElement('option');
      opt.value = s.run_sequence_id;
      const dateStr = s.last_run ? new Date(s.last_run).toLocaleString() : '';
      opt.textContent = s.run_sequence_id.substring(0, 8) +
        ' (' + s.run_count + ' runs, ' + dateStr + ')';
      seqSelect.appendChild(opt);
    });
    // Restore previous selection, or auto-select first
    if (prevVal && [...seqSelect.options].some(o => o.value === prevVal)) {
      seqSelect.value = prevVal;
    } else if (data.length > 0) {
      seqSelect.value = data[0].run_sequence_id;
      loadSequenceData(data[0].run_sequence_id);
    }
  }

  // ── Load runs for a sequence ───────────────────────────────────────────

  async function loadSequenceData(seqId) {
    const data = await apiGet('/analysis/runs?sequence_id=' + encodeURIComponent(seqId));
    const { summary, runs } = data;

    seqInfo.textContent = runs.length + ' runs';
    emptyMsg.style.display = runs.length ? 'none' : 'block';

    updateSummaryCards(summary);
    updateTable(runs);
    updateRunChart(runs);
    updateConfigTimeline(runs);
  }

  // ── Summary cards ──────────────────────────────────────────────────────

  function updateSummaryCards(summary) {
    for (const [vk, ids] of Object.entries(SUMMARY_MAP)) {
      const s = summary[vk];
      document.getElementById(ids.min).textContent = s ? s.min.toFixed(2) : '--';
      document.getElementById(ids.avg).textContent = s ? s.avg.toFixed(2) : '--';
      document.getElementById(ids.max).textContent = s ? s.max.toFixed(2) : '--';
    }
  }

  // ── Config changes timeline ────────────────────────────────────────────

  function updateConfigTimeline(runs) {
    // runs are DESC — reverse to chronological for display
    const chrono = [...runs].reverse();
    const changes = chrono.filter(r => r.config_deltas && Object.keys(r.config_deltas).length > 0);

    if (!changes.length) {
      cfgPanel.style.display = 'none';
      return;
    }
    cfgPanel.style.display = '';
    cfgTimeline.innerHTML = '';

    changes.forEach(r => {
      const el = document.createElement('div');
      el.className = 'cfg-change-entry';
      const deltaStrs = Object.entries(r.config_deltas).map(([key, d]) => {
        const label = PARAM_LABELS[key] || key;
        return '<span class="cfg-delta-item">' + label +
          ': <span class="cfg-old">' + d.prev + '</span> \u2192 ' +
          '<span class="cfg-new">' + d.curr + '</span></span>';
      });
      el.innerHTML = '<span class="cfg-run-badge">Run #' + r.run_number + '</span> ' +
        deltaStrs.join(' ');
      cfgTimeline.appendChild(el);
    });
  }

  // ── Table ──────────────────────────────────────────────────────────────

  function updateTable(runs) {
    tbody.innerHTML = '';
    // runs are already sorted DESC from the API
    runs.forEach(r => {
      const hasDelta = r.config_deltas && Object.keys(r.config_deltas).length > 0;

      // Main data row
      const tr = document.createElement('tr');
      if (hasDelta) tr.classList.add('has-delta');
      tr.innerHTML =
        '<td class="col-run">' + r.run_number +
          (hasDelta ? ' <span class="cfg-icon" title="Config changed">\u2699</span>' : '') +
        '</td>' +
        '<td class="col-time">' + fmtTime(r.created_at) + '</td>' +
        velCell(r, 'gate_1_transit_velocity_ms') +
        velCell(r, 'gate_2_transit_velocity_ms') +
        velCell(r, 'gate_3_transit_velocity_ms') +
        velCell(r, 'gate_1_to_gate_2_velocity_ms') +
        velCell(r, 'gate_2_to_gate_3_velocity_ms');
      tbody.appendChild(tr);

      // Expandable config delta row
      if (hasDelta) {
        const deltaTr = document.createElement('tr');
        deltaTr.className = 'delta-row';
        deltaTr.style.display = 'none';
        const deltaStrs = Object.entries(r.config_deltas).map(([key, d]) => {
          const label = PARAM_LABELS[key] || key;
          return label + ': ' + d.prev + ' \u2192 ' + d.curr;
        });
        deltaTr.innerHTML = '<td colspan="7" class="delta-cell">' +
          deltaStrs.join(' &nbsp;\u2502&nbsp; ') + '</td>';
        tbody.appendChild(deltaTr);

        // Toggle on click
        tr.style.cursor = 'pointer';
        tr.addEventListener('click', () => {
          deltaTr.style.display = deltaTr.style.display === 'none' ? '' : 'none';
        });
      }
    });
  }

  function velCell(run, key) {
    const v = run[key];
    const t = run.trends ? run.trends[key] : null;
    const valStr = v != null ? v.toFixed(2) : '--';
    const trendStr = t && TREND_HTML[t] ? ' ' + TREND_HTML[t] : '';
    const cls = v != null ? '' : ' class="dim"';
    return '<td' + cls + '>' + valStr + trendStr + '</td>';
  }

  function fmtTime(iso) {
    if (!iso) return '--';
    const d = new Date(iso);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }

  // ── Run-over-run chart ─────────────────────────────────────────────────

  // Outlier detection for the trend chart. Two rules, applied in order:
  //   (a) Intra-run absurdity: a velocity in a run is flagged if it is
  //       > 3x EVERY other velocity in the same run (i.e. dominates all
  //       peers). Catches calibration / yardstick rows where one gate's
  //       reading is orders of magnitude off (e.g. G3 transit reading
  //       1635 m/s when every other reading is < 60 m/s). Deliberately
  //       does NOT flag the systematic ~4x gap between gate-transit and
  //       gate-to-gate-flight velocities, which is a known measurement-
  //       method artefact, not bad data.
  //   (b) Per-series 3-sigma fence (only when n>5 valid values remain after
  //       rule (a)): drops single-shot anomalies that aren't intra-run
  //       inconsistent but are far from the sequence's typical velocity for
  //       that series.
  // Rule (a) runs first so its hits don't pollute the mean/sigma of rule (b).
  function detectChartOutliers(runs, velKeys) {
    const flagged = new Set();  // 'runId:vk' tuples
    const key = (r, vk) => r.id + ':' + vk;

    // (a) intra-run dominance check
    for (const r of runs) {
      const vals = velKeys
        .map(vk => ({ vk, v: r[vk] }))
        .filter(x => x.v != null && x.v > 0);
      if (vals.length < 2) continue;
      for (const a of vals) {
        const peers = vals.filter(p => p.vk !== a.vk);
        if (peers.length === 0) continue;
        const peerMax = Math.max(...peers.map(p => p.v));
        if (a.v > 3 * peerMax) flagged.add(key(r, a.vk));
      }
    }

    // (b) per-series 3-sigma, post (a)
    for (const vk of velKeys) {
      const vals = runs
        .filter(r => !flagged.has(key(r, vk)))
        .map(r => r[vk])
        .filter(v => v != null);
      if (vals.length <= 5) continue;
      const mean = vals.reduce((s, v) => s + v, 0) / vals.length;
      const variance = vals.reduce((s, v) => s + (v - mean) ** 2, 0) / vals.length;
      const sigma = Math.sqrt(variance);
      if (sigma === 0) continue;
      for (const r of runs) {
        const v = r[vk];
        if (v != null && Math.abs(v - mean) > 3 * sigma) {
          flagged.add(key(r, vk));
        }
      }
    }
    return flagged;
  }

  function updateRunChart(runs) {
    const chronological = [...runs].reverse();
    const labels = chronological.map(r => '#' + r.run_number);
    const outliers = detectChartOutliers(chronological, VEL_KEYS);

    const datasets = VEL_KEYS.map(vk => ({
      label: SERIES_LABELS[vk],
      data: chronological.map(r => {
        const v = r[vk];
        if (v == null) return null;
        if (outliers.has(r.id + ':' + vk)) return null;
        return v;
      }),
      borderColor: SERIES_COLORS[vk],
      backgroundColor: SERIES_COLORS[vk] + '33',
      borderWidth: 2,
      pointRadius: 3,
      tension: 0.25,
      spanGaps: true,
    }));

    if (runChart) runChart.destroy();
    runChart = new Chart(document.getElementById('chart-runs'), {
      type: 'line',
      data: { labels, datasets },
      options: chartOpts('Run #', 'Velocity (m/s)'),
    });
  }

  // ── Sequence-over-sequence chart ───────────────────────────────────────

  async function loadOverviewChart() {
    const data = await apiGet('/analysis/overview');
    if (!data.length) return;

    const labels = data.map(s => {
      const short = s.run_sequence_id.substring(0, 6);
      const date = s.first_run ? new Date(s.first_run).toLocaleDateString() : '';
      return short + '\n' + date;
    });

    const avgKeys = VEL_KEYS.map(k => 'avg_' + k);
    const datasets = VEL_KEYS.map((vk, i) => ({
      label: SERIES_LABELS[vk],
      data: data.map(s => s[avgKeys[i]]),
      borderColor: SERIES_COLORS[vk],
      backgroundColor: SERIES_COLORS[vk] + '55',
      borderWidth: 2,
      pointRadius: 4,
      tension: 0.25,
      spanGaps: true,
    }));

    if (seqChart) seqChart.destroy();
    seqChart = new Chart(document.getElementById('chart-sequences'), {
      type: 'line',
      data: { labels, datasets },
      options: chartOpts('Sequence', 'Avg Velocity (m/s)'),
    });
  }

  // ── Shared chart options ───────────────────────────────────────────────

  function chartOpts(xLabel, yLabel) {
    return {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: {
          position: 'bottom',
          labels: { color: '#e0e0e0', boxWidth: 14, font: { size: 11 } },
        },
        tooltip: {
          callbacks: {
            label: ctx => ctx.dataset.label + ': ' +
              (ctx.parsed.y != null ? ctx.parsed.y.toFixed(2) + ' m/s' : '--'),
          },
        },
      },
      scales: {
        x: {
          title: { display: true, text: xLabel, color: '#8888aa' },
          ticks: { color: '#8888aa', font: { size: 10 } },
          grid: { color: 'rgba(255,255,255,0.05)' },
        },
        y: {
          title: { display: true, text: yLabel, color: '#8888aa' },
          ticks: { color: '#8888aa' },
          grid: { color: 'rgba(255,255,255,0.08)' },
          beginAtZero: false,
        },
      },
    };
  }

  // ── Clear ──────────────────────────────────────────────────────────────

  function clearView() {
    tbody.innerHTML = '';
    emptyMsg.style.display = 'block';
    seqInfo.textContent = '';
    cfgPanel.style.display = 'none';
    for (const ids of Object.values(SUMMARY_MAP)) {
      document.getElementById(ids.min).textContent = '--';
      document.getElementById(ids.avg).textContent = '--';
      document.getElementById(ids.max).textContent = '--';
    }
    if (runChart) { runChart.destroy(); runChart = null; }
  }
})();
