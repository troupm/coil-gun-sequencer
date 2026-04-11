/**
 * Shared SSE client and API helpers.
 * Every page sources this first; page-specific scripts register a
 * callback via onStateUpdate() to receive sequencer state snapshots.
 */

// ── SSE connection ─────────────────────────────────────────────────────

let _stateCallbacks = [];
let _lastState = null;
let _evtSource = null;

function onStateUpdate(fn) {
  _stateCallbacks.push(fn);
  // If we already have state, call immediately
  if (_lastState) fn(_lastState);
}

function _connectSSE() {
  if (_evtSource) _evtSource.close();
  _evtSource = new EventSource('/api/stream');

  _evtSource.onmessage = function(e) {
    const data = JSON.parse(e.data);
    _lastState = data;
    _stateCallbacks.forEach(fn => fn(data));
    _setConnStatus('LIVE', true);
  };

  _evtSource.onerror = function() {
    _setConnStatus('OFFLINE', false);
    // EventSource auto-reconnects
  };
}

function _setConnStatus(text, ok) {
  const el = document.getElementById('conn-status');
  if (el) {
    el.textContent = text;
    el.style.color = ok ? 'var(--green)' : 'var(--red)';
  }
}

// Connect on load
document.addEventListener('DOMContentLoaded', _connectSSE);


// ── API helpers ────────────────────────────────────────────────────────

async function apiPost(path, body) {
  const resp = await fetch('/api' + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
  return resp.json();
}

async function apiGet(path) {
  const resp = await fetch('/api' + path);
  return resp.json();
}


// ── Formatting helpers ─────────────────────────────────────────────────

function fmtUs(val) {
  if (val == null) return '--';
  if (val >= 1000) return (val / 1000).toFixed(2) + ' ms';
  return val.toFixed(1) + ' \u00B5s';
}

function fmtVel(val) {
  if (val == null) return '--';
  return val.toFixed(2) + ' m/s';
}
