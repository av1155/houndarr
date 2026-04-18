// Client controller for the "What's new" changelog modal.
// Listens for the HX-Trigger dispatched by /settings/changelog/popup and
// calls showModal() on the injected <dialog>.  Mirrors the AbortController
// cleanup pattern used in settings_content.html so HTMX partial swaps do
// not double-register listeners.
//
// The modal DOM is injected by HTMX replacing #changelog-slot; the buttons
// inside carry their own hx-post attributes, so dismiss/disable writes go
// through the standard HTMX pipeline (CSRF token added by app.js).  This
// file only handles open/close lifecycle and focus restoration.

(function () {
  if (window.__houndarrChangelogController) {
    window.__houndarrChangelogController.abort();
  }
  const controller = new AbortController();
  window.__houndarrChangelogController = controller;
  const { signal } = controller;

  const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const closeAnimationMs = 160;
  let previouslyFocused = null;
  let isClosing = false;

  function getDialog() {
    return document.getElementById('changelog-modal');
  }

  function openDialog() {
    const dialog = getDialog();
    if (!dialog || dialog.open) {
      return;
    }
    // Guard: never stack on top of another open dialog (e.g. the
    // instance-add modal on the Settings page).  The auto-open trigger
    // should lose cleanly when the admin is mid-task.
    const existingOpen = document.querySelector('dialog[open]');
    if (existingOpen && existingOpen !== dialog) {
      return;
    }
    previouslyFocused =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    isClosing = false;
    dialog.classList.remove('is-closing');
    dialog.showModal();
    document.body.style.overflow = 'hidden';
  }

  function restoreFocus() {
    if (previouslyFocused && document.contains(previouslyFocused)) {
      previouslyFocused.focus();
    }
    previouslyFocused = null;
  }

  function closeDialog() {
    const dialog = getDialog();
    if (!dialog || !dialog.open || isClosing) {
      return;
    }

    const finalize = () => {
      dialog.classList.remove('is-closing');
      if (dialog.open) {
        dialog.close();
      }
      document.body.style.overflow = '';
      isClosing = false;
      restoreFocus();
      // Replace the dialog with a fresh empty #changelog-slot so future
      // force-opens from Settings (or another auto-trigger after full
      // reload) still have an HTMX target to swap into.
      const slot = document.createElement('div');
      slot.id = 'changelog-slot';
      slot.setAttribute('aria-hidden', 'true');
      dialog.replaceWith(slot);
    };

    if (prefersReducedMotion) {
      finalize();
      return;
    }

    isClosing = true;
    dialog.classList.add('is-closing');
    window.setTimeout(finalize, closeAnimationMs);
  }

  // Server-triggered custom event (fired by HX-Trigger response header).
  document.body.addEventListener(
    'houndarr-show-changelog',
    function () {
      openDialog();
    },
    { signal },
  );

  // Native <dialog> Escape key fires a `cancel` event.  We preventDefault
  // so the close animation runs, then dispatch the dismiss POST through
  // HTMX by clicking the primary button (keeps persistence logic in one
  // place).
  document.body.addEventListener(
    'cancel',
    function (event) {
      const target = event.target;
      if (!(target instanceof HTMLDialogElement) || target.id !== 'changelog-modal') {
        return;
      }
      event.preventDefault();
      const dismissBtn = target.querySelector('[data-changelog-dismiss="true"]');
      if (dismissBtn instanceof HTMLElement) {
        dismissBtn.click();
      } else {
        closeDialog();
      }
    },
    { signal },
  );

  // Backdrop click (the <dialog> itself is the click target when the
  // backdrop is clicked, because the inner content stops propagation at
  // the dialog box).  Treat as equivalent to dismiss.
  document.body.addEventListener(
    'click',
    function (event) {
      const target = event.target;
      if (!(target instanceof Element)) {
        return;
      }

      // Close button inside the modal header
      if (target.closest('[data-close-changelog-modal="true"]')) {
        const dialog = getDialog();
        const dismissBtn = dialog?.querySelector('[data-changelog-dismiss="true"]');
        if (dismissBtn instanceof HTMLElement) {
          dismissBtn.click();
        } else {
          closeDialog();
        }
        return;
      }

      // Backdrop click: event.target is the <dialog> itself.
      if (target.id === 'changelog-modal' && target instanceof HTMLDialogElement) {
        const dismissBtn = target.querySelector('[data-changelog-dismiss="true"]');
        if (dismissBtn instanceof HTMLElement) {
          dismissBtn.click();
        } else {
          closeDialog();
        }
      }
    },
    { signal },
  );

  // After dismiss/disable POSTs complete (hx-swap="none"), close the
  // dialog.  htmx:afterRequest fires even on 204 responses.
  document.body.addEventListener(
    'htmx:afterRequest',
    function (evt) {
      const triggerEl = evt.detail?.elt;
      if (!(triggerEl instanceof Element)) {
        return;
      }
      if (
        triggerEl.matches('[data-changelog-dismiss="true"]') ||
        triggerEl.matches('[data-changelog-disable="true"]')
      ) {
        closeDialog();
      }
    },
    { signal },
  );
})();
