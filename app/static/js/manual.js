/**
 * Manual page – independent coil fires + simulated gate triggers.
 *
 * Buttons require the sequencer to be ARMED (or already FIRING). The
 * enable/disable logic mirrors that invariant so the operator can't
 * send a request that the backend is just going to reject.
 */

(function () {
  const btnArm    = document.getElementById('btn-arm');
  const btnSave   = document.getElementById('btn-save');
  const btnClear  = document.getElementById('btn-clear');
  const btnCoil2  = document.getElementById('btn-coil-2');
  const btnCoil3  = document.getElementById('btn-coil-3');
  const btnGate1  = document.getElementById('btn-gate-1');
  const btnGate2  = document.getElementById('btn-gate-2');
  const stateInd  = document.getElementById('state-indicator');
  const stateLbl  = document.getElementById('state-label');

  // First timestamp we've seen this run — used as a zero so the other
  // columns render as small-integer µs offsets instead of 20-digit
  // perf_counter_ns values.
  let _tZero = null;

  // ── Lifecycle ────────────────────────────────────────────────────────
  btnArm.addEventListener('click',   () => { btnArm.disabled   = true; apiPost('/arm');   });
  btnSave.addEventListener('click',  () => { btnSave.disabled  = true; apiPost('/save');  });
  btnClear.addEventListener('click', () => { btnClear.disabled = true; apiPost('/clear'); });

  // ── Component tests ──────────────────────────────────────────────────
  btnCoil2.addEventListener('click', () => _manual('/manual/coil/2/fire'));
  btnCoil3.addEventListener('click', () => _manual('/manual/coil/3/fire'));
  btnGate1.addEventListener('click', () => _manual('/manual/gate/1/trigger'));
  btnGate2.addEventListener('click', () => _manual('/manual/gate/2/trigger'));

  function _manual(path) {
    // Brief disable to prevent double-click; the backend's state check
    // is authoritative but UX is nicer if we don't fire twice off one
    // fat-finger tap. State update will re-enable based on server state.
    [btnCoil2, btnCoil3, btnGate1, btnGate2].forEach(b => b.disabled = true);
    apiPost(path);
  }

  // ── State updates via SocketIO ───────────────────────────────────────
  onStateUpdate(function (s) {
    const state = s.state;

    stateInd.className = 'state-indicator ' + state;
    stateLbl.textContent = state.toUpperCase();

    // Lifecycle buttons
    btnArm.disabled   = state !== 'ready';
    btnSave.disabled  = (state !== 'firing' && state !== 'complete' && state !== 'armed');
    btnClear.disabled = (state === 'ready');

    // Component-test buttons: require ARMED or FIRING
    const canManual = (state === 'armed' || state === 'firing');
    btnCoil2.disabled = !canManual;
    btnCoil3.disabled = !canManual;
    btnGate1.disabled = !canManual;
    btnGate2.disabled = !canManual;

    // Reset the render baseline at the start of every new run so the
    // displayed offsets are always relative to *this* run.
    if (state === 'ready') _tZero = null;

    // Timestamps — convert ns → µs offsets from the first-seen event.
    const ts = s.timestamps || {};
    ['t_coil_1_on', 't_coil_1_off',
     't_coil_2_on', 't_coil_2_off',
     't_coil_3_on', 't_coil_3_off',
     't_gate_1_on', 't_gate_1_off',
     't_gate_2_on', 't_gate_2_off',
     't_gate_3_on', 't_gate_3_off'].forEach(k => {
      const v = ts[k];
      if (v != null && _tZero == null) _tZero = v;
      const cellId = k.replace('t_', 't-').replaceAll('_', '-').replace('-on', '-on').replace('-off', '-off');
      _setText(cellId, _fmtOffset(v, _tZero));
    });

    // Run info
    const seqShort = s.run_sequence_id ? s.run_sequence_id.substring(0, 8) : '--';
    _setText('run-info-text', 'Sequence: ' + seqShort + ' | Run: ' + (s.run_number || '--'));
  });

  function _fmtOffset(v, zero) {
    if (v == null || zero == null) return '--';
    const us = (v - zero) / 1000;
    return us.toFixed(1);
  }

  function _setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
  }
})();
