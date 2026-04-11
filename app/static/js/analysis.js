/**
 * Analysis page – sequence filter, summary cards, trend charts, log table.
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

  // Summary card IDs mapped to velocity keys
  const SUMMARY_MAP = {
    'gate_1_transit_velocity_ms':      { min: 'sum-g1t-min',  avg: 'sum-g1t-avg',  max: 'sum-g1t-max'  },
    'gate_2_transit_velocity_ms':      { min: 'sum-g2t-min',  avg: 'sum-g2t-avg',  max: 'sum-g2t-max'  },
    'gate_3_transit_velocity_ms':      { min: 'sum-g3t-min',  avg: 'sum-g3t-avg',  max: 'sum-g3t-max'  },
    'gate_1_to_gate_2_velocity_ms':    { min: 'sum-g12f-min', avg: 'sum-g12f-avg', max: 'sum-g12f-max' },
    'gate_2_to_gate_3_velocity_ms':    { min: 'sum-g23f-min', avg: 'sum-g23f-avg', max: 'sum-g23f-max' },
  };

  // Chart colours per velocity series
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

  // ── DOM refs ───────────────────────────────────────────────────────────

  const seqSelect = document.getElementById('seq-select');
  const seqInfo   = document.getElementById('seq-info');
  const tbody     = document.getElementById('runs-tbody');
  const emptyMsg  = document.getElementById('empty-msg');

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

  // ── Load sequence list ─────────────────────────────────────────────────

  async function loadSequences() {
    const data = await apiGet('/sequences');
    seqSelect.innerHTML = '<option value="">-- select sequence --</option>';
    data.forEach(s => {
      const opt = document.createElement('option');
      opt.value = s.run_sequence_id;
      const dateStr = s.last_run ? new Date(s.last_run).toLocaleString() : '';
      opt.textContent = s.run_sequence_id.substring(0, 8) +
        ' (' + s.run_count + ' runs, ' + dateStr + ')';
      seqSelect.appendChild(opt);
    });
    // Auto-select first sequence if available
    if (data.length > 0) {
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

  // ── Table ──────────────────────────────────────────────────────────────

  function updateTable(runs) {
    tbody.innerHTML = '';
    // runs are already sorted DESC from the API
    runs.forEach(r => {
      const tr = document.createElement('tr');
      tr.innerHTML =
        '<td class="col-run">' + r.run_number + '</td>' +
        '<td class="col-time">' + fmtTime(r.created_at) + '</td>' +
        velCell(r, 'gate_1_transit_velocity_ms') +
        velCell(r, 'gate_2_transit_velocity_ms') +
        velCell(r, 'gate_3_transit_velocity_ms') +
        velCell(r, 'gate_1_to_gate_2_velocity_ms') +
        velCell(r, 'gate_2_to_gate_3_velocity_ms');
      tbody.appendChild(tr);
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

  function updateRunChart(runs) {
    // Runs come DESC from API — reverse to chronological for charting
    const chronological = [...runs].reverse();
    const labels = chronological.map(r => '#' + r.run_number);

    const datasets = VEL_KEYS.map(vk => ({
      label: SERIES_LABELS[vk],
      data: chronological.map(r => r[vk] != null ? r[vk] : null),
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
    for (const ids of Object.values(SUMMARY_MAP)) {
      document.getElementById(ids.min).textContent = '--';
      document.getElementById(ids.avg).textContent = '--';
      document.getElementById(ids.max).textContent = '--';
    }
    if (runChart) { runChart.destroy(); runChart = null; }
  }
})();
