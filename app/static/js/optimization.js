/**
 * Optimization page – skill results viewer.
 * Loads analysis report markdown files and renders them in-browser.
 */

(function () {
  const reportSelect  = document.getElementById('report-select');
  const reportContent = document.getElementById('report-content');
  const historyEl     = document.getElementById('history-content');

  // Configure marked for safe rendering
  marked.setOptions({
    gfm: true,
    breaks: false,
  });

  // ── Init ───────────────────────────────────────────────────────────

  loadFileList();
  loadHistory();

  reportSelect.addEventListener('change', () => {
    const filename = reportSelect.value;
    if (filename) loadReport(filename);
  });

  // ── Load file list ─────────────────────────────────────────────────

  async function loadFileList() {
    const files = await apiGet('/skill-results');
    reportSelect.innerHTML = '';

    if (!files.length) {
      const opt = document.createElement('option');
      opt.disabled = true;
      opt.selected = true;
      opt.textContent = 'No reports yet';
      reportSelect.appendChild(opt);
      return;
    }

    files.forEach((f, i) => {
      const opt = document.createElement('option');
      opt.value = f.filename;
      opt.textContent = f.timestamp;
      reportSelect.appendChild(opt);
    });

    // Auto-load the most recent report
    reportSelect.value = files[0].filename;
    loadReport(files[0].filename);
  }

  // ── Load and render a report ───────────────────────────────────────

  async function loadReport(filename) {
    reportContent.innerHTML = '<p class="opt-placeholder">Loading...</p>';

    const data = await apiGet('/skill-results/file/' + encodeURIComponent(filename));
    if (data.error) {
      reportContent.innerHTML = '<p class="opt-placeholder">' + data.error + '</p>';
      return;
    }

    reportContent.innerHTML = '<article class="md-rendered">' +
      marked.parse(data.content) + '</article>';
  }

  // ── Load history ───────────────────────────────────────────────────

  async function loadHistory() {
    const data = await apiGet('/skill-results/history');
    if (!data.content) {
      historyEl.innerHTML = '<p class="opt-placeholder">No history yet.</p>';
      return;
    }

    historyEl.innerHTML = '<div class="md-rendered md-compact">' +
      marked.parse(data.content) + '</div>';
  }
})();
