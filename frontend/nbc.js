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

const DEFAULT_MONTHS_BACK = 3;
const MAX_MONTHS_BACK     = 12;

let sessionId    = null;
let isLoggingIn  = false;
let isFetching   = false;
// False once the login comes back already signed in (NBC skipped the code), so there is no
// code to ask for.
let otpRequired  = true;

const loginBtn    = document.getElementById('login-btn');
const fetchBtn    = document.getElementById('fetch-btn');
const otpSection  = document.getElementById('otp-section');
const otpNotice   = document.getElementById('otp-notice');
const statusEl    = document.getElementById('status');
const sinceDateEl = document.getElementById('since_date');
const otpInput    = document.getElementById('otp_code');

// A cached script paired with freshly served markup runs the page as two versions of
// itself; say so instead of failing somewhere far from the cause.
const missingElements = Object.entries({
  'login-btn': loginBtn,
  'fetch-btn': fetchBtn,
  'otp-section': otpSection,
  'otp-notice': otpNotice,
  'status': statusEl,
  'since_date': sinceDateEl,
  'otp_code': otpInput,
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

// ── since_date ────────────────────────────────────────────────────
// NBC's history is read with its "Last 12 months" view, so the date can go back that far.
const isoDate = (d) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

const monthsBack = (months) => {
  const d = new Date();
  d.setMonth(d.getMonth() - months);
  return isoDate(d);
};

(function initSinceDate() {
  sinceDateEl.max = isoDate(new Date());
  sinceDateEl.min = monthsBack(MAX_MONTHS_BACK);
  const saved = getCookie('nbc_since_date');
  sinceDateEl.value = saved && saved >= sinceDateEl.min && saved <= sinceDateEl.max
    ? saved
    : monthsBack(DEFAULT_MONTHS_BACK);
})();

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

  if (!sinceDateEl.value || sinceDateEl.value > sinceDateEl.max || sinceDateEl.value < sinceDateEl.min) {
    showStatus('error', `Pick a date between ${sinceDateEl.min} and ${sinceDateEl.max}.`);
    sinceDateEl.focus();
    return;
  }
  setCookie('nbc_since_date', sinceDateEl.value);

  setLoggingIn(true);
  clearDiagnostic();
  showStatus('loading', 'Signing in to NBC…');

  try {
    const job = await PennyJobs.run('/nbc/login/jobs', LoginPicker.selected(), showJob);
    sessionId = job.session_id;
    otpRequired = job.auth.status !== 'authenticated';

    otpSection.hidden = false;
    otpInput.closest('.form-group').hidden = !otpRequired;
    if (otpRequired) {
      // The server says where the code went: "NBC sent a 6-digit code by email to j...e@…".
      otpNotice.textContent = job.auth.message || 'Enter the verification code NBC sent you.';
      otpInput.focus();
      showStatus('success', otpNotice.textContent);
    } else {
      otpNotice.textContent = 'NBC signed in without a code.';
      showStatus('success', 'Already signed in to NBC — no code needed.');
    }
  } catch (err) {
    showStatus('error', err.message);
    reportToServer('error', `nbc login failed: ${err.message}`, {
      jobId: err.job && err.job.job_id,
      userAgent: navigator.userAgent,
    });
    resetFlow();
  } finally {
    setLoggingIn(false);
  }
});

// ── Step 2: code + export ─────────────────────────────────────────
fetchBtn.addEventListener('click', async () => {
  if (isFetching) return;

  const otpCode = otpInput.value.trim();

  if (otpRequired && !otpCode) {
    showStatus('error', 'Please enter the verification code before continuing.');
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
  showStatus('loading', otpRequired ? 'Submitting the code…' : 'Checking the NBC session…');

  const diag = { bank: 'nbc', stage: 'verify', startedAt: new Date().toISOString(), userAgent: navigator.userAgent };
  const startedMs = performance.now();

  try {
    const verifyJob = await PennyJobs.run(
      '/nbc/verify/jobs',
      { session_id: sessionId, otp_code: otpCode || null },
      showJob,
    );
    diag.verifyJobId = verifyJob.job_id;

    // A rejected code keeps the session open: NBC's message is shown and the code can be
    // typed again.
    if (verifyJob.auth.status === 'needs_otp') {
      otpNotice.textContent = verifyJob.auth.message || 'NBC did not accept the code. Enter it again.';
      showStatus('error', otpNotice.textContent);
      otpInput.select();
      return;
    }
    otpRequired = false;

    diag.stage = 'export';
    const exportJob = await PennyJobs.run(
      '/nbc/export',
      { session_id: sessionId, since_date: sinceDateEl.value },
      showJob,
    );
    diag.exportJobId = exportJob.job_id;

    // One CSV per account, each saved as its own download.
    diag.stage = 'download';
    const files = await PennyJobs.saveExport(exportJob);
    diag.files = files.map((file) => ({ name: file.filename, bytes: file.size, url: file.url }));

    diag.stage = 'done';
    diag.totalElapsedMs = Math.round(performance.now() - startedMs);
    showStatus('success', PennyJobs.describeSaved(files, 'Transactions'));
    reportToServer('info', `nbc export delivered ${files.length} file(s)`, diag);
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
    reportToServer('error', `nbc export failed at stage "${diagnostic.stage}": ${err.message}`, diagnostic);
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

otpInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') fetchBtn.click();
});

// ── State helpers ─────────────────────────────────────────────────
function setLoggingIn(active) {
  isLoggingIn = active;
  loginBtn.disabled = active;
  sinceDateEl.disabled = active;
}

function setFetching(active) {
  isFetching = active;
  fetchBtn.disabled = active;
  otpInput.disabled = active;
}

function resetFlow() {
  sessionId = null;
  otpRequired = true;
  otpSection.hidden = true;
  otpInput.closest('.form-group').hidden = false;
  otpInput.value = '';
  otpNotice.textContent = 'Enter the verification code NBC sent you.';
  loginBtn.disabled = false;
  sinceDateEl.disabled = false;
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
// Every failure is kept on screen and sent to the server log, next to the scrape it
// belongs to.

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

LoginPicker.mount('nbc');
