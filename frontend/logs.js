'use strict';

const BASE = '';

const selectEl = document.getElementById('log-file');
const outputEl = document.getElementById('log-output');
const refreshBtn = document.getElementById('refresh-btn');
const deleteBtn = document.getElementById('delete-btn');

const formatBytes = (n) => {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
};

const formatTime = (epoch) => new Date(epoch * 1000).toLocaleString();

async function fetchList(preserveSelection = false) {
  const previous = preserveSelection ? selectEl.value : null;
  const res = await fetch(`${BASE}/logs`);
  if (!res.ok) throw new Error(`Failed to list logs (${res.status})`);
  const data = await res.json();
  selectEl.innerHTML = '';
  if (!data.files.length) {
    const opt = document.createElement('option');
    opt.textContent = 'No log files found';
    opt.disabled = true;
    selectEl.appendChild(opt);
    outputEl.textContent = 'No log files found.';
    outputEl.classList.add('logs-empty');
    return;
  }
  for (const file of data.files) {
    const opt = document.createElement('option');
    opt.value = file.name;
    opt.textContent = `${file.name}  —  ${formatBytes(file.size)}  —  ${formatTime(file.mtime)}`;
    selectEl.appendChild(opt);
  }
  if (previous && data.files.some((f) => f.name === previous)) {
    selectEl.value = previous;
  }
  await fetchContent(selectEl.value);
}

async function fetchContent(name) {
  if (!name) return;
  outputEl.textContent = 'Loading…';
  outputEl.classList.add('logs-empty');
  const res = await fetch(`${BASE}/logs/content?name=${encodeURIComponent(name)}`);
  if (!res.ok) {
    outputEl.textContent = `Failed to load log (${res.status})`;
    return;
  }
  const text = await res.text();
  if (!text.trim()) {
    outputEl.textContent = '(file is empty)';
    outputEl.classList.add('logs-empty');
  } else {
    outputEl.textContent = text;
    outputEl.classList.remove('logs-empty');
    outputEl.scrollTop = outputEl.scrollHeight;
  }
}

async function deleteAll() {
  if (!confirm('Delete all log files? This will truncate the active log and remove rotated backups.')) {
    return;
  }
  const res = await fetch(`${BASE}/logs`, { method: 'DELETE' });
  if (!res.ok) {
    alert(`Failed to delete logs (${res.status})`);
    return;
  }
  await fetchList();
}

selectEl.addEventListener('change', (e) => fetchContent(e.target.value));
refreshBtn.addEventListener('click', () => fetchList(true));
deleteBtn.addEventListener('click', deleteAll);

fetchList().catch((err) => {
  outputEl.textContent = `Error: ${err.message}`;
});
