'use strict';

const BASE = 'http://localhost:5056';

let sessionId    = null;
let isLoggingIn  = false;
let isFetching   = false;

const loginBtn   = document.getElementById('login-btn');
const fetchBtn   = document.getElementById('fetch-btn');
const otpSection = document.getElementById('otp-section');
const statusEl   = document.getElementById('status');
const sinceDateEl = document.getElementById('since_date');
const otpInput   = document.getElementById('otp_code');

// ── Populate since_date options ───────────────────────────────────
(function buildDateOptions() {
  const now = new Date();
  const options = [
    { label: 'Last 1 month',  months: 1  },
    { label: 'Last 3 months', months: 3  },
    { label: 'Last 6 months', months: 6  },
    { label: 'Last 1 year',   months: 12 },
    { label: 'Last 2 years',  months: 24 },
  ];
  options.forEach(({ label, months }) => {
    const d = new Date(now);
    d.setMonth(d.getMonth() - months);
    const value = d.toISOString().split('T')[0];
    const opt = new Option(`${label}  (since ${value})`, value);
    sinceDateEl.appendChild(opt);
  });
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

  setLoggingIn(true);
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
    resetFlow();
  } finally {
    setLoggingIn(false);
  }
});

// ── Step 2: OTP + Fetch ───────────────────────────────────────────
fetchBtn.addEventListener('click', async () => {
  if (isFetching) return;

  const otpCode   = otpInput.value.trim();
  const sinceDate = sinceDateEl.value;

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
  showStatus('loading', 'Submitting OTP and fetching activity data — this may take a minute…');

  try {
    const res = await fetch(`${BASE}/ws/scrape`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: sessionId,
        otp_code:   otpCode,
        since_date: sinceDate,
        format:     'csv',
      }),
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
    const filename = getFilenameFromResponse(res) || `wealthsimple_activity_${today()}.csv`;
    triggerDownload(blob, filename);
    showStatus('success', `File downloaded — ${filename}`);
    resetFlow();
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
  sinceDateEl.disabled = active;
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
