'use strict';

const BASE = '';

// Starting a job, following it and downloading what it produced is the same on every bank
// page, so it lives in jobs.js.

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
// False once the login comes back already signed in (WS skipped the code), so there is no
// OTP to ask for.
let otpRequired  = true;
// Wealthsimple's message while it waits for the sign-in to be approved on the user's phone
// ("Check your trusted device"), or null when it is not waiting for that.
let approvalPending = null;

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
    const job = await PennyJobs.run('/ws/login/jobs', LoginPicker.selected(), showJob);
    sessionId = job.session_id;
    otpRequired = job.auth.status === 'needs_otp';

    otpSection.hidden = false;
    otpInput.closest('.form-group').hidden = !otpRequired;
    if (job.auth.status === 'waiting_for_external') {
      // "Check your trusted device": nothing to type. The download starts by itself and
      // waits on the server until the sign-in is approved in the Wealthsimple app.
      approvalPending = job.auth.message;
      setTimeout(() => fetchBtn.click(), 0);
    } else if (otpRequired) {
      otpInput.focus();
      showStatus('success', 'Login initiated — enter the OTP sent to your device below.');
    } else {
      showStatus('success', 'Already signed in to Wealthsimple — no OTP needed.');
    }
  } catch (err) {
    showStatus('error', err.message);
    reportToServer('error', `login failed: ${err.message}`, {
      jobId: err.job && err.job.job_id,
      userAgent: navigator.userAgent,
    });
    resetFlow();
  } finally {
    setLoggingIn(false);
  }
});

// ── Step 2: OTP + Export ──────────────────────────────────────────
fetchBtn.addEventListener('click', async () => {
  if (isFetching) return;

  const otpCode = otpInput.value.trim();
  const request = buildDownloadRequest();

  if (otpRequired && !otpCode) {
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
  if (approvalPending) showStatus('loading', approvalPending);
  else showStatus('loading', otpRequired ? 'Submitting OTP…' : 'Checking the Wealthsimple session…');

  const diag = { stage: 'verify', startedAt: new Date().toISOString(), userAgent: navigator.userAgent };
  const startedMs = performance.now();

  try {
    // Step 2a: check the code. WS can answer it, or the password, with "Check your trusted
    // device"; the next verify then waits on the server for the sign-in to be approved in the
    // Wealthsimple app, and the page says so meanwhile.
    let verifyJob = await PennyJobs.run(
      '/ws/verify/jobs',
      { session_id: sessionId, otp_code: approvalPending ? null : (otpCode || null) },
      showVerifyJob,
    );
    while (verifyJob.auth.status === 'waiting_for_external') {
      approvalPending = verifyJob.auth.message || 'Approve the sign-in in the Wealthsimple app on your phone…';
      showStatus('loading', approvalPending);
      verifyJob = await PennyJobs.run('/ws/verify/jobs', { session_id: sessionId, otp_code: null }, showVerifyJob);
    }
    approvalPending = null;
    diag.verifyJobId = verifyJob.job_id;

    // Choosing "Try another way" on the trusted-device prompt switches to an SMS code.
    if (verifyJob.auth.status === 'needs_otp') {
      otpRequired = true;
      otpInput.closest('.form-group').hidden = false;
      otpInput.value = '';
      otpInput.focus();
      showStatus('success', verifyJob.auth.message || 'Enter the OTP sent to your device below.');
      return;
    }
    otpRequired = false;

    // Step 2b: export. The job keeps running on the server whatever happens to this page,
    // and its files stay downloadable from /exports/{job_id}/file once it is done.
    diag.stage = 'export';
    diag.download = request.type;
    const exportJob = await PennyJobs.run(
      request.path,
      { session_id: sessionId, ...request.body },
      showJob,
    );
    diag.exportJobId = exportJob.job_id;

    // Wealthsimple gives one CSV per account, and each one is saved as its own download —
    // the activity exports byte-for-byte as WS wrote them, and an earnings series per
    // account named above, built on the server.
    diag.stage = 'download';
    const files = await PennyJobs.saveExport(exportJob);
    diag.files = files.map((file) => ({ name: file.filename, bytes: file.size, url: file.url }));

    diag.stage = 'done';
    diag.totalElapsedMs = Math.round(performance.now() - startedMs);
    showStatus('success', PennyJobs.describeSaved(files, request.label));
    reportToServer('info', `export delivered ${files.length} file(s)`, diag);
    resetFlow();
  } catch (err) {
    const diagnostic = { ...diag, message: err.message };
    if (err.job) {
      diagnostic.jobId = err.job.job_id;
      diagnostic.jobStage = err.job.stage;
      diagnostic.lastDetail = err.job.detail;
    }
    diagnostic.totalElapsedMs = Math.round(performance.now() - startedMs);
    showStatus('error', err.message);
    showDiagnostic(diagnostic);
    reportToServer('error', `export failed at stage "${diagnostic.stage}": ${err.message}`, diagnostic);
    // A failed code check or export ends the session on the server, and so does an unknown one.
    if (err.job || err.status === 404) {
      resetFlow();
    }
  } finally {
    setFetching(false);
  }
});

// Puts a running job's progress on the spinner.
function showJob(job) {
  if (job.state === 'queued' || job.state === 'running') {
    showStatus('loading', PennyJobs.describe(job));
  }
}

// While the sign-in waits for approval on the phone, that message stays on the spinner, with
// how long it has been waiting; otherwise the usual progress is shown.
function showVerifyJob(job) {
  if (!approvalPending) {
    showJob(job);
    return;
  }
  if (job.state === 'queued' || job.state === 'running') {
    const since = job.started_at || job.created_at;
    const seconds = since ? Math.max(0, Math.round((Date.now() - Date.parse(since)) / 1000)) : 0;
    showStatus('loading', `${approvalPending} (${seconds}s)`);
  }
}

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
    path: '/ws/export',
    body: { since_date: sinceDateEl.value, account_ids: accountIds },
    progress: accountIds.length
      ? `fetching activity data and daily earnings for ${accounts}`
      : 'fetching activity data',
    label: accountIds.length ? 'Activity and daily earnings' : 'Activity',
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
  otpRequired = true;
  approvalPending = null;
  otpSection.hidden = true;
  otpInput.closest('.form-group').hidden = false;
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

LoginPicker.mount('ws');
