'use strict';

// Reads a /scrape response and turns it into files to save.
//
// One export comes back as the file itself. Several — a bank that exports one file per
// account — come back as a JSON envelope of base64 contents, which is unpacked into one
// download per file. Nothing here parses or rewrites a file: the bytes the bank wrote are
// the bytes that get saved.
//
// Each step records what it saw into `diag` and fails with the step it failed at, so a
// scrape that dies after a successful 200 says which half broke: a body that never
// finished arriving reads differently from one that arrived and could not be decoded.
window.ScrapeDownload = (function () {

  class StageError extends Error {
    constructor(stage, message, diag) {
      super(message);
      this.name = 'StageError';
      this.stage = stage;
      this.diag = { ...(diag || {}), stage };
    }
  }

  // `fallbackName` names a file the server did not: a single-file response with no
  // Content-Disposition, and any envelope entry missing a filename — numbered, so several
  // unnamed files can't land on top of each other.
  async function readScrapeResponse(res, diag, options) {
    const { fallbackName, mimeType = 'application/octet-stream' } = options || {};

    diag.stage = 'read-body';
    diag.status = res.status;
    diag.responseType = res.type;
    diag.contentType = res.headers.get('Content-Type') || '';
    diag.contentLength = res.headers.get('Content-Length');

    if (!diag.contentType.includes('application/json')) {
      const filename = getFilenameFromResponse(res) || fallbackName;
      const blob = await readBody(() => res.blob(), diag);
      diag.bytesRead = blob.size;
      return [{ name: filename, blob }];
    }

    const buffer = await readBody(() => res.arrayBuffer(), diag);
    diag.bytesRead = buffer.byteLength;
    if (diag.contentLength && Number(diag.contentLength) !== buffer.byteLength) {
      throw new StageError(
        'read-body',
        `The response was cut short — read ${buffer.byteLength} of ${diag.contentLength} bytes.`,
        diag,
      );
    }

    diag.stage = 'parse-json';
    let payload;
    try {
      payload = JSON.parse(new TextDecoder().decode(buffer));
    } catch (err) {
      throw new StageError(
        'parse-json',
        `The response was not valid JSON after reading ${buffer.byteLength} bytes: ${err.message}`,
        diag,
      );
    }

    const files = payload && payload.files;
    if (!Array.isArray(files) || !files.length) {
      throw new StageError('parse-json', 'The scrape returned no files.', diag);
    }

    diag.stage = 'decode-base64';
    diag.fileCount = files.length;
    return files.map((file, index) => {
      const name = file.filename || numbered(fallbackName, index);
      let bytes;
      try {
        bytes = base64ToBytes(file.content_base64);
      } catch (err) {
        throw new StageError('decode-base64', `Could not decode ${name}: ${err.message}`, diag);
      }
      return { name, blob: new Blob([bytes], { type: mimeType }) };
    });
  }

  async function readBody(read, diag) {
    try {
      return await read();
    } catch (err) {
      throw new StageError(
        'read-body',
        `The response body stopped arriving: ${err.message}. The scrape finished on the server, ` +
        'so the exports are on disk under the data directory.',
        diag,
      );
    }
  }

  function numbered(name, index) {
    const dot = String(name || 'export').lastIndexOf('.');
    if (dot <= 0) return `${name}_${index + 1}`;
    return `${name.slice(0, dot)}_${index + 1}${name.slice(dot)}`;
  }

  function base64ToBytes(base64) {
    const binary = atob(base64 || '');
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return bytes;
  }

  function getFilenameFromResponse(res) {
    const cd = res.headers.get('Content-Disposition') || '';
    const match = cd.match(/filename[^;=\n]*=\s*(?:["']([^"']+)["']|([^;\n]+))/i);
    return (match && (match[1] || match[2])?.trim()) || null;
  }

  // ── Saving ────────────────────────────────────────────────────────
  function triggerDownload(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = Object.assign(document.createElement('a'), { href: url, download: filename });
    document.body.appendChild(a);
    a.click();
    a.remove();
    // Revoked on a later tick: a batch of downloads fired back to back can still be
    // reading the object URL when the next one is created.
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  }

  // Browsers drop downloads fired within the same tick, so the batch is spaced out.
  async function saveAll(files) {
    for (let i = 0; i < files.length; i++) {
      if (i) await new Promise((resolve) => setTimeout(resolve, 400));
      triggerDownload(files[i].blob, files[i].name);
    }
  }

  // The browser cannot tell whether Chrome's "download multiple files" gate silently dropped
  // the ones after the first, so say what should have arrived and where the copies live.
  function describeSaved(files, label) {
    const noun = label || 'Activity';
    if (files.length === 1) {
      return `${noun} downloaded as ${files[0].name}.`;
    }
    return (
      `${noun} downloaded — ${files.length} files: ${files.map((f) => f.name).join(', ')}. ` +
      'If fewer than that were saved, allow multiple downloads for this site; a copy of every ' +
      'export is also kept on the server under the data directory.'
    );
  }

  return {
    StageError,
    readScrapeResponse,
    getFilenameFromResponse,
    triggerDownload,
    saveAll,
    describeSaved,
  };
})();
