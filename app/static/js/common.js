/**
 * Shared SocketIO client and API helpers.
 * Every page sources this first; page-specific scripts register
 * callbacks via onStateUpdate() and onEvent() to receive data.
 */

// ── SocketIO connection ────────────────────────────────────────────────

let _stateCallbacks = [];
let _eventCallbacks = {};  // event name -> [fn, ...]
let _lastState = null;
let socket = null;

function onStateUpdate(fn) {
  _stateCallbacks.push(fn);
  if (_lastState) fn(_lastState);
}

function onEvent(event, fn) {
  if (!_eventCallbacks[event]) _eventCallbacks[event] = [];
  _eventCallbacks[event].push(fn);
}

function _connectSocketIO() {
  socket = io();

  socket.on('connect', function () {
    _setConnStatus('LIVE', true);
  });

  socket.on('disconnect', function () {
    _setConnStatus('OFFLINE', false);
  });

  socket.on('state_update', function (data) {
    _lastState = data;
    _stateCallbacks.forEach(fn => fn(data));
  });

  // Forward named events to registered listeners
  ['run_saved', 'config_updated', 'sequence_changed'].forEach(evt => {
    socket.on(evt, function (data) {
      (_eventCallbacks[evt] || []).forEach(fn => fn(data));
    });
  });
}

function _setConnStatus(text, ok) {
  const el = document.getElementById('conn-status');
  if (el) {
    el.textContent = text;
    el.style.color = ok ? 'var(--green)' : 'var(--red)';
  }
}

// Connect on load
document.addEventListener('DOMContentLoaded', _connectSocketIO);


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
