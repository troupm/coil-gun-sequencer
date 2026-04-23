/**
 * Manual page – direct Coil/Gate testing buttons.
 *
 * Honors the Arm/Ready lifecycle: manual actions require ARMED (they
 * transition to FIRING server-side); Save/Clear return the sequencer
 * to READY just like on the Fire Control page.
 */

(function() {
  const btnArm   = document.getElementById('btn-arm');
  const btnSave  = document.getElementById('btn-save');
  const btnClear = document.getElementById('btn-clear');
  const stateInd = document.getElementById('state-indicator');
  const stateLbl = document.getElementById('state-label');

  const coilButtons = document.querySelectorAll('.btn-manual-coil');
  const gateButtons = document.querySelectorAll('.btn-manual-gate');

  // ── Button handlers ──────────────────────────────────────────────────

  btnArm.addEventListener('click', () => {
    btnArm.disabled = true;
    apiPost('/arm');
  });
  btnSave.addEventListener('click', () => {
    btnSave.disabled = true;
    apiPost('/save');
  });
  btnClear.addEventListener('click', () => {
    btnClear.disabled = true;
    apiPost('/clear');
  });

  coilButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      const n = btn.dataset.coil;
      apiPost('/manual/coil/' + n + '/fire');
    });
  });

  gateButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      const n = btn.dataset.gate;
      // Disable just this button so a single gate can't be double-triggered
      // in the same run (the sequencer's seen_leading dedup would reject
      // repeats anyway, but this gives immediate UI feedback).
      btn.disabled = true;
      apiPost('/manual/gate/' + n + '/trigger');
    });
  });

  // ── State updates via SocketIO ───────────────────────────────────────

  onStateUpdate(function(s) {
    const state = s.state;

    stateInd.className = 'state-indicator ' + state;
    stateLbl.textContent = state.toUpperCase();

    // Lifecycle buttons — mirror Fire Control semantics
    btnArm.disabled   = state !== 'ready';
    btnSave.disabled  = (state !== 'firing' && state !== 'complete' && state !== 'armed');
    btnClear.disabled = (state === 'ready');

    // Manual action buttons enabled whenever a run is active
    const manualEnabled = (state === 'armed' || state === 'firing' || state === 'complete');
    coilButtons.forEach(b => { b.disabled = !manualEnabled; });

    // Gate trigger buttons: additionally disable gates that already fired
    // this run (server-side dedup would reject repeats, but mirror the
    // state in the UI so the operator can see what's still available).
    const ts = s.timestamps || {};
    gateButtons.forEach(b => {
      const n = b.dataset.gate;
      const alreadyFired = ts['t_gate_' + n + '_on'] != null;
      b.disabled = !manualEnabled || alreadyFired;
    });

    // Run info
    const seqShort = s.run_sequence_id ? s.run_sequence_id.substring(0, 8) : '--';
    const el = document.getElementById('run-info-text');
    if (el) el.textContent = 'Sequence: ' + seqShort + ' | Run: ' + (s.run_number || '--');
  });
})();
