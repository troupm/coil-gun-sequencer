/**
 * Configuration page – parameter editing, sequence management.
 */

(function() {
  const PARAM_KEYS = [
    'projectile_length_mm',
    'projectile_mass_grams',
    'v_coil_floor',
    'v_coil_ceiling',
    'gate_1_coil_2_delay_us',
    'gate_2_coil_3_delay_us',
    'coil_1_pulse_duration_us',
    'coil_2_pulse_duration_us',
    'coil_3_pulse_duration_us',
    'gate_1_to_gate_2_distance_mm',
    'gate_2_to_gate_3_distance_mm',
  ];

  const statusEl = document.getElementById('cfg-status-text');
  let _debounceTimer = null;
  let _suppressSync = false;  // Prevent SSE from overwriting user edits mid-typing

  // ── Populate fields from SSE state ───────────────────────────────────

  onStateUpdate(function(s) {
    if (_suppressSync) return;
    const cfg = s.config || {};
    PARAM_KEYS.forEach(key => {
      const el = document.getElementById(key);
      if (el && document.activeElement !== el) {
        el.value = cfg[key] != null ? cfg[key] : '';
      }
    });
    const seqShort = s.run_sequence_id ? s.run_sequence_id.substring(0, 8) : '--';
    statusEl.textContent = 'Sequence: ' + seqShort +
      ' | Run: ' + (s.run_number || '--') +
      ' | State: ' + (s.state || '--').toUpperCase();
  });

  // ── Auto-save on input change (with short debounce) ──────────────────

  PARAM_KEYS.forEach(key => {
    const el = document.getElementById(key);
    if (!el) return;

    el.addEventListener('focus', () => { _suppressSync = true; });
    el.addEventListener('blur', () => {
      _suppressSync = false;
      _saveConfig();
    });
    el.addEventListener('input', () => {
      clearTimeout(_debounceTimer);
      _debounceTimer = setTimeout(_saveConfig, 600);
    });
  });

  async function _saveConfig() {
    const payload = {};
    PARAM_KEYS.forEach(key => {
      const el = document.getElementById(key);
      if (el && el.value !== '') {
        payload[key] = parseFloat(el.value);
      }
    });
    try {
      const res = await apiPost('/config', payload);
      if (res.status === 'updated') {
        statusEl.textContent = 'Config saved (snapshot #' + res.snapshot_id + ')';
      }
    } catch (e) {
      statusEl.textContent = 'Save error: ' + e.message;
    }
  }

  // ── Action buttons ───────────────────────────────────────────────────

  document.getElementById('btn-cfg-save').addEventListener('click', async () => {
    const res = await apiPost('/save');
    statusEl.textContent = res.status === 'saved'
      ? 'Run saved (log #' + res.event_log_id + ')'
      : 'Nothing to save';
  });

  document.getElementById('btn-cfg-clear').addEventListener('click', async () => {
    await apiPost('/clear');
    statusEl.textContent = 'Run cleared';
  });

  document.getElementById('btn-new-seq').addEventListener('click', async () => {
    const res = await apiPost('/sequence/new');
    statusEl.textContent = 'New sequence: ' + res.run_sequence_id.substring(0, 8);
  });
})();
