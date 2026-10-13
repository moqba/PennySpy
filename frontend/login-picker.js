'use strict';

// The "Login" dropdown on each bank page. It lists the bank's saved logins by label and
// partly hidden username (the server never sends more), remembers the last choice by id, and
// hands the chosen id to /<bank>/login as `credential_id`.
//
// With nothing saved, the dropdown offers the server's env-var login instead, which is what
// the server falls back to when no `credential_id` is sent.

window.LoginPicker = (() => {
  const ENV_OPTION = '';

  const cookieName = (bank) => `${bank}_credential_id`;

  const readCookie = (name) => {
    const match = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
    return match ? decodeURIComponent(match[1]) : null;
  };

  const writeCookie = (name, value) => {
    document.cookie = `${name}=${encodeURIComponent(value)};max-age=31536000;path=/`;
  };

  function option(value, text) {
    const el = document.createElement('option');
    el.value = value;
    el.textContent = text;
    return el;
  }

  async function mount(bank) {
    const select = document.getElementById('credential_id');
    const hint = document.getElementById('credential-hint');
    select.addEventListener('change', () => writeCookie(cookieName(bank), select.value));

    let logins = [];
    try {
      const res = await fetch(`/credentials?bank=${encodeURIComponent(bank)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      logins = await res.json();
    } catch (e) {
      hint.textContent = `Could not load saved logins (${e.message}).`;
    }

    select.replaceChildren();
    if (!logins.length) {
      select.appendChild(option(ENV_OPTION, 'Server environment login'));
      select.disabled = true;
      hint.innerHTML = 'No saved login for this bank — <a href="credentials.html">add one on the Logins page</a>.';
      return;
    }

    for (const login of logins) {
      select.appendChild(option(login.id, `${login.label} (${login.username_hint})`));
    }
    select.disabled = logins.length === 1;
    const saved = readCookie(cookieName(bank));
    if (saved && logins.some((l) => l.id === saved)) select.value = saved;
    hint.innerHTML = '<a href="credentials.html">Manage logins</a>';
  }

  // The `credential_id` to send, or nothing when the server should pick on its own.
  function selected() {
    const value = document.getElementById('credential_id').value;
    return value ? { credential_id: value } : {};
  }

  return { mount, selected };
})();
