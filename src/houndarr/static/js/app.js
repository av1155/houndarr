// houndarrClientHelpers (including formatLocalTimestamp) is defined in base.html <head>
// so it is available on both initial page load and HTMX navigation.
// The overlay scrollbar module lives in hx-scrollbar.js.

(function () {
  function getCsrfToken() {
    const match = document.cookie.match(/(?:^|;\s*)houndarr_csrf=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : '';
  }

  if (document.body) {
    document.body.setAttribute('hx-headers', JSON.stringify({ 'X-CSRF-Token': getCsrfToken() }));
  }
})();

(function () {
  // Cancel any HTMX request that begins after the page has started
  // unloading.  Without this guard, the changelog-popup hx-trigger
  // (load once delay:800ms in base.html) and any other deferred
  // request can fire mid-navigation when HX-Refresh calls
  // location.reload() during a password change or factory reset.
  // Webkit logs the resulting aborted fetch as a "due to access
  // control checks" pageerror plus htmx:afterRequest / htmx:sendError
  // console errors; chromium and firefox swallow the abort silently.
  // Aborting before the request leaves the browser keeps every engine
  // quiet without weakening the autouse console_guard fixture in the
  // browser-e2e suite.
  let isUnloading = false;
  window.addEventListener('pagehide', function () {
    isUnloading = true;
  });
  document.body.addEventListener('htmx:beforeRequest', function (evt) {
    if (isUnloading) {
      evt.preventDefault();
    }
  });
})();

(function () {
  const TOAST_VISIBLE_MS = 2400;
  const TOAST_LEAVE_MS = 240;
  let toastTimer = null;

  function showToast(message) {
    const toast = document.getElementById('toast');
    if (!toast || !message) return;
    const label = toast.querySelector('.toast__label');
    if (label) label.textContent = message;
    if (toastTimer !== null) {
      clearTimeout(toastTimer);
      toastTimer = null;
    }
    toast.classList.remove('is-leaving');
    toast.hidden = false;
    toastTimer = window.setTimeout(function () {
      toast.classList.add('is-leaving');
      toastTimer = window.setTimeout(function () {
        toast.hidden = true;
        toast.classList.remove('is-leaving');
        toastTimer = null;
      }, TOAST_LEAVE_MS);
    }, TOAST_VISIBLE_MS);
  }

  window.houndarrShowToast = showToast;

  document.body.addEventListener('houndarr-toast', function (evt) {
    const payload = evt.detail;
    let msg = '';
    if (typeof payload === 'string') msg = payload;
    else if (payload && typeof payload.value === 'string') msg = payload.value;
    else if (payload && typeof payload.message === 'string') msg = payload.message;
    if (msg) showToast(msg);
  });

  function consumeFlashCookie() {
    const match = document.cookie.match(/(?:^|;\s*)houndarr_flash=([^;]*)/);
    if (!match) return;
    let raw = match[1];
    document.cookie = 'houndarr_flash=; Max-Age=0; path=/';
    if (!raw) return;
    if (raw.length >= 2 && raw.startsWith('"') && raw.endsWith('"')) {
      raw = raw.slice(1, -1);
    }
    let msg = '';
    try { msg = decodeURIComponent(raw); } catch { msg = raw; }
    if (msg) showToast(msg);
  }
  consumeFlashCookie();
})();

(function () {
  const reducedMotionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
  const navLinks = () =>
    Array.from(document.querySelectorAll('[data-shell-nav="true"][data-shell-route]'));
  let shellEnterTimer = null;

  function routeIsActive(route, currentPath) {
    if (route === '/') {
      return currentPath === '/';
    }
    return currentPath === route || currentPath.startsWith(`${route}/`);
  }

  function syncShellNavState() {
    // Two variants share the same data-shell-nav selector but use
    // different active-state class sets: the pill tabs in the desktop
    // header flip a single BEM modifier, while the mobile drawer rows
    // keep the original Tailwind utility swap. Branch on the nearest
    // .pill-nav ancestor so a single pass handles both.
    const path = window.location.pathname;
    navLinks().forEach((link) => {
      const route = link.getAttribute('data-shell-route') || '';
      const isActive = routeIsActive(route, path);
      const isPill = link.closest('.pill-nav') !== null;

      if (isPill) {
        link.classList.toggle('pill-nav__tab--active', isActive);
      } else {
        link.classList.toggle('bg-surface-3', isActive);
        link.classList.toggle('text-white', isActive);
        link.classList.toggle('text-slate-400', !isActive);
        link.classList.toggle('hover:text-white', !isActive);
        link.classList.toggle('hover:bg-surface-2', !isActive);
      }

      if (isActive) {
        link.setAttribute('aria-current', 'page');
      } else {
        link.removeAttribute('aria-current');
      }
    });
  }

  function syncDocumentTitleFromContent() {
    const marker = document.querySelector('#app-content [data-page-title]');
    if (!(marker instanceof HTMLElement)) {
      return;
    }
    const pageTitle = marker.dataset.pageTitle;
    if (pageTitle) {
      document.title = pageTitle;
    }
  }

  function setShellLoading(isLoading) {
    const content = document.getElementById('app-content');
    if (!content) {
      return;
    }
    content.classList.toggle('is-shell-loading', isLoading);
  }

  function triggerShellEnter() {
    if (reducedMotionQuery.matches) {
      return;
    }
    const content = document.getElementById('app-content');
    if (!content) {
      return;
    }
    content.classList.remove('is-shell-entering');
    void content.offsetWidth;
    content.classList.add('is-shell-entering');
    if (shellEnterTimer !== null) {
      window.clearTimeout(shellEnterTimer);
    }
    shellEnterTimer = window.setTimeout(() => {
      content.classList.remove('is-shell-entering');
      shellEnterTimer = null;
    }, 190);
  }

  function syncShellUi() {
    syncShellNavState();
    syncDocumentTitleFromContent();
  }

  const toggle = document.getElementById('mobile-nav-toggle');
  const menu = document.getElementById('mobile-nav-menu');
  const backdrop = document.getElementById('mobile-nav-backdrop');

  if (!toggle || !menu) {
    return;
  }

  const setExpanded = (expanded) => {
    toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    menu.classList.toggle('is-open', expanded);
    if (backdrop) {
      backdrop.classList.toggle('is-visible', expanded);
    }
  };

  if (backdrop) {
    backdrop.addEventListener('click', () => setExpanded(false));
  }

  toggle.addEventListener('click', function () {
    const isOpen = toggle.getAttribute('aria-expanded') === 'true';
    setExpanded(!isOpen);
  });

  window.addEventListener('resize', function () {
    if (window.innerWidth >= 640) {
      setExpanded(false);
    }
  });

  syncShellUi();

  document.body.addEventListener('htmx:beforeRequest', function (evt) {
    const triggerEl = evt.detail.elt;
    if (!(triggerEl instanceof Element)) {
      return;
    }
    if (!triggerEl.closest('[data-shell-nav="true"]')) {
      return;
    }
    setExpanded(false);
    setShellLoading(true);
  });

  document.body.addEventListener('htmx:afterSwap', function (evt) {
    if (!evt.detail.target || evt.detail.target.id !== 'app-content') {
      return;
    }
    setShellLoading(false);
    syncShellUi();
    triggerShellEnter();
    // Treat every #app-content swap as a page navigation and jump to the
    // top of the viewport, matching the browser's native behaviour for
    // full-page loads. htmx:historyRestore (browser back/forward) has its
    // own handler below and intentionally does NOT reset scroll so the
    // user's prior scroll position is preserved on back navigation.
    window.scrollTo({ top: 0, left: 0, behavior: 'auto' });
  });

  document.body.addEventListener('htmx:responseError', function () {
    setShellLoading(false);
  });

  document.body.addEventListener('htmx:sendError', function () {
    setShellLoading(false);
  });

  document.body.addEventListener('htmx:historyRestore', function () {
    syncShellUi();
    triggerShellEnter();
  });

  window.addEventListener('popstate', function () {
    window.setTimeout(function () {
      syncShellUi();
      triggerShellEnter();
    }, 0);
  });
})();

