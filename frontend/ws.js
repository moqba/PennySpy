'use strict';

const BASE = '';

// Reading the scrape response and saving what it carries is the same job on every bank
// page, so it lives in scrape-download.js and both pages share it.
const { StageError, readScrapeResponse, saveAll, describeSaved } = window.ScrapeDownload;

// ── Cookie helpers ────────────────────────────────────────────────
const setCookie = (name, value) =>
  document.cookie = `${name}=${encodeURIComponent(value)};max-age=31536000;path=/`;

const getCookie = (name) => {
  const match = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
  return match ? decodeURIComponent(match[1]) : null;
};

let sessionId    = null;
let isLoggingIn  = false;
let isFetching   = false;

const loginBtn         = document.getElementById('login-btn');
const fetchBtn         = document.getElementById('fetch-btn');
const otpSection       = document.getElementById('otp-section');
const statusEl         = document.getElementById('status');
const sinceDateEl      = document.getElementById('since_date');
const otpInput         = document.getElementById('otp_code');
const accountIdsEl     = document.getElementById('account_ids');

// A cached script paired with freshly served markup runs the page as two versions of
// itself, and the first sign of it is a null element somewhere far from the cause — which
// reads as a scrape failure rather than a stale file. Say what actually happened instead.
const missingElements = Object.entries({
  'login-btn': loginBtn,
  'fetch-btn': fetchBtn,
  'otp-section': otpSection,
  'status': statusEl,
  'since_date': sinceDateEl,
  'otp_code': otpInput,
  'account_ids': accountIdsEl,
}).filter(([, el]) => !el).map(([id]) => id);

if (missingElements.length) {
  const message =
    `This page and its script are out of step — ${missingElements.join(', ')} missing. ` +
    'Reload with Ctrl+Shift+R (Cmd+Shift+R on macOS) to clear the cached copy.';
  document.body.prepend(Object.assign(document.createElement('div'), {
    className: 'health-alert',
    textContent: message,
  }));
  throw new Error(message);
}

// ── Populate since_date options ───────────────────────────────────
(function buildDateOptions() {
  const now = new Date();
  // These are exactly the periods Wealthsimple's own export offers; since_date picks
  // the window, and the CSV comes back covering that whole window. The daily earnings
  // series uses the same window — its graph is always fetched over a year and trimmed to
  // the chosen period on the server.
  const options = [
    { label: 'Last 3 months', months: 3  },
    { label: 'Last 6 months', months: 6  },
    { label: 'Last 12 months', months: 12 },
  ];
  options.forEach(({ label, months }) => {
    const d = new Date(now);
    d.setMonth(d.getMonth() - months);
    const value = d.toISOString().split('T')[0];
    const opt = new Option(`${label}  (since ${value})`, value);
    sinceDateEl.appendChild(opt);
  });

  const savedIndex = parseInt(getCookie('ws_since_index'), 10);
  if (!isNaN(savedIndex) && savedIndex >= 0 && savedIndex < sinceDateEl.options.length) {
    sinceDateEl.selectedIndex = savedIndex;
  }
})();

// ── Daily earnings ────────────────────────────────────────────────
// The earnings series rides along with the activity export: naming accounts adds one CSV
// each to the same download, and naming none downloads the activity export alone. The
// button says which of the two is about to happen.
(function initAccountIds() {
  const savedAccounts = getCookie('ws_account_ids');
  if (savedAccounts) accountIdsEl.value = parseAccountIds(savedAccounts).join('\n');

  accountIdsEl.addEventListener('input', applyDownloadLabel);
  applyDownloadLabel();
})();

function applyDownloadLabel() {
  fetchBtn.textContent = parseAccountIds(accountIdsEl.value).length
    ? 'Download Activity + Earnings'
    : 'Download Activity';
}

// Split the account-ID textarea on newlines and/or commas, trimming blanks.
function parseAccountIds(raw) {
  return String(raw || '')
    .split(/[\n,]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

// ── Beforeunload guard ────────────────────────────────────────────
window.addEventListener('beforeunload', (e) => {
  if (isLoggingIn || isFetching) {
    e.preventDefault();
    e.returnValue = '';
  }
});

// ── Step 1: Login ─────────────────────────────────────────────────
loginBtn.addEventListener('click', async () => {
  if (isLoggingIn || isFetching) return;

  setCookie('ws_since_index', sinceDateEl.selectedIndex);
  setCookie('ws_account_ids', parseAccountIds(accountIdsEl.value).join('\n'));

  setLoggingIn(true);
  clearDiagnostic();
  showStatus('loading', 'Opening Wealthsimple login — complete sign-in in the browser window…');

  try {
    const res = await fetch(`${BASE}/ws/login`, { method: 'POST' });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: `HTTP ${res.status} — ${res.statusText}` }));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }

    const data = await res.json();
    sessionId = data.session_id;

    otpSection.hidden = false;
    otpInput.focus();
    showStatus('success', 'Login initiated — enter the OTP sent to your device below.');
  } catch (err) {
    showStatus('error', err.message);
    reportToServer('error', `login failed: ${err.message}`, { userAgent: navigator.userAgent });
    resetFlow();
  } finally {
    setLoggingIn(false);
  }
});

// ── Step 2: OTP + Fetch ───────────────────────────────────────────
fetchBtn.addEventListener('click', async () => {
  if (isFetching) return;

  const otpCode = otpInput.value.trim();
  const request = buildDownloadRequest();

  if (!otpCode) {
    showStatus('error', 'Please enter your OTP code before continuing.');
    otpInput.focus();
    return;
  }

  if (!sessionId) {
    showStatus('error', 'Session expired — please restart the login.');
    resetFlow();
    return;
  }

  setFetching(true);
  clearDiagnostic();
  showStatus('loading', 'Submitting OTP…');

  const diag = { stage: 'verify', startedAt: new Date().toISOString(), userAgent: navigator.userAgent };
  const startedMs = performance.now();

  try {
    // Step 2a: verify OTP
    const verifyRes = await fetch(`${BASE}/ws/verify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, otp_code: otpCode }),
    });

    if (!verifyRes.ok) {
      const err = await verifyRes.json().catch(() => ({ detail: `HTTP ${verifyRes.status}` }));
      throw new StageError('verify', err.detail || `OTP verification failed (HTTP ${verifyRes.status})`, diag);
    }

    // Step 2b: scrape transactions
    showStatus('loading', `OTP accepted — ${request.progress}, this may take a few minutes…`);

    diag.stage = 'fetch';
    diag.download = request.type;
    const fetchStartedMs = performance.now();
    let res;
    try {
      res = await fetch(`${BASE}${request.path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, ...request.body }),
      });
    } catch (err) {
      // The scrape holds one request open for minutes; a connection that dies in that
      // window leaves the server having finished the work and the browser with nothing.
      diag.elapsedMs = Math.round(performance.now() - fetchStartedMs);
      throw new StageError(
        'fetch',
        `The connection dropped after ${Math.round(diag.elapsedMs / 1000)}s — the scrape may have finished on ` +
        `the server without reaching this page. Check the server log and the exports directory. (${err.message})`,
        diag,
      );
    }
    diag.elapsedMs = Math.round(performance.now() - fetchStartedMs);

    if (res.status === 404) {
      const err = await res.json().catch(() => ({ detail: 'Session not found — please restart the login.' }));
      throw Object.assign(new StageError('http', err.detail || 'Session expired', diag), { resetRequired: true });
    }

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: `HTTP ${res.status} — ${res.statusText}` }));
      throw new StageError('http', err.detail || `HTTP ${res.status}`, diag);
    }

    // Wealthsimple gives one CSV per account, and each one is saved as its own download —
    // the activity exports byte-for-byte as WS wrote them, and an earnings series per
    // account named above, built on the server.
    const files = await readScrapeResponse(res, diag, {
      fallbackName: request.fallbackName,
      mimeType: 'text/csv',
    });

    diag.stage = 'trigger-download';
    diag.files = files.map((file) => ({ name: file.name, bytes: file.blob.size }));
    await saveAll(files);

    diag.stage = 'done';
    diag.totalElapsedMs = Math.round(performance.now() - startedMs);
    showStatus('success', describeSaved(files, request.label));
    reportToServer('info', `scrape delivered ${files.length} file(s)`, diag);
    resetFlow();
  } catch (err) {
    const diagnostic = { ...(err.diag || diag), stage: err.stage || diag.stage || 'unknown', message: err.message };
    diagnostic.totalElapsedMs = Math.round(performance.now() - startedMs);
    showStatus('error', err.message);
    showDiagnostic(diagnostic);
    reportToServer('error', `scrape failed at stage "${diagnostic.stage}": ${err.message}`, diagnostic);
    if (err.resetRequired) {
      resetFlow();
    }
  } finally {
    setFetching(false);
  }
});

// ── Allow pressing Enter in OTP field to submit ───────────────────
otpInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') fetchBtn.click();
});

// ── The request the current choice describes ──────────────────────
// Returns everything the fetch step needs. The options are read here rather than when the
// login started, so a correction made while waiting for the OTP is the one that gets sent.
function buildDownloadRequest() {
  const accountIds = parseAccountIds(accountIdsEl.value);
  const accounts = `${accountIds.length} account${accountIds.length > 1 ? 's' : ''}`;

  return {
    type: accountIds.length ? 'activity+earnings' : 'activity',
    path: '/ws/scrape',
    body: { since_date: sinceDateEl.value, account_ids: accountIds },
    progress: accountIds.length
      ? `fetching activity data and daily earnings for ${accounts}`
      : 'fetching activity data',
    label: accountIds.length ? 'Activity and daily earnings' : 'Activity',
    fallbackName: `wealthsimple_activity_${today()}.csv`,
  };
}

// ── State helpers ─────────────────────────────────────────────────
const optionInputs = () => [sinceDateEl, accountIdsEl];

function setLoggingIn(active) {
  isLoggingIn = active;
  loginBtn.disabled = active;
  optionInputs().forEach((el) => { el.disabled = active; });
}

function setFetching(active) {
  isFetching = active;
  fetchBtn.disabled = active;
  otpInput.disabled = active;
}

function resetFlow() {
  sessionId = null;
  otpSection.hidden = true;
  otpInput.value = '';
  loginBtn.disabled = false;
  optionInputs().forEach((el) => { el.disabled = false; });
  fetchBtn.disabled = false;
  otpInput.disabled = false;
}

// ── Status display ────────────────────────────────────────────────
function showStatus(type, message) {
  statusEl.hidden = false;
  statusEl.className = `status status--${type}`;

  let icon;
  if (type === 'loading') icon = '<span class="spinner"></span>';
  else if (type === 'error')   icon = '<span class="status__icon">✕</span>';
  else if (type === 'success') icon = '<span class="status__icon">✓</span>';
  else icon = '';

  statusEl.innerHTML = `${icon}<span class="status__text">${escapeHtml(message)}</span>`;
}

function today() {
  return new Date().toISOString().split('T')[0];
}

// ── Diagnostics ───────────────────────────────────────────────────
// A status message is gone the moment the page is left, which is how the failure that
// prompted all this went unread. Every failure is therefore also kept on screen and sent
// to the server log, where it sits next to the scrape it belongs to.

function reportToServer(level, message, detail) {
  try {
    fetch(`${BASE}/client-log`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ level, message, detail }),
      keepalive: true,
    }).catch(() => {});
  } catch {
    // Reporting a problem must never become one.
  }
}

function diagnosticEl() {
  let el = document.getElementById('diagnostic');
  if (!el) {
    el = document.createElement('div');
    el.id = 'diagnostic';
    statusEl.insertAdjacentElement('afterend', el);
  }
  return el;
}

function clearDiagnostic() {
  const el = document.getElementById('diagnostic');
  if (el) el.replaceChildren();
}

function showDiagnostic(detail) {
  const el = diagnosticEl();
  const text = JSON.stringify(detail, null, 2);
  el.innerHTML =
    '<details class="diagnostic" open>' +
    '<summary class="diagnostic__summary">Diagnostic details</summary>' +
    `<pre class="diagnostic__body">${escapeHtml(text)}</pre>` +
    '<button type="button" class="btn btn--full" id="diagnostic-copy">Copy diagnostic</button>' +
    '<p class="diagnostic__note">Also written to the server log — open the Logs page to read it later.</p>' +
    '</details>';
  el.querySelector('#diagnostic-copy').addEventListener('click', (event) => {
    navigator.clipboard.writeText(text).then(
      () => { event.target.textContent = 'Copied'; },
      () => { event.target.textContent = 'Copy failed — select the text above'; },
    );
  });
}

window.addEventListener('error', (event) => {
  reportToServer('error', `uncaught error: ${event.message}`, {
    source: event.filename,
    line: event.lineno,
    column: event.colno,
    userAgent: navigator.userAgent,
  });
});

window.addEventListener('unhandledrejection', (event) => {
  const reason = event.reason;
  reportToServer('error', `unhandled rejection: ${(reason && reason.message) || reason}`, {
    stack: reason && reason.stack,
    userAgent: navigator.userAgent,
  });
});

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
