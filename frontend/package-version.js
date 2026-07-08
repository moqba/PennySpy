'use strict';

(function () {
  const VERSION_URL = (typeof BASE !== 'undefined' ? BASE : '') + '/version';

  function markdownToText(markdown) {
    return String(markdown || '')
      .replace(/^#{1,6}\s+/gm, '')
      .replace(/\*\*(.*?)\*\*/g, '$1')
      .replace(/`([^`]+)`/g, '$1')
      .replace(/^\s*[-*]\s+/gm, '- ')
      .trim();
  }

  function closeReleaseModal(modal) {
    modal.remove();
    document.removeEventListener('keydown', modal._onKeyDown);
  }

  function openReleaseModal(release) {
    const existing = document.querySelector('.release-modal');
    if (existing) closeReleaseModal(existing);

    const modal = document.createElement('div');
    modal.className = 'release-modal';
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    modal.setAttribute('aria-labelledby', 'release-modal-title');

    const dialog = document.createElement('div');
    dialog.className = 'release-modal__dialog';

    const header = document.createElement('div');
    header.className = 'release-modal__header';

    const titleWrap = document.createElement('div');
    const eyebrow = document.createElement('div');
    eyebrow.className = 'release-modal__eyebrow';
    eyebrow.textContent = 'Change log';

    const title = document.createElement('h2');
    title.id = 'release-modal-title';
    title.className = 'release-modal__title';
    title.textContent = release.release_name || `PennySpy v${release.latest_version}`;

    titleWrap.append(eyebrow, title);

    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'release-modal__close';
    close.setAttribute('aria-label', 'Close change log');
    close.textContent = 'x';
    close.addEventListener('click', () => closeReleaseModal(modal));

    header.append(titleWrap, close);

    const body = document.createElement('pre');
    body.className = 'release-modal__notes';
    body.textContent = markdownToText(release.release_notes) || 'No release notes were published for this version.';

    const footer = document.createElement('div');
    footer.className = 'release-modal__footer';

    if (release.release_url) {
      const link = document.createElement('a');
      link.className = 'release-modal__link';
      link.href = release.release_url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = 'Open on GitHub';
      footer.append(link);
    }

    dialog.append(header, body, footer);
    modal.append(dialog);
    modal.addEventListener('click', (event) => {
      if (event.target === modal) closeReleaseModal(modal);
    });
    modal._onKeyDown = (event) => {
      if (event.key === 'Escape') closeReleaseModal(modal);
    };
    document.addEventListener('keydown', modal._onKeyDown);
    document.body.append(modal);
    close.focus();
  }

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
        const updateText = document.createElement('span');
        updateText.textContent = `New version available: v${body.latest_version}`;

        const changeLogButton = document.createElement('button');
        changeLogButton.type = 'button';
        changeLogButton.className = 'package-update__button';
        changeLogButton.textContent = 'Change log';
        changeLogButton.addEventListener('click', () => openReleaseModal(body));

        update.replaceChildren(updateText, changeLogButton);
        update.hidden = false;
      }
    } catch {
      version.textContent = 'PennySpy version unavailable';
    }
  }

  loadVersion();
})();
