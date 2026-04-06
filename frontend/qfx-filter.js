'use strict';

window.QfxFilter = (function () {

  // ── Helpers ──────────────────────────────────────────────────────
  function ymdToIso(yyyymmdd) {
    return yyyymmdd.slice(0, 4) + '-' + yyyymmdd.slice(4, 6) + '-' + yyyymmdd.slice(6, 8);
  }

  function isoToYmd(iso) {
    return iso.replace(/-/g, '');
  }

  function extractDtPosted(stmtTrnBlock) {
    const m = stmtTrnBlock.match(/<DTPOSTED>\s*(\d{8})/);
    return m ? m[1] : null;
  }

  // ── Parse full date range across all transactions ────────────────
  function parseRange(ofxText) {
    const re = /<DTPOSTED>\s*(\d{8})/g;
    let m, min = null, max = null;
    while ((m = re.exec(ofxText)) !== null) {
      const d = m[1];
      if (!min || d < min) min = d;
      if (!max || d > max) max = d;
    }
    return min && max ? { start: min, end: max } : null;
  }

  // ── Count transactions in a date range ───────────────────────────
  function countInRange(ofxText, startYMD, endYMD) {
    const re = /<STMTTRN>[\s\S]*?<\/STMTTRN>/gi;
    let m, count = 0;
    while ((m = re.exec(ofxText)) !== null) {
      const d = extractDtPosted(m[0]);
      if (d && d >= startYMD && d <= endYMD) count++;
    }
    return count;
  }

  // ── Filter OFX text, keeping only transactions in range ──────────
  function filterText(ofxText, startYMD, endYMD) {
    // Process each BANKTRANLIST section independently
    return ofxText.replace(
      /<BANKTRANLIST>([\s\S]*?)<\/BANKTRANLIST>/gi,
      function (fullMatch, inner) {
        // Update DTSTART / DTEND
        let updated = inner.replace(
          /(<DTSTART>)\s*\d{8,14}(\S*)/i,
          '$1' + startYMD + '120000$2'
        );
        updated = updated.replace(
          /(<DTEND>)\s*\d{8,14}(\S*)/i,
          '$1' + endYMD + '120000$2'
        );

        // Filter STMTTRN blocks
        updated = updated.replace(
          /<STMTTRN>[\s\S]*?<\/STMTTRN>/gi,
          function (txBlock) {
            const d = extractDtPosted(txBlock);
            if (!d) return txBlock; // keep if unparseable
            return (d >= startYMD && d <= endYMD) ? txBlock : '';
          }
        );

        return '<BANKTRANLIST>' + updated + '</BANKTRANLIST>';
      }
    );
  }

  // ── Initialize the filter UI ─────────────────────────────────────
  function initUI(containerEl, ofxText, filename) {
    const range = parseRange(ofxText);
    if (!range) {
      containerEl.hidden = true;
      return;
    }

    const startInput   = containerEl.querySelector('#filter-start');
    const endInput     = containerEl.querySelector('#filter-end');
    const summaryEl    = containerEl.querySelector('#filter-summary');
    const downloadBtn  = containerEl.querySelector('#filter-download-btn');

    startInput.value = ymdToIso(range.start);
    endInput.value   = ymdToIso(range.end);
    startInput.min   = ymdToIso(range.start);
    startInput.max   = ymdToIso(range.end);
    endInput.min     = ymdToIso(range.start);
    endInput.max     = ymdToIso(range.end);

    function updateSummary() {
      const s = isoToYmd(startInput.value);
      const e = isoToYmd(endInput.value);
      const total = countInRange(ofxText, range.start, range.end);
      const selected = countInRange(ofxText, s, e);
      summaryEl.textContent = selected + ' of ' + total + ' transactions selected';
    }

    updateSummary();
    startInput.addEventListener('change', updateSummary);
    endInput.addEventListener('change', updateSummary);

    // Replace any existing listener by cloning the button
    const newBtn = downloadBtn.cloneNode(true);
    downloadBtn.parentNode.replaceChild(newBtn, downloadBtn);

    newBtn.addEventListener('click', function () {
      const s = isoToYmd(startInput.value);
      const e = isoToYmd(endInput.value);
      const filtered = filterText(ofxText, s, e);
      const blob = new Blob([filtered], { type: 'application/octet-stream' });
      const url = URL.createObjectURL(blob);
      const a = Object.assign(document.createElement('a'), { href: url, download: filename });
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    });

    containerEl.hidden = false;
  }

  return { parseRange, countInRange, filterText, initUI };
})();
