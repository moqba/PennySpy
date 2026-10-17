'use strict';

// Follows the server's background jobs: sign-in, code check and export each start a job,
// answer at once with its id, and report progress until they finish.
//
// The page listens on /jobs/{id}/events (server-sent events) and falls back to polling
// /jobs/{id} if the stream can't be opened or drops. A finished export is fetched with
// /exports/{id}/file — one ordinary browser download per file, so nothing big ever has to
// fit in a single long-held request again.
window.PennyJobs = (function () {

  const BASE = '';
  const POLL_MS = 1000;

  class JobError extends Error {
    constructor(message, job) {
      super(message);
      this.name = 'JobError';
      this.job = job || null;
    }
  }

  // POST that starts a job. Mistakes the server can see straight away — no saved login, an
  // expired session — come back here as an error, before any job exists.
  async function start(path, body) {
    const res = await fetch(`${BASE}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const error = new JobError(data.detail || `HTTP ${res.status} — ${res.statusText}`);
      error.status = res.status;
      throw error;
    }
    return data;
  }

  // Resolves with the finished job, or rejects with a JobError carrying it. `onUpdate` gets
  // every state the server reports, and again once a second so an elapsed timer keeps ticking.
  function follow(job, onUpdate) {
    return new Promise((resolve, reject) => {
      let latest = job;
      let source = null;
      let pollTimer = null;
      let done = false;

      const tick = setInterval(() => notify(latest), 1000);

      function notify(next) {
        try { onUpdate && onUpdate(next); } catch { /* a display problem must not stop the job */ }
      }

      function finish(next) {
        if (done) return;
        done = true;
        clearInterval(tick);
        clearTimeout(pollTimer);
        if (source) source.close();
        notify(next);
        if (next.state === 'failed') reject(new JobError(next.error || 'The job failed.', next));
        else resolve(next);
      }

      function receive(next) {
        if (done || next.version < latest.version) return;
        latest = next;
        if (next.state === 'done' || next.state === 'failed') finish(next);
        else notify(next);
      }

      async function poll() {
        if (done) return;
        try {
          const res = await fetch(`${BASE}/jobs/${latest.job_id}`);
          if (res.status === 404) {
            finish({ ...latest, state: 'failed', error: 'The server no longer knows this job — it may have restarted.' });
            return;
          }
          if (res.ok) receive(await res.json());
        } catch {
          // The server is unreachable for a moment; keep trying.
        }
        if (!done) pollTimer = setTimeout(poll, POLL_MS);
      }

      notify(latest);
      if (latest.state === 'done' || latest.state === 'failed') {
        finish(latest);
        return;
      }

      if (!('EventSource' in window)) {
        poll();
        return;
      }
      source = new EventSource(`${BASE}/jobs/${latest.job_id}/events`);
      source.onmessage = (event) => {
        try { receive(JSON.parse(event.data)); } catch { /* ignore a malformed event */ }
      };
      source.onerror = () => {
        // Left alone, EventSource reconnects forever, so polling takes over instead.
        if (done) return;
        source.close();
        source = null;
        poll();
      };
    });
  }

  async function run(path, body, onUpdate) {
    return follow(await start(path, body), onUpdate);
  }

  // "Exporting from Wealthsimple · 42s — Waiting for the export to finish"
  function describe(job) {
    const parts = [job.stage || 'Working'];
    const since = job.started_at || job.created_at;
    if (since && job.state !== 'done' && job.state !== 'failed') {
      const seconds = Math.max(0, Math.round((Date.now() - Date.parse(since)) / 1000));
      parts[0] += ` · ${seconds}s`;
    }
    if (job.detail && job.state !== 'done') parts.push(job.detail);
    return parts.join(' — ');
  }

  // ── Saving ────────────────────────────────────────────────────────
  // Each file is a plain link to /exports/{id}/file, so the browser downloads it itself.
  function triggerDownload(url, filename) {
    const a = Object.assign(document.createElement('a'), { href: `${BASE}${url}`, download: filename });
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  // Browsers drop downloads fired within the same tick, so the batch is spaced out.
  async function saveExport(job) {
    const files = job.files || [];
    if (!files.length) throw new JobError('The export finished without any files.', job);
    for (let i = 0; i < files.length; i++) {
      if (i) await new Promise((resolve) => setTimeout(resolve, 400));
      triggerDownload(files[i].url, files[i].filename);
    }
    return files;
  }

  // The browser cannot tell whether Chrome's "download multiple files" gate silently dropped
  // the ones after the first, so say what should have arrived and where the copies live.
  // `notes` are the server's remarks on the export, such as that it starts later than asked.
  function describeSaved(files, label, notes) {
    const noun = label || 'Activity';
    const message = files.length === 1
      ? `${noun} downloaded as ${files[0].filename}.`
      : `${noun} downloaded — ${files.length} files: ${files.map((f) => f.filename).join(', ')}. ` +
        'If fewer than that were saved, allow multiple downloads for this site; a copy of every ' +
        'export is also kept on the server under the data directory.';
    return [message, ...(notes || [])].join(' ');
  }

  return { JobError, start, follow, run, describe, saveExport, describeSaved };
})();
