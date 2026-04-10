'use strict';

const BASE = 'http://localhost:5056';

// ── Cookie helpers ────────────────────────────────────────────────
const setCookie = (name, value) =>
  document.cookie = `${name}=${encodeURIComponent(value)};max-age=31536000;path=/`;

const getCookie = (name) => {
  const match = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
  return match ? decodeURIComponent(match[1]) : null;
};

let sessionId  = null;
let isFetching = false;

const fetchBtn   = document.getElementById('fetch-btn');
const statusEl   = document.getElementById('status');
const fromDateEl = document.getElementById('from_date');
const toDateEl   = document.getElementById('to_date');

// ── Set default dates ────────────────────────────────────────────
(function setDefaults() {
  const today = new Date();
  toDateEl.value = today.toISOString().split('T')[0];

  const threeMonthsAgo = new Date(today);
  threeMonthsAgo.setMonth(threeMonthsAgo.getMonth() - 3);
  fromDateEl.value = threeMonthsAgo.toISOString().split('T')[0];

  const savedFrom = getCookie('scotia_from_date');
  const savedTo   = getCookie('scotia_to_date');
  if (savedFrom) fromDateEl.value = savedFrom;
  if (savedTo)   toDateEl.value = savedTo;
})();

// ── Beforeunload guard ────────────────────────────────────────────
window.addEventListener('beforeunload', (e) => {
  if (isFetching) {
    e.preventDefault();
    e.returnValue = '';
  }
});

// ── Fetch trigger ─────────────────────────────────────────────────
fetchBtn.addEventListener('click', async () => {
  if (isFetching) return;

  const fromDate = fromDateEl.value;
  const toDate   = toDateEl.value;

  if (!fromDate || !toDate) {
    showStatus('error', 'Please select both a from date and a to date.');
    return;
  }

  setCookie('scotia_from_date', fromDate);
  setCookie('scotia_to_date', toDate);

  setFetching(true);
  showStatus('loading', 'Connecting to Scotiabank — logging in…');

  try {
    // Step 1: login
    const loginRes = await fetch(`${BASE}/scotia/login`, { method: 'POST' });

    if (!loginRes.ok) {
      const err = await loginRes.json().catch(() => ({ detail: `HTTP ${loginRes.status}` }));
      throw new Error(err.detail || `Login failed (HTTP ${loginRes.status})`);
    }

    const loginData = await loginRes.json();
    sessionId = loginData.session_id;

    // Login handles 2SV inline (waits for phone approval + clicks continue)
    showStatus('loading', 'Authenticated — downloading statements…');

    const res = await fetch(`${BASE}/scotia/scrape`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, from_date: fromDate, to_date: toDate }),
    });

    if (res.status === 404) {
      const err = await res.json().catch(() => ({ detail: 'Session not found — please try again.' }));
      throw new Error(err.detail || 'Session expired');
    }

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: `HTTP ${res.status} — ${res.statusText}` }));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }

    const blob = await res.blob();
    const filename = getFilenameFromResponse(res) || `scotiabank_${today()}.csv`;
    triggerDownload(blob, filename);
    showStatus('success', `File downloaded — ${filename}`);
  } catch (err) {
    showStatus('error', err.message);
  } finally {
    sessionId = null;
    setFetching(false);
  }
});

// ── State helpers ─────────────────────────────────────────────────
function setFetching(active) {
  isFetching = active;
  fetchBtn.disabled = active;
  fromDateEl.disabled = active;
  toDateEl.disabled = active;
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
