// Settings page controller. initSettingsPage() re-runs every time
// HTMX swaps the Settings partial into #app-content; the outer
// AbortController aborts the previous binding so every listener
// (add-instance modal, test-connection, confirm dialog, admin
// dropdown, flash toast, preferences switch) tears down before a
// new one is wired.

function initSettingsPage() {
  window.__houndarrSettingsPageController?.abort();
  const controller = new AbortController();
  window.__houndarrSettingsPageController = controller;
  const { signal } = controller;

  (function () {
    const addInstanceBtn = document.getElementById('add-instance-btn');
    const addInstanceModal = document.getElementById('add-instance-modal');
    const addInstanceModalContent = document.getElementById('add-instance-modal-content');
    const addInstanceModalTitle = document.getElementById('add-instance-modal-title');
    const addInstanceModalSubtitle = document.getElementById('add-instance-modal-subtitle');
    const closeAnimationMs = 160;
    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    let pendingModalShow = false;
    let connectionStatusTimer = null;

    if (!addInstanceBtn || !addInstanceModal || !addInstanceModalContent) {
      return;
    }

    function flashUpdatedInstanceRow(row) {
      if (!row) {
        return;
      }
      row.classList.remove('just-updated');
      void row.offsetWidth;
      row.classList.add('just-updated');
      window.setTimeout(() => {
        row.classList.remove('just-updated');
      }, 450);
    }

    function setAddInstanceModalChrome(mode, instanceName) {
      if (mode === 'edit') {
        addInstanceModalTitle.textContent = 'Edit Instance';
        addInstanceModalSubtitle.textContent = instanceName
          ? `Update settings for ${instanceName}.`
          : 'Update instance settings.';
        return;
      }

      addInstanceModalTitle.textContent = 'Add Instance';
      addInstanceModalSubtitle.textContent = 'Configure an *arr instance.';
    }

    function getModalForm() {
      return addInstanceModalContent.querySelector('form[data-form-mode]');
    }

    function getModalFormMode() {
      const modalForm = getModalForm();
      return modalForm ? modalForm.dataset.formMode : null;
    }

    function getConnectionTestButton() {
      return addInstanceModalContent.querySelector('#instance-test-connection-btn');
    }

    function isConnectionGuardedFormLoaded() {
      return Boolean(addInstanceModalContent.querySelector('#instance-connection-verified'));
    }

    function syncAddInstancePlaceholders() {
      const typeSelect = addInstanceModalContent.querySelector('[data-instance-field="type"]');
      const nameInput = addInstanceModalContent.querySelector('[data-instance-field="name"]');
      const urlInput = addInstanceModalContent.querySelector('[data-instance-field="url"]');

      if (!typeSelect || !nameInput || !urlInput) {
        return;
      }

      const t = typeSelect.value;
      nameInput.placeholder = nameInput.dataset[t + 'Placeholder'] || nameInput.dataset.sonarrPlaceholder;
      urlInput.placeholder = urlInput.dataset[t + 'Placeholder'] || urlInput.dataset.sonarrPlaceholder;
    }

    function syncAppOnlyControls() {
      const typeSelect = addInstanceModalContent.querySelector('[data-instance-field="type"]');
      if (!typeSelect) {
        return;
      }

      const t = typeSelect.value;
      const appOnlyAttrs = ['data-sonarr-only', 'data-lidarr-only', 'data-readarr-only', 'data-whisparr_v2-only', 'data-whisparr_v3-only'];
      appOnlyAttrs.forEach(function (attr) {
        const appType = attr.replace('data-', '').replace('-only', '');
        addInstanceModalContent.querySelectorAll('[' + attr + '="true"]').forEach(function (el) {
          if (!(el instanceof HTMLElement)) {
            return;
          }
          el.style.display = (t === appType) ? '' : 'none';
        });
      });
    }

    function addConnectionStatusClass(statusTone) {
      if (statusTone === 'success') {
        return 'text-xs text-green-400';
      }
      if (statusTone === 'error') {
        return 'text-xs text-red-400';
      }
      return 'text-xs text-slate-400';
    }

    function isConnectionVerified() {
      const verifiedInput = addInstanceModalContent.querySelector('#instance-connection-verified');
      return verifiedInput && verifiedInput.value === 'true';
    }

    function setConnectionState(isVerified, statusText, statusTone) {
      const verifiedInput = addInstanceModalContent.querySelector('#instance-connection-verified');
      const submitBtn = addInstanceModalContent.querySelector('#instance-submit-btn');
      const statusEl = addInstanceModalContent.querySelector('#instance-connection-status');

      if (verifiedInput) {
        verifiedInput.value = isVerified ? 'true' : 'false';
      }
      if (submitBtn) {
        submitBtn.disabled = !isVerified;
      }
      if (statusEl && statusText) {
        if (connectionStatusTimer) {
          window.clearTimeout(connectionStatusTimer);
          connectionStatusTimer = null;
        }

        if (prefersReducedMotion) {
          statusEl.textContent = statusText;
          statusEl.className = addConnectionStatusClass(statusTone);
          return;
        }

        statusEl.classList.add('is-updating');
        connectionStatusTimer = window.setTimeout(() => {
          statusEl.textContent = statusText;
          statusEl.className = addConnectionStatusClass(statusTone);
          requestAnimationFrame(() => {
            statusEl.classList.remove('is-updating');
          });
          connectionStatusTimer = null;
        }, 80);
      }
    }

    function setConnectionTestButtonState(state) {
      const testBtn = getConnectionTestButton();
      if (!testBtn) {
        return;
      }

      testBtn.classList.remove('is-testing', 'is-success', 'is-error');

      if (state === 'testing') {
        testBtn.classList.add('is-testing');
      }
      if (state === 'success') {
        testBtn.classList.add('is-success');
      }
      if (state === 'error') {
        void testBtn.offsetWidth;
        testBtn.classList.add('is-error');
      }
    }

    function neutralConnectionPromptForMode(mode) {
      if (mode === 'edit') {
        return 'Test connection before saving changes.';
      }
      return 'Test connection before adding this instance.';
    }

    function resetConnectionStateOnFieldChange(target) {
      const mode = getModalFormMode();
      if (!isConnectionGuardedFormLoaded() || mode === null) {
        return;
      }

      if (
        !target.matches(
          '[data-instance-field="type"], [data-instance-field="url"], form[data-form-mode] [name="api_key"]',
        )
      ) {
        return;
      }

      const actionLabel = mode === 'edit' ? 'Save Changes' : 'Add Instance';
      if (isConnectionVerified()) {
        setConnectionState(
          false,
          `Connection details changed. Test again to enable ${actionLabel}.`,
          'error',
        );
        setConnectionTestButtonState('neutral');
        return;
      }

      setConnectionState(false, neutralConnectionPromptForMode(mode), 'neutral');
      setConnectionTestButtonState('neutral');
    }

    function resetInstanceFormToDefaults() {
      if (!addInstanceModalContent) {
        return;
      }

      addInstanceModalContent.querySelectorAll('[data-default-value]').forEach(function (el) {
        if (!(el instanceof HTMLInputElement) && !(el instanceof HTMLSelectElement)) {
          return;
        }
        el.value = el.dataset.defaultValue || '';
        el.dispatchEvent(new Event('change', { bubbles: true }));
      });

      addInstanceModalContent.querySelectorAll('[data-default-checked]').forEach(function (el) {
        if (!(el instanceof HTMLInputElement)) {
          return;
        }
        el.checked = el.dataset.defaultChecked === '1';
        el.dispatchEvent(new Event('change', { bubbles: true }));
      });

      syncAppOnlyControls();
    }

    window.houndarrOpenAddInstanceModal = function () {
      addInstanceModal.classList.remove('is-closing');
      addInstanceModal.__hxClosing = false;
      pendingModalShow = !addInstanceModal.open;
      setAddInstanceModalChrome('add');
    };

    window.houndarrCloseAddInstanceModal = function () {
      hxCloseDialogAnimated(addInstanceModal, closeAnimationMs, () => {
        addInstanceModalContent.replaceChildren();
        pendingModalShow = false;
        setAddInstanceModalChrome('add');
        addInstanceBtn.focus();
      });
    };

    document.body.addEventListener(
      'click',
      function (event) {
        const target = event.target;
        if (!(target instanceof Element)) {
          return;
        }

        const openBtn = target.closest('[data-open-add-instance-modal="true"]');
        if (openBtn) {
          window.houndarrOpenAddInstanceModal();
          return;
        }

        const closeBtn = target.closest('[data-close-add-instance-modal="true"]');
        if (closeBtn) {
          window.houndarrCloseAddInstanceModal();
        }
      },
      { signal },
    );

    addInstanceModal.addEventListener(
      'cancel',
      function (event) {
        event.preventDefault();
        window.houndarrCloseAddInstanceModal();
      },
      { signal },
    );

    addInstanceModal.addEventListener(
      'click',
      function (event) {
        if (event.target === addInstanceModal) {
          window.houndarrCloseAddInstanceModal();
        }
      },
      { signal },
    );

    addInstanceModalContent.addEventListener(
      'change',
      function (event) {
        if (event.target.matches('[data-instance-field="type"]')) {
          syncAddInstancePlaceholders();
          syncAppOnlyControls();
        }
        resetConnectionStateOnFieldChange(event.target);
      },
      { signal },
    );

    addInstanceModalContent.addEventListener(
      'input',
      function (event) {
        resetConnectionStateOnFieldChange(event.target);
      },
      { signal },
    );

    addInstanceModalContent.addEventListener(
      'click',
      function (event) {
        const target = event.target;
        if (!(target instanceof Element)) {
          return;
        }
        const resetBtn = target.closest('[data-reset-instance-form="true"]');
        if (!resetBtn) {
          return;
        }
        event.preventDefault();
        resetInstanceFormToDefaults();
      },
      { signal },
    );

    document.body.addEventListener(
      'htmx:beforeRequest',
      function (evt) {
        const triggerEl = evt.detail.elt;
        if (!triggerEl || !triggerEl.matches('[data-test-connection-btn="true"]')) {
          return;
        }
        setConnectionTestButtonState('testing');
      },
      { signal },
    );

    document.body.addEventListener(
      'houndarr-connection-test-success',
      function () {
        if (!isConnectionGuardedFormLoaded() || getModalFormMode() === null) {
          return;
        }
        // Server response already contains the full message via HTMX swap;
        // only update form state (verified flag + submit button).
        setConnectionState(true, null, 'success');
        setConnectionTestButtonState('success');
      },
      { signal },
    );

    document.body.addEventListener(
      'houndarr-connection-test-failure',
      function () {
        if (!isConnectionGuardedFormLoaded()) {
          return;
        }
        // Server response already contains the specific error via HTMX swap;
        // only update form state.
        setConnectionState(false, null, 'error');
        setConnectionTestButtonState('error');
      },
      { signal },
    );

    document.body.addEventListener(
      'htmx:afterRequest',
      function (evt) {
        const triggerEl = evt.detail.elt;
        if (!triggerEl || !triggerEl.matches('[data-test-connection-btn="true"]')) {
          return;
        }

        const testBtn = getConnectionTestButton();
        if (!testBtn) {
          return;
        }
        testBtn.classList.remove('is-testing');
      },
      { signal },
    );

    document.body.addEventListener(
      'htmx:afterSwap',
      function (evt) {
        if (evt.detail.target.id === 'app-content') {
          document.body.style.overflow = '';
        }

        if (evt.detail.target.id === 'instance-tbody') {
          window.houndarrCloseAddInstanceModal();
        }

        if (evt.detail.target.id.startsWith('instance-row-')) {
          window.houndarrCloseAddInstanceModal();
        }

        if (evt.detail.target.id === 'admin-security') {
          // After the password-change form re-renders (success or
          // validation error) via the /settings/account/password
          // endpoint, return focus to the first password field so
          // keyboard users do not lose their place.
          const firstPwInput = document.querySelector('#current-password');
          if (firstPwInput) {
            firstPwInput.focus();
          }
        }

        if (evt.detail.target.id === 'add-instance-modal-content') {
          if (pendingModalShow) {
            pendingModalShow = false;
            addInstanceModal.showModal();
            document.body.style.overflow = 'hidden';
          }

          const modalForm = addInstanceModalContent.querySelector('form[data-form-mode]');
          if (modalForm) {
            setAddInstanceModalChrome(modalForm.dataset.formMode, modalForm.dataset.instanceName);
          }

          // Intentionally no auto-focus on the Name input. The dialog's
          // close button carries `autofocus` so the browser still has a
          // safe keyboard landing spot without stealing focus into a
          // text field the user may not want to type in.
          syncAddInstancePlaceholders();
          syncAppOnlyControls();

          if (isConnectionGuardedFormLoaded()) {
            setConnectionState(false, neutralConnectionPromptForMode(getModalFormMode()), 'neutral');
            setConnectionTestButtonState('neutral');
          }
        }
      },
      { signal },
    );

    document.body.addEventListener(
      'htmx:responseError',
      function () {
        pendingModalShow = false;
      },
      { signal },
    );

    document.body.addEventListener(
      'htmx:sendError',
      function () {
        pendingModalShow = false;
      },
      { signal },
    );
  })();

  /* Admin dropdown: collapse + expand animation, plus confirm-dialog
     wiring for destructive actions inside the dropdown. Animation falls
     back to an instant toggle when prefers-reduced-motion is set. */
  (function () {
    const panel = document.getElementById('admin-grouped');
    const body = document.getElementById('admin-body');
    const toggle = document.getElementById('admin-toggle');
    if (!panel || !body || !toggle) return;

    const DUR = 260;
    const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    let animating = false;

    // Always start collapsed. No persistence: refresh, re-login, and
    // HTMX navigation away-and-back all reset to closed so the page
    // leads with Instances. A stale localStorage flag from an earlier
    // build could still be around on returning users; clear it so the
    // key does not linger.
    try { localStorage.removeItem('houndarr.adminOpen'); } catch { /* ignore */ }
    panel.setAttribute('data-open', 'false');
    toggle.setAttribute('aria-expanded', 'false');
    body.style.transition = '';
    body.style.height = '0px';
    body.style.opacity = '0';

    toggle.addEventListener('click', () => {
      if (animating) return;
      const isOpen = panel.getAttribute('data-open') === 'true';
      setOpen(!isOpen);
    });

    function setOpen(shouldOpen) {
      if (prefersReduced) {
        panel.setAttribute('data-open', String(shouldOpen));
        toggle.setAttribute('aria-expanded', String(shouldOpen));
        body.style.transition = '';
        body.style.height = shouldOpen ? 'auto' : '0px';
        body.style.opacity = shouldOpen ? '1' : '0';
        return;
      }

      animating = true;
      const startHeight = body.getBoundingClientRect().height;
      panel.setAttribute('data-open', String(shouldOpen));
      toggle.setAttribute('aria-expanded', String(shouldOpen));

      if (shouldOpen) {
        body.style.transition = '';
        body.style.height = `${startHeight}px`;
        body.style.opacity = '0';
        body.style.height = 'auto';
        const endHeight = body.scrollHeight;
        body.style.height = `${startHeight}px`;
        requestAnimationFrame(() => {
          body.style.transition = `height ${DUR}ms var(--ease-station), opacity ${DUR}ms ease`;
          body.style.height = `${endHeight}px`;
          body.style.opacity = '1';
        });
        window.setTimeout(() => {
          body.style.transition = '';
          body.style.height = 'auto';
          animating = false;
        }, DUR);
      } else {
        body.style.transition = '';
        body.style.height = `${startHeight}px`;
        body.style.opacity = '1';
        requestAnimationFrame(() => {
          body.style.transition = `height ${DUR}ms var(--ease-station), opacity ${DUR}ms ease`;
          body.style.height = '0px';
          body.style.opacity = '0';
        });
        window.setTimeout(() => {
          body.style.transition = '';
          body.style.height = '0px';
          animating = false;
        }, DUR);
      }
    }
  })();

  /* ── Confirm dialog (Admin > Maintenance + Danger zone) ───── */
  (function () {
    const dialog = document.getElementById('confirm-dialog');
    const form = document.getElementById('confirm-form');
    if (!dialog || !form) return;

    const titleEl = document.getElementById('confirm-title');
    const bodyEl = document.getElementById('confirm-body');
    const bulletsEl = document.getElementById('confirm-bullets');
    const typedWrap = document.getElementById('confirm-typed');
    const phraseInput = document.getElementById('confirm-phrase-input');
    const secondFactorWrap = document.getElementById('confirm-second-factor');
    const confirmGo = document.getElementById('confirm-go');
    const iconEl = document.getElementById('confirm-icon');
    const FACTORY_PHRASE = 'RESET';

    // Kind-specific copy + endpoints.  The factory variant is the only
    // one that surfaces the typed-phrase + second-factor fields; neutral
    // actions just show the title / body / confirm button.
    const COPY = {
      instances: {
        tone: 'neutral',
        title: 'Reset policy settings?',
        body: 'Reverts policy settings (batch, cadence, cooldowns, cutoff, upgrade) to their factory defaults.',
        keep: 'Your instances, connections, and API keys are kept.',
        cta: 'Reset settings',
        endpoint: '/settings/admin/reset-instances',
        requiresTyped: false,
      },
      logs: {
        tone: 'neutral',
        title: 'Clear all logs?',
        body: 'Empties the Activity log on disk.  A single breadcrumb entry survives so you can see when the wipe happened.',
        keep: 'Instances, settings, and credentials are not affected.',
        cta: 'Clear logs',
        endpoint: '/settings/admin/clear-logs',
        requiresTyped: false,
      },
      factory: {
        tone: 'danger',
        title: 'Factory reset Houndarr?',
        body: 'This deletes the database and master-key file, then returns you to first-run state.',
        bullets: [
          'All instances, preferences, logs, and sessions are dropped.',
          'The master key is regenerated; existing backups of encrypted API keys become unreadable.',
        ],
        cta: 'Factory reset',
        endpoint: '/settings/admin/factory-reset',
        requiresTyped: true,
      },
    };

    function setIconTone(tone) {
      if (tone === 'danger') {
        iconEl.className =
          'w-9 h-9 rounded-inset grid place-items-center bg-danger-bg border border-danger-border text-danger shrink-0';
        confirmGo.className =
          'btn btn-soft btn-error px-3 py-2 text-sm font-medium';
      } else {
        iconEl.className =
          'w-9 h-9 rounded-inset grid place-items-center bg-surface-2 border border-border-default text-slate-300 shrink-0';
        confirmGo.className =
          'btn btn-primary px-3 py-2 text-sm font-medium';
      }
    }

    function makeBullet(text) {
      const li = document.createElement('li');
      li.className = 'flex gap-2';
      const marker = document.createElement('span');
      marker.className = 'text-slate-600 mt-0.5';
      marker.textContent = '\u2014';
      const bodyText = document.createElement('span');
      bodyText.textContent = text;
      li.append(marker, bodyText);
      return li;
    }

    function openFor(kind) {
      const copy = COPY[kind];
      if (!copy) return;
      form.setAttribute('hx-post', copy.endpoint);
      if (window.htmx && typeof window.htmx.process === 'function') {
        window.htmx.process(form);
      }
      setIconTone(copy.tone);
      titleEl.textContent = copy.title;
      bodyEl.textContent = copy.body;

      bulletsEl.replaceChildren();
      if (copy.bullets && copy.bullets.length) {
        bulletsEl.classList.remove('hidden');
        copy.bullets.forEach((b) => bulletsEl.appendChild(makeBullet(b)));
      } else if (copy.keep) {
        bulletsEl.classList.remove('hidden');
        const li = document.createElement('li');
        li.className = 'flex gap-2 text-slate-500';
        const marker = document.createElement('span');
        marker.className = 'text-slate-600 mt-0.5';
        marker.textContent = '\u2713';
        const t = document.createElement('span');
        t.textContent = copy.keep;
        li.append(marker, t);
        bulletsEl.appendChild(li);
      } else {
        bulletsEl.classList.add('hidden');
      }

      // Disable the typed-phrase + second-factor inputs when the action
      // does not require them. Disabled inputs are omitted from the form
      // payload, so if a future neutral-action handler started reading
      // the factory-only fields it would receive nothing instead of a
      // stale user-typed value.
      const secondInput = secondFactorWrap.querySelector('input');
      if (copy.requiresTyped) {
        typedWrap.classList.remove('hidden');
        secondFactorWrap.classList.remove('hidden');
        phraseInput.disabled = false;
        phraseInput.value = '';
        phraseInput.placeholder = FACTORY_PHRASE;
        if (secondInput) {
          secondInput.disabled = false;
          secondInput.value = '';
        }
        confirmGo.textContent = copy.cta;
        confirmGo.disabled = true;
      } else {
        typedWrap.classList.add('hidden');
        secondFactorWrap.classList.add('hidden');
        phraseInput.disabled = true;
        if (secondInput) secondInput.disabled = true;
        confirmGo.textContent = copy.cta;
        confirmGo.disabled = false;
      }

      dialog.classList.remove('hidden');
      setTimeout(() => {
        if (copy.requiresTyped) {
          phraseInput.focus();
        } else {
          confirmGo.focus();
        }
      }, 40);
    }

    function close() {
      dialog.classList.add('hidden');
      confirmGo.disabled = false;
      pendingCustomConfirm = null;
    }

    /* Generic confirm path: any HTMX button with `hx-confirm="..."` is
       routed through this shared dialog instead of the native
       window.confirm(). Optional data-confirm-title / -body / -cta /
       -tone attributes customize copy; falling back to the hx-confirm
       string for the body keeps existing markup working unchanged. */
    let pendingCustomConfirm = null;

    function openForCustom({ title, body, cta, tone, onConfirm }) {
      form.removeAttribute('hx-post');
      if (window.htmx && typeof window.htmx.process === 'function') {
        window.htmx.process(form);
      }
      setIconTone(tone === 'danger' ? 'danger' : 'neutral');
      titleEl.textContent = title;
      bodyEl.textContent = body;
      bulletsEl.replaceChildren();
      bulletsEl.classList.add('hidden');
      typedWrap.classList.add('hidden');
      secondFactorWrap.classList.add('hidden');
      phraseInput.disabled = true;
      const secondInput = secondFactorWrap.querySelector('input');
      if (secondInput) secondInput.disabled = true;
      confirmGo.textContent = cta;
      confirmGo.disabled = false;
      pendingCustomConfirm = onConfirm;
      dialog.classList.remove('hidden');
      setTimeout(() => confirmGo.focus(), 40);
    }

    /* Intercept the confirm-go submit when a custom callback is queued
       (generic hx-confirm flow). The admin reset flow leaves
       pendingCustomConfirm null, so the form submits via hx-post as
       before and this handler is a no-op. The dialog's DOM node is
       replaced on every HTMX swap into Settings (GC handles teardown),
       but we still bind with { signal } for consistency with the rest
       of this module and to be safe if the dialog ever becomes
       persistent. */
    confirmGo.addEventListener('click', (event) => {
      if (!pendingCustomConfirm) return;
      event.preventDefault();
      const cb = pendingCustomConfirm;
      pendingCustomConfirm = null;
      close();
      cb();
    }, { signal });

    document.body.addEventListener('click', (event) => {
      const openBtn = event.target.closest('[data-confirm-reset]');
      if (openBtn) {
        openFor(openBtn.getAttribute('data-confirm-reset'));
        return;
      }
      const dismissBtn = event.target.closest('[data-dismiss-confirm]');
      if (dismissBtn) {
        close();
      }
    }, { signal });

    /* Route `hx-confirm` through the shared dialog so every destructive
       HTMX action (Delete instance, etc.) gets the Station confirm UI
       instead of the native browser prompt. The { signal } options
       argument on addEventListener is what aborts the previous binding
       on the next initSettingsPage() re-run; without it every HTMX swap
       into Settings would leak a fresh handler onto document.body. */
    document.body.addEventListener('htmx:confirm', (evt) => {
      if (!evt.detail || !evt.detail.question) return;
      evt.preventDefault();
      const elt = evt.detail.elt;
      const isDestructive =
        elt.hasAttribute('hx-delete') ||
        (elt.getAttribute('data-confirm-tone') || '').toLowerCase() === 'danger';
      openForCustom({
        title:
          elt.getAttribute('data-confirm-title') ||
          (isDestructive ? 'Delete this item?' : 'Confirm action'),
        body: elt.getAttribute('data-confirm-body') || evt.detail.question,
        cta:
          elt.getAttribute('data-confirm-cta') ||
          (isDestructive ? 'Delete' : 'Confirm'),
        tone:
          elt.getAttribute('data-confirm-tone') ||
          (isDestructive ? 'danger' : 'neutral'),
        onConfirm: () => evt.detail.issueRequest(true),
      });
    }, { signal });

    // Typed phrase gating: enable Confirm only when the phrase matches.
    // Same rationale as the confirmGo click listener above for { signal }.
    if (phraseInput) {
      phraseInput.addEventListener('input', () => {
        confirmGo.disabled = phraseInput.value.trim() !== FACTORY_PHRASE;
      }, { signal });
    }

    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && !dialog.classList.contains('hidden')) {
        close();
      }
    }, { signal });

    // Close the dialog on any server response (success or flash-rendering
    // error) so the #admin-flash slot is visible to the user.
    document.body.addEventListener('htmx:afterRequest', (evt) => {
      if (!evt.detail || !evt.detail.elt) return;
      if (evt.detail.elt.closest('#confirm-dialog')) {
        close();
      }
    }, { signal });
  })();

  /* ── Admin flash toast auto-fade ─────────────────────────────
     The route returns a single .border-l-4 .tone-class div which HTMX
     innerHTML-swaps into the stable #admin-flash wrapper.  After 3.2s
     we fade the inner child to 0 opacity and clear it so the wrapper
     is ready for the next flash. */
  (function () {
    const FADE_MS = 3200;
    let fadeTimer = null;
    document.body.addEventListener('htmx:afterSwap', (evt) => {
      const wrapper = evt.detail && evt.detail.target;
      if (!wrapper || wrapper.id !== 'admin-flash') return;
      if (fadeTimer) window.clearTimeout(fadeTimer);
      const toast = wrapper.querySelector('[data-admin-flash]');
      if (!toast) return;
      fadeTimer = window.setTimeout(() => {
        toast.classList.add('is-fading');
        window.setTimeout(() => {
          if (toast.parentElement === wrapper) {
            wrapper.replaceChildren();
          }
        }, 320);
      }, FADE_MS);
    }, { signal });
  })();

  /* ── Switch rollback on /preferences error ───────────────────
     The #admin-updates changelog-popup switch flips visually the moment
     the user clicks the checkbox, before the form POST reaches the
     server. /settings/changelog/preferences normally returns 204, but if
     the write fails (DB locked, etc.) HTMX's config skips the swap for
     4xx/5xx, leaving the thumb in the user's desired position while the
     server state stayed the opposite. Listen for htmx:responseError
     scoped to that form and flip the checkbox back so the DOM stays in
     sync with what actually persisted. */
  (function () {
    document.body.addEventListener('htmx:responseError', (evt) => {
      const form = evt.detail && evt.detail.elt;
      if (!(form instanceof HTMLFormElement)) return;
      if (!form.closest('#admin-updates')) return;
      const checkbox = form.querySelector('input[type="checkbox"][name="enabled"]');
      if (!checkbox) return;
      checkbox.checked = !checkbox.checked;
    }, { signal });
  })();

}

// Direct load (e.g. the Settings page is the initial URL).
if (document.querySelector('[data-page-key="settings"]')) {
  initSettingsPage();
}

// HTMX navigation into the Settings page.
document.body.addEventListener('htmx:afterSwap', (evt) => {
  if (
    evt.detail?.target?.id === 'app-content' &&
    document.querySelector('[data-page-key="settings"]')
  ) {
    initSettingsPage();
  }
});
