(function () {
  function formatLocalTimestamp(isoTimestamp) {
    if (!isoTimestamp) {
      return '—';
    }

    const parsed = new Date(isoTimestamp);
    if (Number.isNaN(parsed.getTime())) {
      return isoTimestamp;
    }

    return new Intl.DateTimeFormat(undefined, {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
      timeZoneName: 'short',
    }).format(parsed);
  }

  window.houndarrClientHelpers = {
    formatLocalTimestamp,
  };
})();

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

      link.classList.toggle('bg-slate-800', isActive);
      link.classList.toggle('text-white', isActive);
      link.classList.toggle('text-slate-400', !isActive);
      link.classList.toggle('hover:text-white', !isActive);
      link.classList.toggle('hover:bg-slate-800', !isActive);
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

  if (!toggle || !menu) {
    return;
  }

  const setExpanded = (expanded) => {
    toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    menu.classList.toggle('is-open', expanded);
  };

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
