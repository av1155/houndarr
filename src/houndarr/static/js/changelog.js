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

  // Native <dialog> Escape key fires a `cancel` event that does NOT bubble,
  // so we listen in the capture phase (which still reaches non-bubbling
  // events on descendants) on document.  preventDefault so the close
  // animation runs, then dispatch the dismiss POST through HTMX by
  // clicking the primary button (keeps persistence logic in one place).
  document.addEventListener(
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
    { signal, capture: true },
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

  // Animated <details> accordion for older releases.  Native <details> has
  // no open/close animation, so we intercept the summary click, handle the
  // open attribute ourselves, and animate the body's height + opacity.
  // The Station ease (heavy ease-out) makes the motion feel snappy at
  // small distances; for the full older-releases panel (often 1500+ px)
  // it reads as "instant" because 75% of the height change happens in
  // the first 100ms.  A balanced standard easing at 480ms gives the
  // expand a deliberate, controlled feel without being sluggish.
  const accordionAnimationMs = 480;
  const accordionEase = 'cubic-bezier(0.4, 0, 0.2, 1)';

  function animateAccordion(details, body, shouldOpen) {
    if (prefersReducedMotion) {
      details.open = shouldOpen;
      body.style.height = '';
      body.style.opacity = '';
      body.style.transition = '';
      return;
    }

    if (body.dataset.animating === '1') {
      return;
    }
    body.dataset.animating = '1';

    const transitionValue =
      `height ${accordionAnimationMs}ms ${accordionEase}, ` +
      `opacity ${accordionAnimationMs}ms ${accordionEase}`;

    if (shouldOpen) {
      // Lock the collapsed state INLINE before flipping details.open so
      // the browser never paints a frame of the natural-height content.
      body.style.transition = 'none';
      body.style.height = '0px';
      body.style.opacity = '0';
      details.open = true;
      // Two RAFs: first lets the browser commit the display:block + 0px
      // inline height.  Second starts the transition from that committed
      // state to the natural height.  A single RAF is not enough because
      // the display change from `<details>` toggling needs a style recalc
      // that the browser may defer.
      window.requestAnimationFrame(() => {
        window.requestAnimationFrame(() => {
          const targetHeight = body.scrollHeight;
          body.style.transition = transitionValue;
          body.style.height = `${targetHeight}px`;
          body.style.opacity = '1';
          window.setTimeout(() => {
            body.style.transition = '';
            body.style.height = 'auto';
            body.style.opacity = '';
            body.dataset.animating = '0';
          }, accordionAnimationMs);
        });
      });
      return;
    }

    // Closing: lock current height, reflow, transition to 0.
    const startHeight = body.getBoundingClientRect().height;
    body.style.transition = 'none';
    body.style.height = `${startHeight}px`;
    body.style.opacity = '1';
    void body.offsetHeight;
    body.style.transition = transitionValue;
    body.style.height = '0px';
    body.style.opacity = '0';
    window.setTimeout(() => {
      details.open = false;
      body.style.transition = '';
      body.style.height = '';
      body.style.opacity = '';
      body.dataset.animating = '0';
    }, accordionAnimationMs);
  }

  document.addEventListener(
    'click',
    function (event) {
      const target = event.target;
      if (!(target instanceof Element)) {
        return;
      }
      const summary = target.closest('.changelog-accordion > summary');
      if (!(summary instanceof HTMLElement)) {
        return;
      }
      const details = summary.parentElement;
      if (!(details instanceof HTMLDetailsElement)) {
        return;
      }
      const body = details.querySelector('.changelog-accordion-body');
      if (!(body instanceof HTMLElement)) {
        return;
      }
      event.preventDefault();
      animateAccordion(details, body, !details.open);
    },
    { signal },
  );
})();
