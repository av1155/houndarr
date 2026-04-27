// Logs page controller.  initLogsPage() re-runs every time HTMX
// swaps the Logs partial into #app-content; the outer AbortController
// aborts the previous binding so listeners don't linger on the
// detached DOM between navigations.

function initLogsPage() {
  window.__houndarrLogsPageController?.abort();
  const controller = new AbortController();
  window.__houndarrLogsPageController = controller;
  const { signal } = controller;

  const FEED = document.getElementById('log-feed');
  const FORM = document.getElementById('log-filter-form');
  const LIVE = document.getElementById('live-indicator');
  const LIVE_META = document.getElementById('live-meta');
  const BANNER = document.getElementById('new-entries');
  const BANNER_COUNT = document.getElementById('new-entries-count');
  const BANNER_NOUN = document.getElementById('new-entries-noun');
  const showToast = window.houndarrShowToast || function () {};

  if (!FEED || !FORM) {
    return;
  }

  // Expand/collapse state persists on window across HTMX swaps so a
  // filter change that re-renders the feed keeps the user's intent.
  // expandedOverrides wins over the default when both are clear; the
  // default comes from article[data-expanded] which the template
  // computes per-cycle (open for activity cycles, closed for
  // skip-only and system).
  window.__houndarrLogsExpandedOverrides ||= new Set();
  window.__houndarrLogsCollapsedOverrides ||= new Set();
  const expandedOverrides = window.__houndarrLogsExpandedOverrides;
  const collapsedOverrides = window.__houndarrLogsCollapsedOverrides;

  const formatLocalTimestamp =
    window.houndarrClientHelpers?.formatLocalTimestamp ||
    function fallbackFormat(isoTs) {
      return isoTs || '';
    };

  const reducedMotionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');

  function formatRelTime(isoTs) {
    if (!isoTs) return '';
    const then = Date.parse(isoTs);
    if (Number.isNaN(then)) return '';
    const secs = Math.max(0, Math.floor((Date.now() - then) / 1000));
    if (secs < 5) return 'just now';
    if (secs < 60) return `${secs}s ago`;
    const mins = Math.floor(secs / 60);
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
  }

  function formatVisibleTimestamps(root) {
    root.querySelectorAll('[data-ts]:not([data-ts-formatted])').forEach((el) => {
      const ts = el.getAttribute('data-ts') || '';
      if (ts) {
        el.textContent = formatLocalTimestamp(ts);
      }
      el.setAttribute('data-ts-formatted', 'true');
    });
  }

  // Expand / collapse with JS-driven max-height.  Matches the admin
  // panel pattern in settings.js: measure scrollHeight on open, pin
  // the value inline, flip to max-height:none after transitionend so
  // the card can grow on window resize without a second JS pass.
  function applyExpand(article, open, animate = true) {
    const body = article.querySelector('.cycle__body');
    const header = article.querySelector('.cycle__header');
    if (!body) return;

    if (!animate || reducedMotionQuery.matches) {
      article.dataset.expanded = open ? 'true' : 'false';
      header?.setAttribute('aria-expanded', open ? 'true' : 'false');
      body.style.maxHeight = open ? 'none' : '0px';
      return;
    }

    if (open) {
      const target = body.scrollHeight;
      article.dataset.expanded = 'true';
      header?.setAttribute('aria-expanded', 'true');
      body.style.maxHeight = `${target}px`;
      const onDone = (ev) => {
        if (ev.propertyName !== 'max-height') return;
        body.removeEventListener('transitionend', onDone);
        if (article.dataset.expanded === 'true') {
          body.style.maxHeight = 'none';
        }
      };
      body.addEventListener('transitionend', onDone, { signal });
    } else {
      // Pin a concrete max-height first so the transition has a
      // from-value, force a reflow, then animate to 0.
      body.style.maxHeight = `${body.scrollHeight}px`;
      // eslint-disable-next-line no-unused-expressions
      body.offsetHeight;
      article.dataset.expanded = 'false';
      header?.setAttribute('aria-expanded', 'false');
      requestAnimationFrame(() => {
        body.style.maxHeight = '0px';
      });
    }
  }

  function toggleCycle(article) {
    if (!article) return;
    const isOpen = article.dataset.expanded === 'true';
    const cycleId = article.dataset.cycleId || '';
    if (isOpen) {
      if (cycleId) {
        collapsedOverrides.add(cycleId);
        expandedOverrides.delete(cycleId);
      }
      applyExpand(article, false);
    } else {
      if (cycleId) {
        expandedOverrides.add(cycleId);
        collapsedOverrides.delete(cycleId);
      }
      applyExpand(article, true);
    }
  }

  // animate=false on initial mount and after HTMX swaps so newly
  // rendered cards appear at their final state instead of sliding
  // open over 280ms.  User toggles use animate=true.
  function applyExpandState(root) {
    root.querySelectorAll('.cycle').forEach((article) => {
      const cycleId = article.dataset.cycleId || '';
      const defaultOpen = article.dataset.expanded === 'true';
      let open = defaultOpen;
      if (cycleId) {
        if (expandedOverrides.has(cycleId)) open = true;
        else if (collapsedOverrides.has(cycleId)) open = false;
      }
      applyExpand(article, open, false);
    });
  }

  FEED.addEventListener(
    'click',
    (ev) => {
      const header = ev.target.closest('.cycle__header');
      if (!header) return;
      toggleCycle(header.closest('.cycle'));
    },
    { signal },
  );

  FEED.addEventListener(
    'keydown',
    (ev) => {
      if (ev.key !== 'Enter' && ev.key !== ' ') return;
      const header = ev.target.closest('.cycle__header');
      if (!header) return;
      ev.preventDefault();
      toggleCycle(header.closest('.cycle'));
    },
    { signal },
  );

  const COPY_COLS = [
    'timestamp',
    'instance',
    'action',
    'kind',
    'type',
    'title',
    'reason',
    'cycle',
    'trigger',
  ];
  const COPY_MARKDOWN_HEADERS = ['Timestamp', 'Instance', 'Action', 'Kind', 'Title', 'Reason'];

  function extractRowData() {
    const rows = [];
    FEED.querySelectorAll('.cycle').forEach((article) => {
      const instance = article.querySelector('.cycle__instance')?.textContent.trim() || '';
      const trigger = article.dataset.cycleTrigger || '';
      const cycleId = article.dataset.cycleId || '';
      article.querySelectorAll('.entry').forEach((entry) => {
        const tsEl = entry.querySelector('[data-ts]');
        const rawTs = tsEl?.getAttribute('data-ts') || '';
        rows.push({
          timestamp: rawTs ? formatLocalTimestamp(rawTs) : '',
          instance,
          action: entry.querySelector('.entry__action')?.textContent.trim() || '',
          kind: entry.querySelector('.entry__kind')?.textContent.trim() || '',
          type: entry.dataset.itemType || '',
          title: entry.querySelector('.entry__title')?.textContent.trim() || '',
          reason: entry.querySelector('.entry__reason')?.textContent.trim() || '',
          cycle: cycleId,
          trigger,
        });
      });
    });
    return rows;
  }

  function safeTsv(value) {
    return String(value).replaceAll('\t', ' ').replaceAll('\n', ' ').replaceAll('\r', ' ');
  }

  function safeMarkdown(value) {
    return String(value).replaceAll('|', '\\|').replaceAll('\n', ' ');
  }

  function buildCopyText(format) {
    const rows = extractRowData();
    if (rows.length === 0) return null;

    if (format === 'markdown') {
      const divider = COPY_MARKDOWN_HEADERS.map(() => '---');
      const lines = [
        `| ${COPY_MARKDOWN_HEADERS.join(' | ')} |`,
        `| ${divider.join(' | ')} |`,
      ];
      rows.forEach((r) => {
        const cells = [r.timestamp, r.instance, r.action, r.kind, r.title, r.reason].map(safeMarkdown);
        lines.push(`| ${cells.join(' | ')} |`);
      });
      return lines.join('\n');
    }

    if (format === 'json') {
      return JSON.stringify(rows, null, 2);
    }

    if (format === 'text') {
      return rows
        .map((r) => {
          const parts = [];
          if (r.timestamp) parts.push(`[${r.timestamp}]`);
          if (r.instance) parts.push(r.instance);
          if (r.action) parts.push(r.action.toUpperCase());
          if (r.kind) parts.push(`kind:${r.kind}`);
          if (r.title) parts.push(r.title);
          if (r.reason) parts.push(`- ${r.reason}`);
          return parts.join(' ');
        })
        .join('\n');
    }

    const header = COPY_COLS.map(safeTsv).join('\t');
    const body = rows.map((r) => COPY_COLS.map((c) => safeTsv(r[c] || '')).join('\t'));
    return [header, ...body].join('\n');
  }

  async function writeClipboard(text) {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return;
    }
    // Fallback for http:// contexts (non-secure): textarea + execCommand.
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    try {
      document.execCommand('copy');
    } finally {
      document.body.removeChild(ta);
    }
  }

  function performCopy(format) {
    const text = buildCopyText(format);
    if (text === null) {
      showToast('Nothing to copy');
      return;
    }
    writeClipboard(text)
      .then(() => {
        const rowCount = extractRowData().length;
        const labelByFormat = {
          tsv: 'TSV',
          markdown: 'Markdown',
          json: 'JSON',
          text: 'plain text',
        };
        const label = labelByFormat[format] || 'text';
        showToast(`Copied ${rowCount} ${rowCount === 1 ? 'entry' : 'entries'} as ${label}`);
      })
      .catch(() => showToast('Copy failed'));
  }

  let openMenu = null;

  function closeMenu() {
    if (!openMenu) return;
    openMenu.hidden = true;
    const group = openMenu.closest('[data-copy-group]');
    group?.querySelector('[data-copy-chevron]')?.setAttribute('aria-expanded', 'false');
    openMenu = null;
  }

  function openMenuFor(menu) {
    closeMenu();
    menu.hidden = false;
    const group = menu.closest('[data-copy-group]');
    group?.querySelector('[data-copy-chevron]')?.setAttribute('aria-expanded', 'true');
    openMenu = menu;
  }

  document.querySelectorAll('[data-copy-group]').forEach((group) => {
    const mainBtn = group.querySelector('[data-copy-main]');
    const chevronBtn = group.querySelector('[data-copy-chevron]');
    const menu = group.querySelector('.copy-menu');

    mainBtn?.addEventListener(
      'click',
      () => {
        closeMenu();
        performCopy('tsv');
      },
      { signal },
    );

    chevronBtn?.addEventListener(
      'click',
      (ev) => {
        ev.stopPropagation();
        if (!menu) return;
        if (menu.hidden) openMenuFor(menu);
        else closeMenu();
      },
      { signal },
    );

    menu?.querySelectorAll('[data-copy-format]').forEach((item) => {
      item.addEventListener(
        'click',
        () => {
          const fmt = item.getAttribute('data-copy-format') || 'tsv';
          closeMenu();
          performCopy(fmt);
        },
        { signal },
      );
    });
  });

  document.addEventListener(
    'click',
    (ev) => {
      if (openMenu && !ev.target.closest('[data-copy-group]')) {
        closeMenu();
      }
    },
    { signal },
  );

  document.addEventListener(
    'keydown',
    (ev) => {
      if (ev.key === 'Escape' && openMenu) {
        closeMenu();
      }
    },
    { signal },
  );

  // Multi-select filter dropdown controller.  Mirrors the copy-menu
  // pattern above (module-local open reference, click-outside and
  // Escape wired to the shared AbortController signal) but scoped to
  // [data-multiselect] roots so the two controllers never tangle.
  // The checkboxes inside live in the log-filter form, so HTMX fires
  // hx-get on every `change` via hx-trigger="change, submit"; this
  // controller only handles open/close, the summary label, and focus
  // restoration.
  let openMultiselect = null;
  let lastMultiselectTrigger = null;

  function closeMultiselect() {
    if (!openMultiselect) return;
    openMultiselect.hidden = true;
    const root = openMultiselect.closest('[data-multiselect]');
    const trigger = root?.querySelector('[data-multiselect-trigger]');
    trigger?.setAttribute('aria-expanded', 'false');
    openMultiselect = null;
    // Restore focus to the trigger the user opened the menu from so
    // keyboard users land back where they started after Escape.
    lastMultiselectTrigger?.focus();
    lastMultiselectTrigger = null;
  }

  function openMultiselectMenu(menu, trigger) {
    closeMultiselect();
    menu.hidden = false;
    trigger.setAttribute('aria-expanded', 'true');
    openMultiselect = menu;
    lastMultiselectTrigger = trigger;
  }

  function multiselectSummaryText(root) {
    const menu = root.querySelector('[data-multiselect-menu]');
    const boxes = menu ? [...menu.querySelectorAll('input[type="checkbox"]')] : [];
    const checked = boxes.filter((b) => b.checked);
    if (checked.length === 0) return 'All instances';
    if (checked.length === 1) {
      return checked[0].closest('label')?.querySelector('span')?.textContent?.trim() || '';
    }
    return `${checked.length} instances`;
  }

  document.querySelectorAll('[data-multiselect]').forEach((root) => {
    const trigger = root.querySelector('[data-multiselect-trigger]');
    const menu = root.querySelector('[data-multiselect-menu]');
    const summary = root.querySelector('.filter-multiselect__summary');
    if (!trigger || !menu) return;

    trigger.addEventListener(
      'click',
      (ev) => {
        ev.stopPropagation();
        if (menu.hidden) openMultiselectMenu(menu, trigger);
        else closeMultiselect();
      },
      { signal },
    );

    root.querySelectorAll('input[type="checkbox"]').forEach((box) => {
      box.addEventListener(
        'change',
        () => {
          if (summary) summary.textContent = multiselectSummaryText(root);
        },
        { signal },
      );
    });
  });

  document.addEventListener(
    'click',
    (ev) => {
      if (openMultiselect && !ev.target.closest('[data-multiselect]')) {
        closeMultiselect();
      }
    },
    { signal },
  );

  document.addEventListener(
    'keydown',
    (ev) => {
      if (ev.key === 'Escape' && openMultiselect) {
        closeMultiselect();
      }
    },
    { signal },
  );

  const SCROLL_THRESHOLD = 240;
  const POLL_MS = 30000;

  function firstCycleId() {
    const first = FEED.querySelector('.cycle[data-cycle-id]:not([data-cycle-id=""])');
    return first?.getAttribute('data-cycle-id') || '';
  }

  let newestCycleId = firstCycleId();
  let pendingCount = 0;

  function updateBannerVisibility() {
    if (!BANNER) return;
    const scrolledAway = window.scrollY > SCROLL_THRESHOLD;
    const show = pendingCount > 0 && scrolledAway;
    if (show) {
      if (BANNER_COUNT) BANNER_COUNT.textContent = String(pendingCount);
      if (BANNER_NOUN) BANNER_NOUN.textContent = pendingCount === 1 ? 'cycle' : 'cycles';
      BANNER.hidden = false;
      BANNER.dataset.visible = 'true';
      LIVE?.setAttribute('data-state', 'paused');
      const label = LIVE?.querySelector('.live-label');
      if (label) label.textContent = 'Paused';
    } else {
      BANNER.dataset.visible = 'false';
      // Keep hidden once no pending entries remain.
      if (pendingCount === 0) {
        BANNER.hidden = true;
      }
      LIVE?.setAttribute('data-state', 'live');
      const label = LIVE?.querySelector('.live-label');
      if (label) label.textContent = 'Live';
    }
  }

  // Silently refresh the feed when new cycles land and the user is at
  // the top: scrolling away triggers the toast path instead, but at the
  // top there is no need to interrupt the user with a click target. The
  // refresh fires only when there are real new cycles since the last
  // refresh so it does not race with the operator's filter input.
  function maybeAutoRefreshAtTop() {
    if (pendingCount === 0) return;
    if (window.scrollY > SCROLL_THRESHOLD) return;
    if (!window.htmx || !FORM) return;
    pendingCount = 0;
    updateBannerVisibility();
    window.htmx.trigger(FORM, 'submit');
  }

  function updateLiveMeta(isoTs) {
    if (!LIVE_META) return;
    LIVE_META.textContent = isoTs ? `updated ${formatRelTime(isoTs)}` : 'updated just now';
  }

  async function pollHead() {
    if (signal.aborted) return;
    try {
      const qs = newestCycleId
        ? `?since_cycle_id=${encodeURIComponent(newestCycleId)}`
        : '';
      const res = await fetch(`/api/logs/head${qs}`, { signal });
      if (!res.ok) return;
      const data = await res.json();
      pendingCount = Number(data.count_newer_than) || 0;
      if (data.newest_timestamp) updateLiveMeta(data.newest_timestamp);
      updateBannerVisibility();
      maybeAutoRefreshAtTop();
    } catch (err) {
      if (err.name !== 'AbortError') {
        // Head poll is best-effort; silently suppress transient
        // network errors and retry on the next tick.
      }
    }
  }

  const pollTimer = window.setInterval(pollHead, POLL_MS);
  signal.addEventListener('abort', () => {
    window.clearInterval(pollTimer);
  });
  pollHead();

  window.addEventListener('scroll', updateBannerVisibility, {
    signal,
    passive: true,
  });

  BANNER?.addEventListener(
    'click',
    () => {
      pendingCount = 0;
      updateBannerVisibility();
      window.scrollTo({ top: 0, behavior: 'smooth' });
      // Force a refetch so the newest cycles land in the feed.
      if (window.htmx && FORM) {
        window.htmx.trigger(FORM, 'submit');
      }
    },
    { signal },
  );

  document.body.addEventListener(
    'htmx:afterSwap',
    (ev) => {
      const target = ev.detail?.target;
      if (!target) return;
      const touchesFeed =
        target.id === 'log-feed' ||
        target.id === 'pagination-row' ||
        target.closest?.('#log-feed') !== null;
      if (!touchesFeed) return;
      formatVisibleTimestamps(FEED);
      applyExpandState(FEED);
      newestCycleId = firstCycleId() || newestCycleId;
      pendingCount = 0;
      updateBannerVisibility();
    },
    { signal },
  );

  formatVisibleTimestamps(document);
  applyExpandState(FEED);
  updateLiveMeta(null);
}

if (document.querySelector('[data-page-key="logs"]')) {
  initLogsPage();
}

document.body.addEventListener('htmx:afterSwap', (evt) => {
  if (
    evt.detail?.target?.id === 'app-content' &&
    document.querySelector('[data-page-key="logs"]')
  ) {
    initLogsPage();
  }
});
