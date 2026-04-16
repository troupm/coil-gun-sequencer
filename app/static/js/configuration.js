/**
 * Configuration page – parameter editing, sequence management.
 */

(function() {
  // All parameters are numeric on the wire. rail_source_active is a
  // checkbox in the UI, but the payload value is computed as
  // `v_coil_ceiling` when checked and `0.0` when unchecked — giving ML
  // tools a continuous rail-voltage feature instead of a 0/1 indicator.
  const NUMERIC_INPUT_KEYS = [
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
    'capacitor_bank_size_uf',
    'coil_1_brake_resistor_ohms',
    'coil_2_brake_resistor_ohms',
    'coil_1_resistance_ohms',
    'coil_1_inductance_uh',
    'coil_2_resistance_ohms',
    'coil_2_inductance_uh',
    'coil_3_resistance_ohms',
    'coil_3_inductance_uh',
  ];
  const RAIL_KEY = 'rail_source_active';

  const statusEl = document.getElementById('cfg-status-text');
  let _debounceTimer = null;
  let _suppressSync = false;  // Prevent SSE from overwriting user edits mid-typing

  // ── Populate fields from SSE state ───────────────────────────────────

  onStateUpdate(function(s) {
    if (_suppressSync) return;
    const cfg = s.config || {};
    NUMERIC_INPUT_KEYS.forEach(key => {
      const el = document.getElementById(key);
      if (el && document.activeElement !== el) {
        el.value = cfg[key] != null ? cfg[key] : '';
      }
    });
    // Checkbox state derives from the stored float: any non-zero value
    // means the rail source was on.
    const railEl = document.getElementById(RAIL_KEY);
    if (railEl && document.activeElement !== railEl) {
      railEl.checked = Number(cfg[RAIL_KEY] || 0) > 0;
    }
    const seqShort = s.run_sequence_id ? s.run_sequence_id.substring(0, 8) : '--';
    statusEl.textContent = 'Sequence: ' + seqShort +
      ' | Run: ' + (s.run_number || '--') +
      ' | State: ' + (s.state || '--').toUpperCase();
  });

  // ── Auto-save on input change (with short debounce) ──────────────────

  NUMERIC_INPUT_KEYS.forEach(key => {
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

  // Checkbox saves immediately on change — no debounce needed.
  // Changing v_coil_ceiling while the box is checked will also
  // auto-resync rail_source_active because _saveConfig re-reads
  // the current V Ceiling on every call.
  const _railEl = document.getElementById(RAIL_KEY);
  if (_railEl) {
    _railEl.addEventListener('change', _saveConfig);
  }

  async function _saveConfig() {
    const payload = {};
    NUMERIC_INPUT_KEYS.forEach(key => {
      const el = document.getElementById(key);
      if (el && el.value !== '') {
        payload[key] = parseFloat(el.value);
      }
    });
    // rail_source_active: checked → current V Ceiling, unchecked → 0.0.
    // Re-reading V Ceiling on every save means the stored rail voltage
    // stays in sync with the ceiling even if the user edits it without
    // touching the checkbox.
    const railEl = document.getElementById(RAIL_KEY);
    if (railEl) {
      if (railEl.checked) {
        const vceil = parseFloat(document.getElementById('v_coil_ceiling').value);
        payload[RAIL_KEY] = isNaN(vceil) ? 0.0 : vceil;
      } else {
        payload[RAIL_KEY] = 0.0;
      }
    }
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
