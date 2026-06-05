'use strict';

(function () {
  const VERSION_URL = (typeof BASE !== 'undefined' ? BASE : '') + '/version';

  function createVersionPanel() {
    const panel = document.createElement('div');
    panel.className = 'package-version-panel';

    const update = document.createElement('div');
    update.className = 'package-update';
    update.hidden = true;

    const version = document.createElement('div');
    version.className = 'package-version';
    version.textContent = 'PennySpy';

    panel.append(update, version);
    document.body.append(panel);
    return { update, version };
  }

  async function loadVersion() {
    const { update, version } = createVersionPanel();

    try {
      const res = await fetch(VERSION_URL, { method: 'GET', signal: AbortSignal.timeout(5000) });
      if (!res.ok) throw new Error(res.status);

      const body = await res.json();
      if (body.version) {
        version.textContent = `PennySpy v${body.version}`;
      }

      if (body.update_available && body.latest_version) {
        update.textContent = `New version available: v${body.latest_version}`;
        update.hidden = false;
      }
    } catch {
      version.textContent = 'PennySpy version unavailable';
    }
  }

  loadVersion();
})();
