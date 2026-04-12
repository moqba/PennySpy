'use strict';

(function () {
  const HEALTH_URL = (typeof BASE !== 'undefined' ? BASE : 'http://localhost:5056') + '/health';
  const POLL_INTERVAL = 15_000;

  let alertEl = null;
  let serverDown = false;

  function createAlert() {
    const el = document.createElement('div');
    el.className = 'health-alert';
    el.hidden = true;
    el.innerHTML =
      '<span class="health-alert__dot"></span>' +
      'Server is not responding';
    document.body.prepend(el);
    return el;
  }

  async function checkHealth() {
    try {
      const res = await fetch(HEALTH_URL, { method: 'GET', signal: AbortSignal.timeout(5000) });
      if (!res.ok) throw new Error(res.status);
      if (serverDown) {
        serverDown = false;
        alertEl.hidden = true;
      }
    } catch {
      if (!serverDown) {
        serverDown = true;
        alertEl.hidden = false;
      }
    }
  }

  alertEl = createAlert();
  checkHealth();
  setInterval(checkHealth, POLL_INTERVAL);
})();
