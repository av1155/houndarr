// houndarrClientHelpers (including formatLocalTimestamp) is defined in base.html <head>
// so it is available on both initial page load and HTMX navigation.

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
    const path = window.location.pathname;
    navLinks().forEach((link) => {
      const route = link.getAttribute('data-shell-route') || '';
      const isActive = routeIsActive(route, path);

      link.classList.toggle('bg-surface-3', isActive);
      link.classList.toggle('text-white', isActive);
      link.classList.toggle('text-slate-400', !isActive);
      link.classList.toggle('hover:text-white', !isActive);
      link.classList.toggle('hover:bg-surface-2', !isActive);
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

// Overlay scrollbar: sizes + positions `.hx-scrollbar__thumb` against
// window scroll, fades in on scroll, fades out on idle. Paired with the
// hidden native scrollbar in app.css; see that comment for the strip-bug
// background.
(function () {
  var bar = document.querySelector('.hx-scrollbar');
  var thumb = bar && bar.querySelector('.hx-scrollbar__thumb');
  if (!bar || !thumb) return;

  var idleTimeoutId = null;
  var rafId = null;
  var IDLE_MS = 900;
  var MIN_THUMB_PX = 32;

  function update() {
    rafId = null;
    var doc = document.documentElement;
    var viewH = window.innerHeight;
    var docH = Math.max(doc.scrollHeight, document.body.scrollHeight);
    if (docH - viewH < MIN_THUMB_PX / 2) {
      // Ignore sub-pixel rounding and incidental border/padding overflow
      // that isn't meaningfully scrollable. No point showing a thumb
      // whose travel distance is smaller than the thumb's own minimum
      // height.
      bar.classList.remove('is-visible');
      thumb.style.height = '0';
      return;
    }
    var ratio = viewH / docH;
    var thumbH = Math.max(MIN_THUMB_PX, Math.round(ratio * viewH));
    var maxThumbTop = viewH - thumbH;
    var scrollRatio = window.scrollY / (docH - viewH);
    var thumbY = Math.round(scrollRatio * maxThumbTop);
    thumb.style.height = thumbH + 'px';
    thumb.style.transform = 'translateY(' + thumbY + 'px)';
  }

  function schedule() {
    if (rafId !== null) return;
    rafId = window.requestAnimationFrame(update);
  }

  function reveal() {
    bar.classList.add('is-visible');
    if (idleTimeoutId !== null) window.clearTimeout(idleTimeoutId);
    idleTimeoutId = window.setTimeout(function () {
      bar.classList.remove('is-visible');
    }, IDLE_MS);
  }

  window.addEventListener('scroll', function () {
    schedule();
    reveal();
  }, { passive: true });

  window.addEventListener('resize', schedule, { passive: true });

  // Recalculate when HTMX swaps change content height, or when any other
  // DOM mutation (modals, async content) changes scrollable area.
  document.body.addEventListener('htmx:afterSettle', schedule);
  document.body.addEventListener('htmx:afterSwap', schedule);
  var observer = new MutationObserver(schedule);
  observer.observe(document.body, { childList: true, subtree: true });

  // Defer the initial measurement so layout has settled.
  window.requestAnimationFrame(schedule);
})();
