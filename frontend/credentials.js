'use strict';

// Manages the saved bank logins. The server never sends a username or password back, only a
// label and a partly hidden username, so editing starts from blank fields: whatever is left
// blank keeps its saved value.

const BASE = '';

const BANK_NAMES = { bmo: 'BMO', rbc: 'RBC', scotia: 'Scotiabank', ws: 'Wealthsimple' };

const listEl    = document.getElementById('login-list');
const form      = document.getElementById('login-form');
const bankEl    = document.getElementById('bank');
const labelEl   = document.getElementById('label');
const userEl    = document.getElementById('username');
const passEl    = document.getElementById('password');
const saveBtn   = document.getElementById('save-btn');
const cancelBtn = document.getElementById('cancel-btn');
const editHint  = document.getElementById('edit-hint');
const formBadge = document.getElementById('form-badge');
const formTitle = document.getElementById('form-title');
const statusEl  = document.getElementById('status');

let logins    = [];
let editingId = null;

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

function showStatus(type, message) {
  statusEl.hidden = false;
  statusEl.className = `status status--${type}`;
  const icon = type === 'error' ? '<span class="status__icon">✕</span>'
    : type === 'success' ? '<span class="status__icon">✓</span>'
    : '<span class="spinner"></span>';
  statusEl.innerHTML = `${icon}<span class="status__text">${escapeHtml(message)}</span>`;
}

async function request(method, path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
    const detail = Array.isArray(err.detail) ? err.detail.map((d) => d.msg).join('; ') : err.detail;
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return res.status === 204 ? null : res.json();
}

// ── List ──────────────────────────────────────────────────────────
function render() {
  if (!logins.length) {
    listEl.innerHTML = '<p class="logs-empty">No saved logins yet. Add one below.</p>';
    return;
  }
  const rows = Object.keys(BANK_NAMES).flatMap((bank) =>
    logins.filter((l) => l.bank === bank).map((l) => `
      <tr>
        <td><span class="card__badge card__badge--${bank}">${BANK_NAMES[bank]}</span></td>
        <td>${escapeHtml(l.label)}</td>
        <td class="login-list__hint">${escapeHtml(l.username_hint)}</td>
        <td class="login-list__date">${escapeHtml(l.updated_at.slice(0, 10))}</td>
        <td class="login-list__actions">
          <button type="button" class="btn btn--ghost btn--small" data-edit="${l.id}">Edit</button>
          <button type="button" class="btn btn--ghost btn--small" data-remove="${l.id}">Remove</button>
        </td>
      </tr>`));
  listEl.innerHTML = `
    <table class="login-table">
      <thead><tr><th>Bank</th><th>Label</th><th>Username</th><th>Updated</th><th></th></tr></thead>
      <tbody>${rows.join('')}</tbody>
    </table>`;
}

async function load() {
  try {
    logins = await request('GET', '/credentials');
    render();
  } catch (e) {
    listEl.innerHTML = '';
    showStatus('error', `Could not load saved logins: ${e.message}`);
  }
}

// ── Form ──────────────────────────────────────────────────────────
function resetForm() {
  editingId = null;
  form.reset();
  bankEl.disabled = false;
  userEl.placeholder = '';
  passEl.placeholder = '';
  editHint.hidden = true;
  cancelBtn.hidden = true;
  formBadge.textContent = 'Add';
  formTitle.textContent = 'Save a new login';
  saveBtn.textContent = 'Save Login';
}

function startEdit(id) {
  const login = logins.find((l) => l.id === id);
  if (!login) return;
  resetForm();
  editingId = id;
  bankEl.value = login.bank;
  bankEl.disabled = true;
  labelEl.value = login.label;
  userEl.placeholder = `unchanged (${login.username_hint})`;
  passEl.placeholder = 'unchanged';
  editHint.hidden = false;
  cancelBtn.hidden = false;
  formBadge.textContent = 'Edit';
  formTitle.textContent = `Edit ${BANK_NAMES[login.bank]} login “${login.label}”`;
  saveBtn.textContent = 'Save Changes';
  labelEl.focus();
}

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const label = labelEl.value.trim();
  const username = userEl.value.trim();
  const password = passEl.value;

  saveBtn.disabled = true;
  try {
    if (editingId) {
      const body = { label };
      if (username) body.username = username;
      if (password) body.password = password;
      await request('PUT', `/credentials/${editingId}`, body);
      showStatus('success', `Login “${label}” updated.`);
    } else {
      if (!label || !username || !password) {
        throw new Error('Label, username and password are all required.');
      }
      await request('POST', '/credentials', { bank: bankEl.value, label, username, password });
      showStatus('success', `${BANK_NAMES[bankEl.value]} login “${label}” saved.`);
    }
    resetForm();
    await load();
  } catch (err) {
    showStatus('error', err.message);
  } finally {
    // Never leave a typed password sitting in the field after a save attempt.
    passEl.value = '';
    saveBtn.disabled = false;
  }
});

cancelBtn.addEventListener('click', resetForm);

listEl.addEventListener('click', async (e) => {
  const editId = e.target.dataset.edit;
  const removeId = e.target.dataset.remove;
  if (editId) startEdit(editId);
  if (!removeId) return;

  const login = logins.find((l) => l.id === removeId);
  if (!login || !confirm(`Remove the ${BANK_NAMES[login.bank]} login “${login.label}”?`)) return;
  try {
    await request('DELETE', `/credentials/${removeId}`);
    if (editingId === removeId) resetForm();
    showStatus('success', `Login “${login.label}” removed.`);
    await load();
  } catch (err) {
    showStatus('error', err.message);
  }
});

load();
