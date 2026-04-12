'use strict';

const BASE = '';

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
let cachedCsvText = null;

const loginBtn      = document.getElementById('login-btn');
const fetchBtn      = document.getElementById('fetch-btn');
const otpSection    = document.getElementById('otp-section');
const statusEl      = document.getElementById('status');
const sinceDateEl   = document.getElementById('since_date');
const otpInput      = document.getElementById('otp_code');
const accountSection = document.getElementById('account-section');
const accountSelect  = document.getElementById('account-select');
const downloadBtn    = document.getElementById('download-btn');

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

  const savedIndex = parseInt(getCookie('ws_since_index'), 10);
  if (!isNaN(savedIndex) && savedIndex >= 0 && savedIndex < sinceDateEl.options.length) {
    sinceDateEl.selectedIndex = savedIndex;
  }
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

  setCookie('ws_since_index', sinceDateEl.selectedIndex);

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
  showStatus('loading', 'Submitting OTP…');

  try {
    // Step 2a: verify OTP
    const verifyRes = await fetch(`${BASE}/ws/verify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, otp_code: otpCode }),
    });

    if (!verifyRes.ok) {
      const err = await verifyRes.json().catch(() => ({ detail: `HTTP ${verifyRes.status}` }));
      throw new Error(err.detail || `OTP verification failed (HTTP ${verifyRes.status})`);
    }

    // Step 2b: scrape transactions
    showStatus('loading', 'OTP accepted — fetching activity data, this may take a minute…');

    const res = await fetch(`${BASE}/ws/scrape`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: sessionId,
        since_date: sinceDate,
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
    cachedCsvText = await blob.text();
    populateAccountDropdown(cachedCsvText);
    accountSection.hidden = false;
    showStatus('success', 'Activity fetched — select an account below and click Download CSV.');
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
  // Note: cachedCsvText and #account-section are intentionally kept alive
  // so the user can keep downloading after the login flow is done.
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

// ── Account dropdown + filtered download ─────────────────────────
downloadBtn.addEventListener('click', () => {
  if (!cachedCsvText) return;
  const account = accountSelect.value;
  const filtered = filterCsvByAccount(cachedCsvText, account);
  const suffix = account === 'ALL' ? 'all' : account.replace(/\s+/g, '_');
  triggerDownload(new Blob([filtered], { type: 'text/csv' }), `wealthsimple_${suffix}.csv`);
});

function populateAccountDropdown(csvText) {
  const lines = csvText.trim().split('\n');
  const headers = parseCsvLine(lines[0]);
  const accountIdx = headers.indexOf('Account');
  if (accountIdx === -1) return;

  const accounts = new Set();
  for (let i = 1; i < lines.length; i++) {
    const cols = parseCsvLine(lines[i]);
    if (cols[accountIdx]) accounts.add(cols[accountIdx].trim());
  }

  accountSelect.innerHTML = '<option value="ALL">ALL</option>';
  for (const acc of [...accounts].sort()) {
    const opt = document.createElement('option');
    opt.value = acc;
    opt.textContent = acc;
    accountSelect.appendChild(opt);
  }
}

function filterCsvByAccount(csvText, account) {
  if (account === 'ALL') return csvText;
  const lines = csvText.trim().split('\n');
  const headers = parseCsvLine(lines[0]);
  const accountIdx = headers.indexOf('Account');
  if (accountIdx === -1) return csvText;

  const kept = [lines[0]];
  for (let i = 1; i < lines.length; i++) {
    const cols = parseCsvLine(lines[i]);
    if (cols[accountIdx]?.trim() === account) kept.push(lines[i]);
  }
  return kept.join('\n');
}

function parseCsvLine(line) {
  const fields = [];
  let cur = '';
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (inQuotes) {
      if (ch === '"' && line[i + 1] === '"') { cur += '"'; i++; }
      else if (ch === '"') { inQuotes = false; }
      else { cur += ch; }
    } else {
      if (ch === '"') { inQuotes = true; }
      else if (ch === ',') { fields.push(cur); cur = ''; }
      else { cur += ch; }
    }
  }
  fields.push(cur);
  return fields;
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
