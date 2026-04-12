'use strict';

const BASE = '';

// ── Cookie helpers ────────────────────────────────────────────────
const setCookie = (name, value) =>
  document.cookie = `${name}=${encodeURIComponent(value)};max-age=31536000;path=/`;

const getCookie = (name) => {
  const match = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
  return match ? decodeURIComponent(match[1]) : null;
};

const APP_TYPE_EXTENSION = {
  csv:        'csv',
  csv_web:    'csv',
  msmoney:    'ofx',
  quicken:    'qfx',
  quickbooks: 'qbo',
  simplyacc:  'aso',
};

let sessionId   = null;
let isLoggingIn = false;
let isFetching  = false;

const loginBtn   = document.getElementById('login-btn');
const fetchBtn   = document.getElementById('fetch-btn');
const otpSection = document.getElementById('otp-section');
const statusEl   = document.getElementById('status');
const otpInput   = document.getElementById('otp_code');

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

  const account_uuid = document.getElementById('account_uuid').value.trim();

  if (!account_uuid) {
    showStatus('error', 'Account UUID is required');
    return;
  }

  setCookie('bmo_account_uuid',   account_uuid);
  setCookie('bmo_app_type',       document.getElementById('app_type').value);
  setCookie('bmo_statement_date', document.getElementById('statement_date').value);
  setCookie('bmo_from_date',     document.getElementById('from_date').value);

  setLoggingIn(true);
  showStatus('loading', 'Opening BMO login — browser automation is running…');

  try {
    const res = await fetch(`${BASE}/bmo/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ account_uuid }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: `HTTP ${res.status} — ${res.statusText}` }));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }

    const data = await res.json();
    sessionId = data.session_id;

    otpSection.hidden = false;
    otpInput.focus();
    showStatus('success', 'Login initiated — enter the OTP sent to your phone below.');
  } catch (err) {
    showStatus('error', err.message);
    resetFlow();
  } finally {
    setLoggingIn(false);
  }
});

// ── Step 2: OTP + Download ────────────────────────────────────────
fetchBtn.addEventListener('click', async () => {
  if (isFetching) return;

  const otpCode        = otpInput.value.trim();
  const app_type       = document.getElementById('app_type').value;
  const statement_date = document.getElementById('statement_date').value;
  const isCsvWeb       = app_type === 'csv_web';
  const from_date     = document.getElementById('from_date').value;

  if (!otpCode) {
    showStatus('error', 'Please enter your OTP code before continuing.');
    otpInput.focus();
    return;
  }

  if (isCsvWeb && !from_date) {
    showStatus('error', 'Please select a "from date" for web parsing.');
    return;
  }

  if (!sessionId) {
    showStatus('error', 'Session expired — please restart the login.');
    resetFlow();
    return;
  }

  setFetching(true);
  showStatus('loading', 'Submitting OTP…');

  try {
    // Step 2a: verify OTP
    const verifyRes = await fetch(`${BASE}/bmo/verify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, otp_code: otpCode }),
    });

    if (!verifyRes.ok) {
      const err = await verifyRes.json().catch(() => ({ detail: `HTTP ${verifyRes.status}` }));
      throw new Error(err.detail || `OTP verification failed (HTTP ${verifyRes.status})`);
    }

    // Step 2b: scrape transactions
    const statusMsg = isCsvWeb
      ? 'OTP accepted — parsing transactions from page, this may take several minutes…'
      : 'OTP accepted — downloading transactions, this may take a minute…';
    showStatus('loading', statusMsg);

    const bodyObj = {
      session_id: sessionId,
      app_type,
    };
    if (isCsvWeb) {
      bodyObj.from_date = from_date;
    } else {
      bodyObj.statement_date = statement_date;
    }

    const res = await fetch(`${BASE}/bmo/scrape`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(bodyObj),
    });

    if (res.status === 404) {
      const err = await res.json().catch(() => ({ detail: 'Session not found — please restart the login.' }));
      throw Object.assign(new Error(err.detail || 'Session expired'), { resetRequired: true });
    }

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: `HTTP ${res.status} — ${res.statusText}` }));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }

    const blob = await res.blob();
    const ext = APP_TYPE_EXTENSION[app_type] || 'dat';
    const filename = getFilenameFromResponse(res) || `bmo_${app_type}_${today()}.${ext}`;
    triggerDownload(blob, filename);
    showStatus('success', `File downloaded — ${filename}`);

    const isOfx = ['msmoney', 'quicken'].includes(app_type);
    if (isOfx) {
      const ofxText = await blob.text();
      QfxFilter.initUI(document.getElementById('qfx-filter-section'), ofxText, filename);
      // Partial reset: hide OTP but keep filter section visible
      sessionId = null;
      otpSection.hidden = true;
      otpInput.value = '';
      loginBtn.disabled = false;
      fetchBtn.disabled = false;
      otpInput.disabled = false;
      const formInputs = document.querySelectorAll('.form-block select, .form-block input');
      formInputs.forEach(el => { el.disabled = false; });
    } else {
      resetFlow();
    }
  } catch (err) {
    showStatus('error', err.message);
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

// ── State helpers ─────────────────────────────────────────────────
function setLoggingIn(active) {
  isLoggingIn = active;
  loginBtn.disabled = active;
  const formInputs = document.querySelectorAll('.form-block select, .form-block input');
  formInputs.forEach(el => { el.disabled = active; });
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
  fetchBtn.disabled = false;
  otpInput.disabled = false;
  const formInputs = document.querySelectorAll('.form-block select, .form-block input');
  formInputs.forEach(el => { el.disabled = false; });
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

// ── Download helpers ──────────────────────────────────────────────
function triggerDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = Object.assign(document.createElement('a'), { href: url, download: filename });
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function getFilenameFromResponse(res) {
  const cd = res.headers.get('Content-Disposition') || '';
  const match = cd.match(/filename[^;=\n]*=\s*(?:["']([^"']+)["']|([^;\n]+))/i);
  return (match && (match[1] || match[2])?.trim()) || null;
}

function today() {
  return new Date().toISOString().split('T')[0];
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── Restore saved selections ──────────────────────────────────────
(function restoreSelections() {
  const account_uuid   = getCookie('bmo_account_uuid');
  const app_type       = getCookie('bmo_app_type');
  const statement_date = getCookie('bmo_statement_date');
  const from_date     = getCookie('bmo_from_date');
  if (account_uuid)   document.getElementById('account_uuid').value   = account_uuid;
  if (app_type)       document.getElementById('app_type').value       = app_type;
  if (statement_date) document.getElementById('statement_date').value = statement_date;
  if (from_date)     document.getElementById('from_date').value     = from_date;
})();

// ── CSV Web toggle ───────────────────────────────────────────────
(function initCsvWebToggle() {
  const appTypeSelect    = document.getElementById('app_type');
  const statementGroup   = document.getElementById('statement-date-group');
  const untilDateGroup   = document.getElementById('from-date-group');

  function toggle() {
    const isCsvWeb = appTypeSelect.value === 'csv_web';
    statementGroup.hidden = isCsvWeb;
    untilDateGroup.hidden = !isCsvWeb;
  }

  appTypeSelect.addEventListener('change', toggle);
  toggle();
})();
