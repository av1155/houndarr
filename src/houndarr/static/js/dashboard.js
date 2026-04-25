// Dashboard page controller. initDashboardPage() re-runs every time
// HTMX swaps the Dashboard partial into #app-content; the outer
// AbortController aborts the previous binding so listeners don't
// linger on the detached DOM between navigations.

function initDashboardPage() {
  window.__houndarrDashboardPageController?.abort();
  const controller = new AbortController();
  window.__houndarrDashboardPageController = controller;
  const { signal } = controller;

    const RUN_NOW_MIN_RUNNING_MS  = 700;
    const RUN_NOW_SUCCESS_HOLD_MS = 900;
    const RUN_NOW_ERROR_HOLD_MS   = 1100;

    function toNumber(value) {
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : 0;
    }

    // Lucide icon per run-now state. We swap the SVG rather than rotate
    // a single one: a spinning play triangle reads as nonsense, while a
    // rotating loader-circle reads as "working".
    const RUN_NOW_ICONS = {
      idle:    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 5a2 2 0 0 1 3.008-1.728l11.997 6.998a2 2 0 0 1 .003 3.458l-12 7A2 2 0 0 1 5 19z"/></svg>',
      running: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>',
      success: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>',
      error:   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>',
    };

    // Parse SVG as an HTML fragment (not image/svg+xml) so the HTML parser
    // assigns the SVG namespace automatically and accepts our stripped-down
    // strings that omit xmlns. Source strings are literal constants in this
    // file, not user input; using createContextualFragment instead of
    // innerHTML also keeps the XSS surface zero-by-construction if an icon
    // is ever sourced from elsewhere.
    function setRunNowIcon(host, svgString) {
      if (!host) return;
      const fragment = document.createRange().createContextualFragment(svgString);
      host.replaceChildren(fragment);
    }

    function setRunNowButtonState(button, state) {
      const label = button.querySelector('[data-run-now-label]');
      const icon  = button.querySelector('[data-run-now-icon]');

      button.classList.remove('is-running', 'is-success', 'is-error');
      if (icon) icon.classList.remove('animate-spin');

      if (state === 'running') {
        button.classList.add('is-running');
        button.disabled = true;
        button.setAttribute('aria-busy', 'true');
        if (label) label.textContent = 'Running…';
        setRunNowIcon(icon, RUN_NOW_ICONS.running);
        if (icon) icon.classList.add('animate-spin');
        return;
      }

      button.disabled = false;
      button.setAttribute('aria-busy', 'false');

      if (state === 'success') {
        button.classList.add('is-success');
        if (label) label.textContent = 'Queued';
        setRunNowIcon(icon, RUN_NOW_ICONS.success);
        return;
      }
      if (state === 'error') {
        button.classList.add('is-error');
        if (label) label.textContent = 'Failed';
        setRunNowIcon(icon, RUN_NOW_ICONS.error);
        return;
      }
      if (label) label.textContent = 'Run now';
      setRunNowIcon(icon, RUN_NOW_ICONS.idle);
    }

    function completeRunNowRequest(button, statusCode) {
      const startedAt  = Number(button.dataset.runNowStartedAt || 0);
      const elapsed    = Math.max(0, Date.now() - startedAt);
      const waitForMin = Math.max(0, RUN_NOW_MIN_RUNNING_MS - elapsed);
      const outcome    = statusCode >= 200 && statusCode < 300 ? 'success' : 'error';
      const holdMs     = outcome === 'success' ? RUN_NOW_SUCCESS_HOLD_MS : RUN_NOW_ERROR_HOLD_MS;

      window.setTimeout(function () {
        if (!document.body.contains(button)) return;
        setRunNowButtonState(button, outcome);
        window.setTimeout(function () {
          if (!document.body.contains(button)) return;
          setRunNowButtonState(button, 'idle');
        }, holdMs);
      }, waitForMin);
    }

    // Top-of-page renderers.
    //
    // These take the /api/status?v=2 envelope and emit HTML strings for
    // the four top-of-page widgets plus the section heading.  All
    // dynamic values pass through escHtml so the result is safe to parse
    // via a <template> and adopt into the live DOM.

    function formatTimeAgo(iso) {
      if (!iso) return '';
      const ts = Date.parse(iso);
      if (Number.isNaN(ts)) return '';
      const delta = Math.max(0, Date.now() - ts);
      const s = Math.floor(delta / 1000);
      if (s < 60)     return `${s}s ago`;
      const m = Math.floor(s / 60);
      if (m < 60)     return `${m}m ago`;
      const h = Math.floor(m / 60);
      if (h < 24)     return `${h}h ago`;
      const d = Math.floor(h / 24);
      return `${d}d ago`;
    }

    // Lucide radar icon used as the patrol eyebrow glyph.
    const RADAR_ICON =
      '<svg class="dash-sub__eyebrow-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      '<path d="M19.07 4.93A10 10 0 0 0 6.99 3.34"/>' +
      '<path d="M4 6h.01"/>' +
      '<path d="M2.29 9.62A10 10 0 1 0 21.31 8.35"/>' +
      '<path d="M16.24 7.76A6 6 0 1 0 8.23 16.67"/>' +
      '<path d="M12 18h.01"/>' +
      '<path d="M17.99 11.66A6 6 0 0 1 15.77 16.67"/>' +
      '<circle cx="12" cy="12" r="2"/>' +
      '<path d="m13.41 10.59 5.66-5.66"/>' +
      '</svg>';

    // Lucide arrow-right used inline next to "View logs" style links.
    const ARROW_RIGHT_ICON =
      '<svg class="inline-link-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      '<path d="M5 12h14"/>' +
      '<path d="m12 5 7 7-7 7"/>' +
      '</svg>';

    function formatPatrolEyebrow() {
      const now = new Date();
      const monthNames = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
      const mo = monthNames[now.getMonth()];
      const day = String(now.getDate()).padStart(2, '0');
      const year = now.getFullYear();
      const hh = String(now.getHours()).padStart(2, '0');
      const mm = String(now.getMinutes()).padStart(2, '0');
      const tz = now
        .toLocaleTimeString(undefined, { timeZoneName: 'short' })
        .split(' ')
        .pop();
      return `Patrol · ${mo} ${day} ${year} · ${hh}:${mm} ${tz || ''}`.trim();
    }

    function failingInstances(instances) {
      // Shared helper for renderSubheader + renderAlert so both parts
      // of the dashboard header agree on which instances are "failing"
      // right now.  Disabled instances opted out of patrol, so their
      // stale error rows are excluded by design.
      return instances.filter(function (i) { return i.enabled && i.active_error; });
    }

    function renderSubheader(instances) {
      const eyebrow = formatPatrolEyebrow();
      if (instances.length === 0) {
        return `
<section class="dash-sub">
  <p class="dash-sub__eyebrow">${RADAR_ICON}<span>${escHtml(eyebrow)}</span></p>
  <h1 class="dash-sub__sentence">No hounds on patrol yet.</h1>
</section>`;
      }
      const total = instances.length;
      const active = instances.filter(function (i) { return i.enabled; }).length;
      const failing = failingInstances(instances);
      let sentence;
      if (failing.length > 0) {
        const on = total - failing.length;
        // Singular: name the instance so the subheader reads naturally.
        // Plural: switch to a count-based phrasing so the line length
        // stays stable regardless of how many instances degraded at
        // once (a five-instance outage should not push "needs
        // attention" off the end of the viewport).
        const attn = failing.length === 1
          ? `${escHtml(failing[0].name)} needs attention.`
          : `${failing.length} instances need attention.`;
        sentence = `${on} of ${total} hounds on patrol. <span class="attn">${attn}</span>`;
      } else {
        const recent = instances
          .map(function (i) { return i.last_dispatch_at; })
          .filter(Boolean)
          .sort()
          .pop();
        const whenPart = recent
          ? `Last dispatch ${escHtml(formatTimeAgo(recent))}.`
          : 'No recent dispatches.';
        // At active=0 (all instances disabled) and active=1 (exactly
        // one on patrol) the "All N hounds on patrol" phrasing reads
        // wrong; swap to count-specific sentences before falling back
        // to the plural default.
        let patrolLead;
        if (active === 0) {
          patrolLead = 'No hounds on patrol.';
        } else if (active === 1) {
          patrolLead = '1 hound on patrol.';
        } else {
          patrolLead = `All ${active} hounds on patrol.`;
        }
        sentence = `${patrolLead} <span class="muted">${whenPart}</span>`;
      }
      return `
<section class="dash-sub">
  <p class="dash-sub__eyebrow">${RADAR_ICON}<span>${escHtml(eyebrow)}</span></p>
  <h1 class="dash-sub__sentence">${sentence}</h1>
</section>`;
    }

    function renderAlertMessage(msg) {
      // Wrap any http(s):// URL in the message in a <span class="mono"> so
      // it picks up the red monospace treatment from the preview. Each
      // segment passes through escHtml so the final string is safe.
      const text = msg || 'Could not reach instance';
      const match = text.match(/^(.*?)(https?:\/\/\S+)(.*)$/);
      if (!match) return escHtml(text);
      const before = match[1];
      const url = match[2];
      const after = match[3];
      return `${escHtml(before)}<span class="mono">${escHtml(url)}</span>${escHtml(after)}`;
    }

    function renderAlertRow(inst) {
      // One row inside the banner body per errored instance.  The meta
      // ("N failures · last Xm ago") lives inline at the end of the
      // sentence, immediately after the URL, so the eye reads the row
      // as a single line with an inline timestamp instead of a two-
      // column grid with right-aligned metadata.  renderAlertMessage
      // continues to wrap any http(s):// URL in .mono so the URL keeps
      // the red monospace accent.
      const err = inst.active_error || {};
      const failures = toNumber(err.failures_count);
      const failText = failures > 0
        ? `${failures} failure${failures === 1 ? '' : 's'}`
        : 'Connection error';
      const whenAgo = formatTimeAgo(err.timestamp);
      const msg = err.message || '';
      return `
    <li class="dash-alert__row">
      <strong>${escHtml(inst.name)}</strong><span class="muted">:</span>
      ${renderAlertMessage(msg)}
      <span class="dash-alert__row-meta"><span class="muted dash-alert__row-meta-lead">·</span> ${escHtml(failText)} <span class="muted">·</span> last ${escHtml(whenAgo || 'just now')}</span>
    </li>`;
    }

    function renderAlert(instances) {
      const failing = failingInstances(instances);
      if (failing.length === 0) return '';
      // One ?instance_id=N per failing instance so the "View logs"
      // deep link opens the logs page with all errored instances
      // pre-selected in the multi-select filter.  FastAPI reads the
      // repeated query param as a list and the /logs SSR route pre-
      // checks the matching checkboxes in the filter dropdown.
      const qs = failing
        .map((i) => `instance_id=${encodeURIComponent(i.id)}`)
        .join('&');
      const logsHref = `/logs?${qs}&action=error`;
      const rows = failing.map(renderAlertRow).join('');
      return `
<section class="dash-alert" role="alert">
  <span class="dash-alert__icon" aria-hidden="true"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4"/><path d="M12 17h.01"/></svg></span>
  <div class="dash-alert__body">
    <p class="dash-alert__head">Degraded · ${failing.length} instance${failing.length === 1 ? '' : 's'} offline</p>
    <ul class="dash-alert__list">${rows}
    </ul>
  </div>
  <a class="dash-alert__link" href="${logsHref}"
     hx-get="${logsHref}" hx-target="#app-content" hx-swap="innerHTML" hx-push-url="true">View logs${ARROW_RIGHT_ICON}</a>
</section>`;
    }

    function renderLibraryHealth(instances) {
      // Match the per-card contract: disabled or offline instances have a
      // stale monitored_total (0 or a cached snapshot) while their
      // cooldown_breakdown still carries entries from the cooldowns table.
      // Summing both would let cooldowns exceed monitored and drive
      // Eligible negative (then clamped to 0). Cards skip those instances
      // by showing "-"; the rollup does the same here.
      const activeInstances = instances.filter(function (i) {
        return i.enabled && !i.active_error;
      });
      const totals = activeInstances.reduce(function (acc, i) {
        const bd = i.cooldown_breakdown || { missing: 0, cutoff: 0, upgrade: 0 };
        const monI = toNumber(i.monitored_total);
        const gatedI = toNumber(bd.missing) + toNumber(bd.cutoff);
        const unrI = toNumber(i.unreleased_count);
        acc.monitored += monI;
        acc.cooldown  += toNumber(bd.missing);
        acc.cutoffCd  += toNumber(bd.cutoff);
        acc.upgradeCd += toNumber(bd.upgrade);
        acc.unreleased += unrI;
        // Sum per-instance Eligibles (each already clamped at zero)
        // so one instance's stale-cooldown overshoot cannot drain
        // another instance's positive contribution.  Computing
        // max(0, totals.monitored - totals.gated - totals.unreleased)
        // at the fleet scope silently drops a genuine positive
        // whenever any instance's (gated + unreleased) exceeds its
        // monitored_total: the overshoot turns the fleet total
        // negative, max clamps to 0, and the rollup headline
        // contradicts the sum of the cards below it.  Cooldown
        // accounting can run above monitored_total because rows
        // outlive the wanted item (downloads, unmonitors, cutoff-met
        // transitions) and there is no current lifecycle cleanup; a
        // proper reconcile is a separate commit.
        acc.eligible += Math.max(0, monI - gatedI - unrI);
        return acc;
      }, { monitored: 0, cooldown: 0, cutoffCd: 0, upgradeCd: 0, unreleased: 0, eligible: 0 });
      // Upgrade cooldowns sit outside monitored_total (the upgrade pool is
      // has_file + cutoff_met; those items are neither in /wanted/missing
      // nor /wanted/cutoff), so the bar segment + legend entry surface
      // upgrade activity separately.
      const gated = totals.cooldown + totals.cutoffCd;
      const eligible = totals.eligible;
      // Eyebrow count equals the sum of every segment rendered below it
      // so bar width and eyebrow number visually agree.  Labeling this
      // "items tracked" rather than "monitored" avoids overloading the
      // *arr term: upgrade-pool items are tracked by Houndarr but are
      // not part of each *arr's monitored_total.
      const barTotal =
        eligible
        + totals.cooldown
        + totals.cutoffCd
        + totals.upgradeCd
        + totals.unreleased;
      const ariaLabel = `${eligible} eligible, ${totals.cooldown} cooldown, ${totals.cutoffCd} cutoff cooldown, ${totals.upgradeCd} upgrade cooldown, ${totals.unreleased} unreleased`;
      return `
<section class="dash-lh" aria-label="Library health">
  <p class="dash-lh__eyebrow">Library health · ${barTotal} items tracked</p>
  <div class="dash-lh__headline">
    <span class="dash-lh__stat" data-tip="Items Houndarr can search right now across every enabled instance.">
      <span class="dash-lh__stat-value dash-lh__stat-value--eligible">${eligible}</span>
      <span class="dash-lh__stat-label dash-lh__stat-label--eligible">Eligible</span>
    </span>
    <span class="dash-lh__stat" data-tip="Items on per-item cooldown from a recent search. They return to Eligible once the cooldown elapses.">
      <span class="dash-lh__stat-value dash-lh__stat-value--gated">${gated}</span>
      <span class="dash-lh__stat-label">Gated</span>
    </span>
    <span class="dash-lh__stat" data-tip="Items still awaiting their release date.">
      <span class="dash-lh__stat-value dash-lh__stat-value--unrel">${totals.unreleased}</span>
      <span class="dash-lh__stat-label">Unreleased</span>
    </span>
  </div>
  <div class="dash-lh__bar" role="img" aria-label="${escHtml(ariaLabel)}">
    <div class="dash-lh__segment dash-lh__segment--eligible"   style="flex: ${eligible};"></div>
    <div class="dash-lh__segment dash-lh__segment--cooldown"   style="flex: ${totals.cooldown};"></div>
    <div class="dash-lh__segment dash-lh__segment--cutoff-cd"  style="flex: ${totals.cutoffCd};"></div>
    <div class="dash-lh__segment dash-lh__segment--upgrade-cd" style="flex: ${totals.upgradeCd};"></div>
    <div class="dash-lh__segment dash-lh__segment--unreleased" style="flex: ${totals.unreleased};"></div>
  </div>
  <div class="dash-lh__legend">
    <span class="dash-lh__legend-item"><span class="dash-lh__legend-swatch dash-lh__legend-swatch--eligible"></span>${eligible} eligible</span>
    <span class="dash-lh__legend-item"><span class="dash-lh__legend-swatch dash-lh__legend-swatch--cooldown"></span>${totals.cooldown} cooldown</span>
    <span class="dash-lh__legend-item"><span class="dash-lh__legend-swatch dash-lh__legend-swatch--cutoff-cd"></span>${totals.cutoffCd} cutoff cooldown</span>
    <span class="dash-lh__legend-item"><span class="dash-lh__legend-swatch dash-lh__legend-swatch--upgrade-cd"></span>${totals.upgradeCd} upgrade cooldown</span>
    <span class="dash-lh__legend-item"><span class="dash-lh__legend-swatch dash-lh__legend-swatch--unreleased"></span>${totals.unreleased} unreleased</span>
  </div>
</section>`;
    }

    function typeColorVar(typeName) {
      if (typeName === 'sonarr')      return 'var(--color-sonarr)';
      if (typeName === 'radarr')      return 'var(--color-radarr)';
      if (typeName === 'lidarr')      return 'var(--color-lidarr)';
      if (typeName === 'readarr')     return 'var(--color-readarr)';
      if (typeName === 'whisparr_v2') return 'var(--color-whisparr-v2)';
      if (typeName === 'whisparr_v3') return 'var(--color-whisparr-v3)';
      return 'var(--color-brand-400)';
    }

    // Inline Lucide SVG per search kind.  Paths are verbatim from
    // Lucide v0.x; colors come from --color-kind-* tokens set on the
    // element's `data-kind` attribute in app.css.  Returns '' for an
    // unknown kind so render sites can drop the icon entirely without
    // a wrapper element leaking into the layout.
    function searchKindIcon(kind) {
      const label = kind === 'missing' ? 'Missing search'
        : kind === 'cutoff' ? 'Cutoff search'
        : kind === 'upgrade' ? 'Upgrade search'
        : '';
      if (!label) return '';
      const path =
        kind === 'missing' ? '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>'
        : kind === 'cutoff' ? '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>'
        : '<path d="M12 19V5"/><path d="m5 12 7-7 7 7"/>';
      return `<svg class="kind-icon" data-kind="${kind}" role="img" aria-label="${label}" title="${label}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${path}</svg>`;
    }

    function renderRecentHunts(recentSearches) {
      if (!recentSearches || recentSearches.length === 0) {
        return `
<section class="dash-trail" aria-label="Recent searches">
  <p class="dash-trail__head">Recent hunts</p>
  <p class="dash-trail__empty">No dispatches in the last 7 days. Hounds are holding the line.</p>
</section>`;
      }
      const rows = recentSearches
        .slice(0, 5)
        .map(function (r) {
          const color = typeColorVar(r.instance_type);
          const title = r.item_label || 'Untitled';
          return `
        <div class="dash-trail__row">
          ${searchKindIcon(r.search_kind)}
          <span class="dash-trail__title">${escHtml(title)}</span>
          <span class="dash-trail__inst" style="color: ${color};">${escHtml(r.instance_name)}</span>
          <span class="dash-trail__when">${escHtml(formatTimeAgo(r.timestamp))}</span>
        </div>`;
        })
        .join('');
      return `
<section class="dash-trail" aria-label="Recent searches">
  <p class="dash-trail__head">Recent hunts</p>
  <div class="dash-trail__list">${rows}
  </div>
</section>`;
    }

    function renderSectionHead() {
      return `
<header class="dash-section-head">
  <h2 class="dash-section-head__title">Instances</h2>
  <span class="dash-section-head__rule" aria-hidden="true"></span>
  <a class="dash-section-head__add" href="/settings"
     hx-get="/settings" hx-target="#app-content" hx-swap="innerHTML" hx-push-url="true">+ Add Instance</a>
</header>`;
    }

    function renderTopSection(instances, recentSearches) {
      if (instances.length === 0) {
        return renderSubheader(instances);
      }
      return [
        renderSubheader(instances),
        renderAlert(instances),
        renderLibraryHealth(instances),
        renderRecentHunts(recentSearches),
        renderSectionHead(),
      ].join('');
    }

    // Instance card renderer.

    function formatTimeUntil(iso) {
      if (!iso) return '';
      const ts = Date.parse(iso);
      if (Number.isNaN(ts)) return '';
      const delta = Math.max(0, ts - Date.now());
      const total = Math.floor(delta / 1000);
      if (total <= 0) return 'now';
      const d = Math.floor(total / 86400);
      const h = Math.floor((total % 86400) / 3600);
      const m = Math.floor((total % 3600) / 60);
      if (d > 0 && h > 0) return `${d}d ${h}h`;
      if (d > 0)          return `${d}d`;
      if (h > 0 && m > 0) return `${h}h ${m}m`;
      if (h > 0)          return `${h}h`;
      return `${m}m`;
    }

    function typeEyebrowLabel(typeName) {
      if (!typeName) return 'INSTANCE';
      return String(typeName).toUpperCase();
    }

    function renderStatusPill(inst) {
      if (!inst.enabled) {
        return `<span class="dash-pill dash-pill--disabled"><span class="status-dot status-dot--disabled"></span>Disabled</span>`;
      }
      if (inst.active_error) {
        const count = toNumber(inst.active_error.failures_count) || 1;
        const href = `/logs?instance_id=${encodeURIComponent(inst.id)}&action=error`;
        return `<a class="dash-pill dash-pill--error" href="${href}"
                  hx-get="${href}" hx-target="#app-content" hx-swap="innerHTML" hx-push-url="true"
                  aria-label="${count} error${count === 1 ? '' : 's'}; view logs"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4"/><path d="M12 17h.01"/></svg>${count} error${count === 1 ? '' : 's'}</a>`;
      }
      return `<span class="dash-pill dash-pill--active"><span class="status-dot status-dot--active"></span>Active</span>`;
    }

    // Hourly search-budget bar.  Shows how many indexer hits the
    // instance has dispatched in the last rolling hour against the
    // dominant cap (missing + cutoff + upgrade, when enabled).  The
    // "am I about to get banned by my indexer" signal — #1 research-
    // identified trust anxiety and Huntarr's single most-praised
    // widget pre-incident.  Suppressed when hourly_cap=0 (unlimited)
    // since the denominator is meaningless.
    function renderBudgetBar(inst) {
      if (!inst.enabled || inst.active_error) {
        return '';
      }
      const hourlyCap = toNumber(inst.hourly_cap);
      if (hourlyCap <= 0) {
        // hourly_cap=0 means the user disabled the ceiling.  We still
        // emit the budget slot so two cards in the same grid row stay
        // the same height (the cooldown box above has flex:1 1 auto
        // and would otherwise swell to absorb the missing bar).
        const tip = `Hourly cap disabled for this instance. No ceiling on searches dispatched per hour.`;
        return `
<div class="dash-card__budget dash-card__budget--unlimited" data-tip="${escHtml(tip)}">
  <dt class="dash-card__budget-label">Hourly budget</dt>
  <dd class="dash-card__budget-value">Unlimited</dd>
  <div class="dash-card__budget-bar" aria-hidden="true">
    <span class="dash-card__budget-fill"></span>
  </div>
</div>`;
      }
      const effectiveCap =
        hourlyCap
        + (inst.cutoff_enabled ? toNumber(inst.cutoff_hourly_cap) : 0)
        + (inst.upgrade_enabled ? toNumber(inst.upgrade_hourly_cap) : 0);
      const used = Math.max(0, toNumber(inst.searches_last_hour));
      const pct = effectiveCap > 0 ? Math.min(100, (used / effectiveCap) * 100) : 0;
      const level = pct >= 80 ? 'danger' : pct >= 50 ? 'warning' : 'eligible';
      const atCap = effectiveCap > 0 && used >= effectiveCap;
      // Short sentence shown in the shared tooltip on hover; aria
      // label keeps the detailed breakdown for screen readers.
      const tip = `Searches dispatched this hour against this instance's combined hourly cap.`;
      const aria = `${used} of ${effectiveCap} searches used this hour. `
        + `Missing cap ${hourlyCap}, cutoff cap ${inst.cutoff_enabled ? toNumber(inst.cutoff_hourly_cap) : 'off'}, `
        + `upgrade cap ${inst.upgrade_enabled ? toNumber(inst.upgrade_hourly_cap) : 'off'}.`;
      return `
<div class="dash-card__budget" data-level="${level}"${atCap ? ' data-at-cap="true"' : ''} data-tip="${escHtml(tip)}">
  <dt class="dash-card__budget-label">Hourly budget</dt>
  <dd class="dash-card__budget-value">${used} / ${effectiveCap}</dd>
  <div class="dash-card__budget-bar" role="img" aria-label="${escHtml(aria)}">
    <span class="dash-card__budget-fill" style="--budget-fill: ${pct.toFixed(1)}%;"></span>
  </div>
</div>`;
    }

    function renderUnlockPanel(inst) {
      if (!inst.enabled) {
        return `
<div class="dash-unlocks">
  <p class="dash-unlocks__head">Cooldown schedule</p>
  <p class="dash-unlocks--empty">Instance disabled. No patrols running.</p>
</div>`;
      }
      if (inst.active_error) {
        return `
<div class="dash-unlocks">
  <p class="dash-unlocks__head">Cooldown schedule</p>
  <p class="dash-unlocks--empty">Instance offline. Unable to compute unlocks.</p>
</div>`;
      }
      const items = Array.isArray(inst.unlocking_next) ? inst.unlocking_next : [];
      const totalCd = toNumber(inst.cooldown_total);
      const shown = Math.min(items.length, 3);
      const tally = totalCd === 0
        ? '0 in cooldown'
        : `${shown} of ${totalCd} in cooldown`;
      if (items.length === 0) {
        return `
<div class="dash-unlocks">
  <p class="dash-unlocks__head">Cooldown schedule <span class="tally">${tally}</span></p>
  <p class="dash-unlocks--empty">Nothing queued. All caught up.</p>
</div>`;
      }
      const rows = items.slice(0, 3).map(function (row) {
        const title = row.item_label || `Item ${row.item_id}`;
        return `
      <div class="dash-unlocks__row">
        ${searchKindIcon(row.last_search_kind)}
        <span class="dash-unlocks__title">${escHtml(title)}</span>
        <span class="dash-unlocks__time">${escHtml(formatTimeUntil(row.unlock_at))}</span>
      </div>`;
      }).join('');
      return `
<div class="dash-unlocks">
  <p class="dash-unlocks__head">Cooldown schedule <span class="tally">${tally}</span></p>
  <div class="dash-unlocks__list">${rows}
  </div>
</div>`;
    }

    function renderPolicyChips(inst) {
      const chips = [];
      const sleep = toNumber(inst.sleep_interval_mins);
      const batch = toNumber(inst.batch_size);
      const cap   = toNumber(inst.hourly_cap);
      const cd    = toNumber(inst.cooldown_days);
      const grace = toNumber(inst.post_release_grace_hrs);
      const queue = toNumber(inst.queue_limit);

      chips.push({
        label: 'Every',
        value: `${sleep}m`,
        tip:   `Cycle interval: runs every ${sleep} minute${sleep === 1 ? '' : 's'}`,
      });
      chips.push({
        label: 'Batch',
        value: String(batch),
        tip:   `Batch size: up to ${batch} item${batch === 1 ? '' : 's'} dispatched per cycle`,
      });
      chips.push({
        label: 'Cap/h',
        value: String(cap),
        tip:   `Hourly cap: at most ${cap} search${cap === 1 ? '' : 'es'} per hour`,
      });
      chips.push({
        label: 'CD',
        value: `${cd}d`,
        tip:   `Missing cooldown: ${cd} day${cd === 1 ? '' : 's'} after a search before the same item can be re-searched`,
      });
      chips.push({
        label: 'Grace',
        value: `${grace}h`,
        tip:   `Post-release grace: wait ${grace} hour${grace === 1 ? '' : 's'} after release before the first search`,
      });
      if (queue > 0) {
        chips.push({
          label: 'Queue',
          value: `≤${queue}`,
          tip:   `Queue backpressure: skip cycle when the arr download queue has ${queue}+ items`,
        });
      }
      if (inst.upgrade_enabled) {
        const upgDays = toNumber(inst.upgrade_cooldown_days) || 90;
        chips.push({
          label: 'Upgrade',
          value: `${upgDays}d`,
          state: 'on',
          tip:   `Upgrade pass enabled: ${upgDays}-day cooldown on upgrade searches`,
        });
      }
      chips.push({
        label: 'Cutoff',
        value: inst.cutoff_enabled ? 'on' : 'off',
        state: inst.cutoff_enabled ? 'on' : 'off',
        tip:   inst.cutoff_enabled
          ? `Cutoff-unmet pass enabled (separate ${toNumber(inst.cooldown_days)}d+ cooldown window)`
          : 'Cutoff-unmet pass disabled',
      });

      return chips.map(function (chip) {
        const dataState = chip.state ? ` data-state="${chip.state}"` : '';
        return `
        <span class="dash-policy__chip" title="${escHtml(chip.tip)}">
          <span class="dash-policy__label">${escHtml(chip.label)}</span><span class="dash-policy__value"${dataState}>${escHtml(chip.value)}</span>
        </span>`;
      }).join('');
    }

    // Stale-snapshot pill.  Surfaced when the instance's
    // monitored_total / unreleased_count were last refreshed more
    // than 15 minutes ago (10-minute scheduled refresh + 5-minute
    // grace for a missed tick).  Flags the condition where an *arr
    // goes silent and the dashboard's Wanted/Eligible numbers freeze
    // without any visual signal.  Returns '' when fresh so a healthy
    // card stays uncluttered.
    const SNAPSHOT_STALE_THRESHOLD_MS = 15 * 60 * 1000;
    function renderStaleSnapshotPill(inst) {
      if (!inst.enabled || inst.active_error) return '';
      const ts = inst.snapshot_refreshed_at;
      if (!ts) return '';
      const refreshed = Date.parse(ts);
      if (Number.isNaN(refreshed)) return '';
      const age = Date.now() - refreshed;
      if (age <= SNAPSHOT_STALE_THRESHOLD_MS) return '';
      const label = `counts ${formatTimeAgo(ts)} old`;
      return `
  <span class="dash-card__stale" title="Wanted / Eligible counts last refreshed ${escHtml(formatTimeAgo(ts))}. Normal refresh cadence is 10 minutes; longer gaps usually mean the *arr is unreachable.">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/>
      <path d="M12 9v4"/>
      <path d="M12 17h.01"/>
    </svg>
    <span>${escHtml(label)}</span>
  </span>`;
    }

    function renderCardFooter(inst) {
      const disabled = !inst.enabled;
      const offline = !disabled && !!inst.active_error;
      const runNowDisabled = disabled || offline ? ' disabled' : '';
      const lastDispatch = inst.last_dispatch_at ? formatTimeAgo(inst.last_dispatch_at) : '';
      let footText;
      if (disabled) {
        footText = `last dispatch ${escHtml(lastDispatch || 'never')} <span class="sep">·</span> paused`;
      } else if (offline) {
        const since = inst.active_error ? formatTimeAgo(inst.active_error.timestamp) : '';
        footText = `offline since ${escHtml(since || 'just now')}`;
      } else {
        const sleep = toNumber(inst.sleep_interval_mins);
        // Countdown anchor preference order:
        //   1. last_cycle_end  - supervisor's in-memory cycle-end
        //      timestamp, advances once per cycle regardless of
        //      whether any rows were written.  Solves the
        //      "everything LRU-throttled" case that froze the
        //      previous last_activity_at anchor.
        //   2. last_activity_at - newest searched/skipped/error row.
        //      Used on supervisor restart before a cycle has
        //      completed, and any time the supervisor isn't available
        //      (e.g. a stub deployment without an engine).
        //   3. last_dispatch_at - newest searched row only.  Final
        //      fallback; same field the very first iteration of this
        //      countdown used before we realised it froze on skip-
        //      only cycles.
        const anchor =
          inst.last_cycle_end
          || inst.last_activity_at
          || inst.last_dispatch_at
          || '';
        footText = `last dispatch ${escHtml(lastDispatch || 'never')} <span class="sep">·</span>`
          + ` next patrol <span data-next-patrol`
          + ` data-anchor="${escHtml(anchor)}"`
          + ` data-sleep-min="${sleep}">${sleep}m</span>`;
      }
      return `
<div class="dash-card__foot">
  <span class="dash-card__foot-text">${footText}</span>
  ${renderStaleSnapshotPill(inst)}
  <button class="dash-run-now"
          hx-post="/api/instances/${inst.id}/run-now"
          hx-swap="none"
          data-run-now-btn="true"
          data-instance-id="${inst.id}"
          title="Trigger search now"${runNowDisabled}>
    <span data-run-now-icon aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 5a2 2 0 0 1 3.008-1.728l11.997 6.998a2 2 0 0 1 .003 3.458l-12 7A2 2 0 0 1 5 19z"/></svg></span>
    <span data-run-now-label>Run now</span>
  </button>
</div>`;
    }

    function renderCard(inst) {
      const disabled = !inst.enabled;
      const offline = !disabled && !!inst.active_error;
      const typeAttr = inst.type ? ` data-type="${escHtml(inst.type)}"` : '';
      const disabledAttr = disabled ? ' data-disabled="true"' : '';
      const wantedVal = toNumber(inst.monitored_total);
      const bd = inst.cooldown_breakdown || { missing: 0, cutoff: 0, upgrade: 0 };
      // Per-card Eligible mirrors the library-health formula: upgrade
      // cooldowns are tracked separately from monitored_total (the
      // upgrade pool is has_file + cutoff_met items), so only subtract
      // the in-monitored cooldown buckets here.
      const gated = toNumber(bd.missing) + toNumber(bd.cutoff);
      const unr = toNumber(inst.unreleased_count);
      const eligibleVal = Math.max(0, wantedVal - gated - unr);
      // "Searched" on the card shows the rolling 24-hour dispatch
      // count (the rate that matters for indexer pressure).  The
      // cumulative lifetime count rides on a hover tooltip attached
      // to the whole stat cell (rendered below) rather than just the
      // number, so hovering anywhere over the "Searched" label,
      // value, or the padding around them reveals the total.
      // Suppressed when the two numbers are equal, which is
      // trivially true on fresh installs where everything still fits
      // inside the 24-hour window.
      const searched24hVal = toNumber(inst.searched_24h);
      const lifetimeVal = toNumber(inst.lifetime_searched);
      const lifetimeTip = lifetimeVal > 0 && lifetimeVal !== searched24hVal
        ? ` data-tip="Lifetime: ${lifetimeVal} search${lifetimeVal === 1 ? '' : 'es'}"`
        : '';

      const wantedText = offline || disabled
        ? `<dd class="dash-card__stat-value dash-card__stat-value--muted">${wantedVal || '-'}</dd>`
        : `<dd class="dash-card__stat-value">${wantedVal}</dd>`;
      const eligibleText = offline
        ? `<dd class="dash-card__stat-value dash-card__stat-value--muted">-</dd>`
        : disabled
          ? `<dd class="dash-card__stat-value dash-card__stat-value--muted">-</dd>`
          : `<dd class="dash-card__stat-value dash-card__stat-value--eligible">${eligibleVal}</dd>`;
      const searchedText = offline || disabled
        ? `<dd class="dash-card__stat-value dash-card__stat-value--muted">${searched24hVal}</dd>`
        : `<dd class="dash-card__stat-value dash-card__stat-value--searched">${searched24hVal}</dd>`;

      return `
<article class="dash-card"${typeAttr}${disabledAttr}>
  <header class="dash-card__head">
    <div>
      <p class="dash-card__eyebrow">${escHtml(typeEyebrowLabel(inst.type))}</p>
      <p class="dash-card__name">${escHtml(inst.name)}</p>
    </div>
    ${renderStatusPill(inst)}
  </header>
  <dl class="dash-card__stats">
    <div data-tip="Missing and Cutoff Unmet items reported by this instance.">
      <dt class="dash-card__stat-label">Wanted</dt>
      ${wantedText}
    </div>
    <div data-tip="Items Houndarr can search now: Wanted minus items on cooldown or not yet released.">
      <dt class="dash-card__stat-label">Eligible</dt>
      ${eligibleText}
    </div>
    <div${lifetimeTip}>
      <dt class="dash-card__stat-label">Searched</dt>
      ${searchedText}
    </div>
  </dl>
  ${renderUnlockPanel(inst)}
  ${renderBudgetBar(inst)}
  <p class="dash-policy">${renderPolicyChips(inst)}
  </p>
  ${renderCardFooter(inst)}
</article>`;
    }

    function mountTopSection(host, markup) {
      // Parse the rendered markup via a <template> then adopt the
      // resulting nodes.  Avoids Element.innerHTML assignment on the
      // live DOM (all user-controlled values already pass through
      // escHtml in the renderers above).
      const tpl = document.createElement('template');
      // eslint-disable-next-line no-unsanitized/property
      tpl.innerHTML = markup;
      host.replaceChildren(...tpl.content.childNodes);
      // Register any hx-* attributes on the new nodes so HTMX handles
      // clicks on the View-logs link and the + Add Instance link.
      if (window.htmx && typeof window.htmx.process === 'function') {
        window.htmx.process(host);
      }
      // Bind tooltip triggers on the freshly-mounted subtree.  This
      // path replaces DOM directly via replaceChildren rather than
      // through an HTMX swap, so the htmx:afterSwap listener inside
      // tooltip.js never fires for it; call initTooltips explicitly
      // so the library-health stat tooltips and instance-card
      // Wanted / Eligible / Searched / Hourly budget tooltips bind
      // on first paint and every /api/status poll.
      if (typeof window.initTooltips === 'function') {
        window.initTooltips(host);
      }
    }

    // Shared empty-dashboard markup, reused by both the inline
    // hydrate path and the HTMX beforeSwap path.
    const EMPTY_DASHBOARD_MARKUP = `
<section class="dash-empty-state" aria-label="No instances">
  <span class="dash-empty-state__icon" aria-hidden="true"></span>
  <p class="dash-empty-state__title">No instances configured</p>
  <p class="dash-empty-state__body">
    Add a Sonarr, Radarr, Lidarr, Readarr, or Whisparr instance to start patrolling
    your library for missing and cutoff-unmet media.
  </p>
  <a class="dash-empty-state__cta" href="/settings"
     hx-get="/settings" hx-target="#app-content" hx-swap="innerHTML" hx-push-url="true">
    + Add your first instance
  </a>
</section>`;

    function renderEnvelope(payload) {
      const instances = (payload && Array.isArray(payload.instances)) ? payload.instances : [];
      const recentSearches = (payload && Array.isArray(payload.recent_searches))
        ? payload.recent_searches
        : [];
      const topMarkup = renderTopSection(instances, recentSearches);
      const gridMarkup = instances.length === 0
        ? EMPTY_DASHBOARD_MARKUP
        : `<div class="dash-grid">${instances.map(renderCard).join('')}</div>`;
      return { topMarkup, gridMarkup };
    }

    // Initial hydration from the inline <script id="dash-initial-status">
    // blob the dashboard page ships.  Writes both hosts synchronously
    // so the shell-content-enter animation fires with content already
    // in place, matching the logs and settings entrance.
    const initialStatusNode = document.getElementById('dash-initial-status');
    if (initialStatusNode) {
      let initialPayload = null;
      try { initialPayload = JSON.parse(initialStatusNode.textContent || '{}'); } catch { initialPayload = null; }
      if (initialPayload) {
        const { topMarkup, gridMarkup } = renderEnvelope(initialPayload);
        const topHost = document.getElementById('dash-top');
        const gridHost = document.getElementById('instance-grid');
        if (topHost) mountTopSection(topHost, topMarkup);
        if (gridHost) mountTopSection(gridHost, gridMarkup);
      }
    }

    document.body.addEventListener(
      'htmx:beforeSwap',
      function (evt) {
        if (evt.detail.target.id !== 'instance-grid') return;
        // The run-now button and any future sibling controls inside
        // #instance-grid inherit `hx-target="#instance-grid"` via HTMX
        // target inheritance. Without this path guard, their responses
        // (e.g. the 202 {"status":"accepted"} from /api/instances/:id/
        // run-now) get parsed as a status envelope: `instances` is
        // undefined, renders as empty, and the top section flashes to
        // "No hounds on patrol" until the next /api/status poll.
        const path = (evt.detail.pathInfo && evt.detail.pathInfo.requestPath) || '';
        if (!path.startsWith('/api/status')) return;

        let payload;
        try { payload = JSON.parse(evt.detail.serverResponse); } catch { return; }
        const { topMarkup, gridMarkup } = renderEnvelope(payload);
        const topHost = document.getElementById('dash-top');
        if (topHost) mountTopSection(topHost, topMarkup);
        evt.detail.serverResponse = gridMarkup;
      },
      { signal },
    );

    function escHtml(s) {
      return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }

    document.body.addEventListener(
      'htmx:beforeRequest',
      function (evt) {
        const triggerEl = evt.detail.elt;
        if (!triggerEl || !triggerEl.matches('[data-run-now-btn="true"]')) return;
        triggerEl.dataset.runNowStartedAt = String(Date.now());
        setRunNowButtonState(triggerEl, 'running');
      },
      { signal },
    );

    document.body.addEventListener(
      'htmx:afterRequest',
      function (evt) {
        const triggerEl = evt.detail.elt;
        if (!triggerEl || !triggerEl.matches('[data-run-now-btn="true"]')) return;
        const statusCode = evt.detail.xhr ? evt.detail.xhr.status : 0;
        completeRunNowRequest(triggerEl, statusCode);
      },
      { signal },
    );

    // Live "next patrol" countdown.  Renders on every [data-next-patrol]
    // element once per second, computing `next_patrol_at = anchor +
    // sleep_interval_mins * 60s` from data attributes written server-
    // side via the envelope.  The anchor is `last_activity_at` (newest
    // searched/skipped/error row, which advances at the end of every
    // cycle whether items dispatched or all got skipped); falling
    // back to `last_dispatch_at` and then to a static label if neither
    // is present.  Transitions to "running..." when the timer hits
    // zero and stays there until the next 30s status poll refreshes
    // the anchor.
    function formatNextPatrol(anchor, sleepMin) {
      if (sleepMin <= 0) return '-';
      if (!anchor) return `${sleepMin}m`;
      const last = Date.parse(anchor);
      if (Number.isNaN(last)) return `${sleepMin}m`;
      const remaining = (last + sleepMin * 60_000) - Date.now();
      if (remaining <= 0) return 'running...';
      if (remaining <= 60_000) return `${Math.ceil(remaining / 1000)}s`;
      return `${Math.ceil(remaining / 60_000)}m`;
    }
    function tickNextPatrol() {
      document.querySelectorAll('[data-next-patrol]').forEach(function (el) {
        const nextText = formatNextPatrol(
          el.dataset.anchor || '',
          Number(el.dataset.sleepMin) || 0,
        );
        if (el.textContent !== nextText) el.textContent = nextText;
      });
    }
    const nextPatrolInterval = setInterval(tickNextPatrol, 1000);
    signal.addEventListener('abort', () => clearInterval(nextPatrolInterval));
    tickNextPatrol();
}

// Direct load.
if (document.querySelector('[data-page-key="dashboard"]')) {
  initDashboardPage();
}

// HTMX navigation.
document.body.addEventListener('htmx:afterSwap', (evt) => {
  if (
    evt.detail?.target?.id === 'app-content' &&
    document.querySelector('[data-page-key="dashboard"]')
  ) {
    initDashboardPage();
  }
});
