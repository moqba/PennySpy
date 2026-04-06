'use strict';

const BASE = 'http://localhost:5056';

// ── Cookie helpers ────────────────────────────────────────────────
const setCookie = (name, value) =>
  document.cookie = `${name}=${encodeURIComponent(value)};max-age=31536000;path=/`;

const getCookie = (name) => {
  const match = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
  return match ? decodeURIComponent(match[1]) : null;
};

const SOFTWARE_EXTENSION = {
  EXCEL: 'csv',
  QUICKEN: 'ofx',
  QUICKBOOKS: 'qbo',
  MONEY: 'ofx',
  MAKISOFT: 'afx',
  MAKISOFTB: 'afx',
  SIMPLYACCOUNTING: 'aso',
};

let isFetching = false;

const fetchBtn = document.getElementById('fetch-btn');
const statusEl = document.getElementById('status');

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

  const software     = document.getElementById('software').value;
  const account_info = document.getElementById('account_info').value;
  const include      = document.getElementById('include').value;

  setCookie('rbc_software', software);
  setCookie('rbc_account_info', account_info);
  setCookie('rbc_include', include);

  setFetching(true);
  showStatus('loading', 'Connecting to RBC — approve 2FA on your mobile device…');

  try {
    const url = `${BASE}/rbc/scrape?software=${encodeURIComponent(software)}&account_info=${encodeURIComponent(account_info)}&include=${encodeURIComponent(include)}`;
    const res = await fetch(url);

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: `HTTP ${res.status} — ${res.statusText}` }));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }

    const blob = await res.blob();
    const ext = SOFTWARE_EXTENSION[software] || 'dat';
    const filename = getFilenameFromResponse(res) || `rbc_${software.toLowerCase()}_${today()}.${ext}`;
    triggerDownload(blob, filename);
    showStatus('success', `File downloaded — ${filename}`);

    const isOfx = ['QUICKEN', 'MONEY'].includes(software);
    if (isOfx) {
      const ofxText = await blob.text();
      QfxFilter.initUI(document.getElementById('qfx-filter-section'), ofxText, filename);
    }
  } catch (err) {
    showStatus('error', err.message);
  } finally {
    setFetching(false);
  }
});

// ── State helpers ─────────────────────────────────────────────────
function setFetching(active) {
  isFetching = active;
  fetchBtn.disabled = active;

  const selects = document.querySelectorAll('select');
  selects.forEach(s => { s.disabled = active; });
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
  const software     = getCookie('rbc_software');
  const account_info = getCookie('rbc_account_info');
  const include      = getCookie('rbc_include');
  if (software)     document.getElementById('software').value     = software;
  if (account_info) document.getElementById('account_info').value = account_info;
  if (include)      document.getElementById('include').value      = include;
})();
