/**
 * Touchscreen page – Arm/Fire controls and live statistics.
 */

(function() {
  const btnArm   = document.getElementById('btn-arm');
  const btnFire  = document.getElementById('btn-fire');
  const btnSave  = document.getElementById('btn-save');
  const btnClear = document.getElementById('btn-clear');
  const stateInd = document.getElementById('state-indicator');
  const stateLbl = document.getElementById('state-label');

  // ── Button handlers ──────────────────────────────────────────────────
  // Disable immediately on click to prevent double-fire before the
  // state_update round-trip re-enables/disables based on actual state.

  btnArm.addEventListener('click', () => {
    btnArm.disabled = true;
    apiPost('/arm');
  });
  btnFire.addEventListener('click', () => {
    btnFire.disabled = true;
    apiPost('/fire');
  });
  btnSave.addEventListener('click', () => {
    btnSave.disabled = true;
    apiPost('/save');
  });
  btnClear.addEventListener('click', () => {
    btnClear.disabled = true;
    apiPost('/clear');
  });

  // ── State updates via SocketIO ───────────────────────────────────────

  onStateUpdate(function(s) {
    const state = s.state;

    // State indicator
    stateInd.className = 'state-indicator ' + state;
    stateLbl.textContent = state.toUpperCase();

    // Button enable/disable (authoritative, from server state)
    btnArm.disabled   = state !== 'ready';
    btnFire.disabled  = state !== 'armed';
    btnSave.disabled  = (state !== 'firing' && state !== 'complete' && state !== 'armed');
    btnClear.disabled = (state === 'ready');

    // Stats
    const st = s.stats || {};
    _setText('g1-transit', fmtUs(st.gate_1_transit_us));
    _setText('g2-transit', fmtUs(st.gate_2_transit_us));
    _setText('g3-transit', fmtUs(st.gate_3_transit_us));
    _setText('g12-flight', fmtUs(st.gate_1_to_gate_2_flight_us));
    _setText('g23-flight', fmtUs(st.gate_2_to_gate_3_flight_us));

    _setText('g1-tvel', fmtVel(st.gate_1_transit_velocity_ms));
    _setText('g2-tvel', fmtVel(st.gate_2_transit_velocity_ms));
    _setText('g3-tvel', fmtVel(st.gate_3_transit_velocity_ms));
    _setText('g12-fvel', fmtVel(st.gate_1_to_gate_2_velocity_ms));
    _setText('g23-fvel', fmtVel(st.gate_2_to_gate_3_velocity_ms));

    // Run info
    const seqShort = s.run_sequence_id ? s.run_sequence_id.substring(0, 8) : '--';
    _setText('run-info-text', 'Sequence: ' + seqShort + ' | Run: ' + (s.run_number || '--'));
  });

  function _setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
  }
})();
