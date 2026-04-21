// logs.js — Logs page controller. Previously inline in
// templates/partials/pages/logs_content.html. Re-runs on every HTMX
// swap that lands #app-content on the Logs page, using the
// AbortController pattern from settings.js / dashboard.js.

function initLogsPage() {
  window.__houndarrLogsPageController?.abort();
  const controller = new AbortController();
  window.__houndarrLogsPageController = controller;
  const { signal } = controller;

    // -------------------------------------------------------------------------
    // Timestamp formatting
    // -------------------------------------------------------------------------

    const formatLocalTimestamp =
      window.houndarrClientHelpers?.formatLocalTimestamp ||
      function (isoTimestamp) {
        if (!isoTimestamp) {
          return 'N/A';
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
      };

    function formatVisibleLogTimestamps(root = document) {
      root.querySelectorAll('[data-log-ts]').forEach((el) => {
        if (el.getAttribute('data-ts-formatted') === 'true') {
          return;
        }
        const ts = el.getAttribute('data-log-ts') || '';
        el.textContent = formatLocalTimestamp(ts);
        el.setAttribute('data-ts-formatted', 'true');
      });
    }

    // -------------------------------------------------------------------------
    // Row data extraction helpers
    // -------------------------------------------------------------------------

    function getRowCells(row) {
      return {
        timestamp: row.querySelector('[data-col="timestamp"]')?.textContent?.trim() || '',
        instance: row.querySelector('[data-col="instance"]')?.textContent?.trim() || '',
        action: row.querySelector('[data-col="action"]')?.textContent?.trim() || '',
        type: row.querySelector('[data-col="type"]')?.textContent?.trim() || '',
        kind: row.querySelector('[data-col="kind"]')?.textContent?.trim() || '',
        trigger: row.querySelector('[data-col="trigger"]')?.textContent?.trim() || '',
        cycle: row.querySelector('[data-col="cycle"]')?.textContent?.trim() || '',
        cycle_outcome: row.querySelector('[data-col="outcome"]')?.textContent?.trim() || '',
        media: row.querySelector('[data-col="media"]')?.textContent?.replace(/\s+/g, ' ').trim() || '',
        reason: row.querySelector('[data-col="reason"]')?.textContent?.trim() || '',
      };
    }

    // -------------------------------------------------------------------------
    // Copy format implementations
    // -------------------------------------------------------------------------

    const _COPY_HEADERS = [
      'Timestamp (Local)', 'Instance', 'Action', 'Type', 'Kind',
      'Trigger', 'Cycle', 'Cycle outcome', 'Media', 'Reason / Message',
    ];

    function _tsvCell(value) {
      // Replace tabs and newlines with spaces; preserve other content as-is.
      return String(value).replaceAll('\t', ' ').replaceAll('\n', ' ').replaceAll('\r', ' ');
    }

    function buildTsv(rows) {
      const lines = [_COPY_HEADERS.map(_tsvCell).join('\t')];
      rows.forEach((row) => {
        const c = getRowCells(row);
        lines.push([
          c.timestamp, c.instance, c.action, c.type, c.kind,
          c.trigger, c.cycle, c.cycle_outcome, c.media, c.reason,
        ].map(_tsvCell).join('\t'));
      });
      return lines.join('\n');
    }

    function _escapePipes(value) {
      return String(value).replaceAll('|', '\\|').replaceAll('\n', ' ');
    }

    function _mdCell(value) {
      // Use a hyphen for blank cells.
      const str = String(value);
      return _escapePipes(str || '-');
    }

    function buildMarkdown(rows) {
      const divider = _COPY_HEADERS.map(() => '---');
      const lines = [
        `| ${_COPY_HEADERS.join(' | ')} |`,
        `| ${divider.join(' | ')} |`,
      ];
      rows.forEach((row) => {
        const c = getRowCells(row);
        const values = [
          c.timestamp, c.instance, c.action, c.type, c.kind,
          c.trigger, c.cycle, c.cycle_outcome, c.media, c.reason,
        ].map(_mdCell);
        lines.push(`| ${values.join(' | ')} |`);
      });
      return lines.join('\n');
    }

    function buildJson(rows) {
      const data = rows.map((row) => {
        const c = getRowCells(row);
        return {
          timestamp: c.timestamp || null,
          instance: c.instance || null,
          action: c.action || null,
          type: c.type || null,
          kind: c.kind || null,
          trigger: c.trigger || null,
          cycle: c.cycle || null,
          cycle_outcome: c.cycle_outcome || null,
          media: c.media || null,
          reason: c.reason || null,
        };
      });
      return JSON.stringify(data, null, 2);
    }

    function buildPlainText(rows) {
      const lines = [];
      rows.forEach((row) => {
        const c = getRowCells(row);
        const parts = [];
        if (c.timestamp) parts.push(c.timestamp);
        if (c.instance) parts.push(`[${c.instance}]`);
        if (c.action) parts.push(c.action.toUpperCase());
        if (c.type) parts.push(c.type);
        if (c.kind) parts.push(`kind:${c.kind}`);
        if (c.trigger) parts.push(`trigger:${c.trigger}`);
        if (c.cycle) parts.push(`cycle:${c.cycle}`);
        if (c.cycle_outcome) parts.push(`outcome:${c.cycle_outcome}`);
        if (c.media) parts.push(c.media);
        if (c.reason) parts.push(`reason: ${c.reason}`);
        lines.push(parts.join('  '));
      });
      return lines.join('\n');
    }

    function buildCopyText(format) {
      const rows = Array.from(document.querySelectorAll('#log-tbody tr[data-log-row="true"]'));
      if (rows.length === 0) {
        return null;
      }
      if (format === 'tsv') return buildTsv(rows);
      if (format === 'markdown') return buildMarkdown(rows);
      if (format === 'json') return buildJson(rows);
      if (format === 'text') return buildPlainText(rows);
      return buildTsv(rows);
    }

    // -------------------------------------------------------------------------
    // Summary state
    // -------------------------------------------------------------------------

    function setSummaryValue(id, value) {
      const el = document.getElementById(id);
      if (!el) {
        return;
      }
      el.textContent = String(value);
    }

    const summaryState = {
      totalRows: 0,
      searchedRows: 0,
      skippedRows: 0,
      errorRows: 0,
      infoRows: 0,
      totalCycles: 0,
      searchedCycles: 0,
      skipOnlyCycles: 0,
      cycleProgressById: new Map(),
    };

    function mergeCycleProgress(current, incoming) {
      if (current === 'progress' || incoming === 'progress') {
        return 'progress';
      }
      if (current) {
        return current;
      }
      return incoming || 'unknown';
    }

    function adjustCycleBucket(status, delta) {
      if (status === 'progress') {
        summaryState.searchedCycles += delta;
        return;
      }
      if (status === 'no_progress') {
        summaryState.skipOnlyCycles += delta;
        return;
      }
    }

    function accountSummaryRow(row) {
      if (row.getAttribute('data-summary-accounted') === 'true') {
        return;
      }

      row.setAttribute('data-summary-accounted', 'true');
      summaryState.totalRows += 1;

      const action = (row.getAttribute('data-action') || '').toLowerCase();
      if (action === 'searched') {
        summaryState.searchedRows += 1;
      } else if (action === 'skipped') {
        summaryState.skippedRows += 1;
      } else if (action === 'error') {
        summaryState.errorRows += 1;
      } else if (action === 'info') {
        summaryState.infoRows += 1;
      }

      const cycleId = row.getAttribute('data-cycle-id') || '';
      const cycleProgress = row.getAttribute('data-cycle-progress') || '';
      if (!cycleId) {
        return;
      }

      const previous = summaryState.cycleProgressById.get(cycleId) || '';
      const next = mergeCycleProgress(previous, cycleProgress);
      if (!previous) {
        summaryState.totalCycles += 1;
        summaryState.cycleProgressById.set(cycleId, next);
        adjustCycleBucket(next, 1);
        return;
      }

      if (previous !== next) {
        summaryState.cycleProgressById.set(cycleId, next);
        adjustCycleBucket(previous, -1);
        adjustCycleBucket(next, 1);
      }
    }

    function renderSummaryState() {
      setSummaryValue('summary-total-rows', summaryState.totalRows);
      setSummaryValue('summary-total-cycles', summaryState.totalCycles);
      setSummaryValue('summary-searched-cycles', summaryState.searchedCycles);
      setSummaryValue('summary-skip-cycles', summaryState.skipOnlyCycles);
      setSummaryValue('summary-searched-rows', summaryState.searchedRows);
      setSummaryValue('summary-skipped-rows', summaryState.skippedRows);
      setSummaryValue('summary-error-rows', summaryState.errorRows);
      setSummaryValue('summary-info-rows', summaryState.infoRows);
    }

    function resetSummaryState() {
      summaryState.totalRows = 0;
      summaryState.searchedRows = 0;
      summaryState.skippedRows = 0;
      summaryState.errorRows = 0;
      summaryState.infoRows = 0;
      summaryState.totalCycles = 0;
      summaryState.searchedCycles = 0;
      summaryState.skipOnlyCycles = 0;
      summaryState.cycleProgressById.clear();
    }

    function rebuildSummaryFromVisibleRows() {
      resetSummaryState();
      document.querySelectorAll('#log-tbody tr[data-log-row="true"]').forEach((row) => {
        row.removeAttribute('data-summary-accounted');
        accountSummaryRow(row);
      });
      renderSummaryState();
    }

    function updateSummaryForAppendedRows(rows) {
      rows.forEach((row) => {
        accountSummaryRow(row);
      });
      renderSummaryState();
    }

    // -------------------------------------------------------------------------
    // Split-button copy dropdown controller
    // -------------------------------------------------------------------------

    // Collect all main buttons and all label spans across both placements.
    const copyMainBtns = Array.from(document.querySelectorAll('[data-copy-main="true"]'));
    const copyLabels = Array.from(document.querySelectorAll('.copy-visible-logs-label'));
    const copyChevronBtns = Array.from(document.querySelectorAll('[data-copy-chevron="true"]'));
    const copyDropdownMenus = Array.from(document.querySelectorAll('.copy-dropdown-menu'));
    const copyGroups = Array.from(document.querySelectorAll('[data-copy-group]'));

    let copyButtonTimer = null;
    let copyLabelSwapTimer = null;
    let dropdownOpen = false;

    function setCopyButtonLabel(nextLabel) {
      if (!copyLabels.length) {
        return;
      }

      if (copyLabels[0].textContent === nextLabel) {
        return;
      }

      if (copyLabelSwapTimer) {
        window.clearTimeout(copyLabelSwapTimer);
        copyLabelSwapTimer = null;
      }

      copyLabels.forEach((el) => el.classList.add('opacity-0'));
      copyLabelSwapTimer = window.setTimeout(() => {
        copyLabels.forEach((el) => {
          el.textContent = nextLabel;
          el.classList.remove('opacity-0');
        });
        copyLabelSwapTimer = null;
      }, 120);
    }

    function flashCopyButtonState(state) {
      if (!copyMainBtns.length) {
        return;
      }

      if (copyButtonTimer) {
        window.clearTimeout(copyButtonTimer);
        copyButtonTimer = null;
      }

      copyMainBtns.forEach((btn) => {
        btn.classList.remove('is-copy-success', 'is-copy-error');
      });

      if (state === 'success') {
        setCopyButtonLabel('Copied');
        copyMainBtns.forEach((btn) => btn.classList.add('is-copy-success'));
      } else if (state === 'error') {
        setCopyButtonLabel('Copy failed');
        copyMainBtns.forEach((btn) => btn.classList.add('is-copy-error'));
      } else {
        setCopyButtonLabel('Copy as TSV');
        return;
      }

      copyButtonTimer = window.setTimeout(() => {
        flashCopyButtonState('idle');
      }, 1200);
    }

    function closeDropdown() {
      dropdownOpen = false;
      copyDropdownMenus.forEach((menu) => menu.classList.add('hidden'));
      copyChevronBtns.forEach((btn) => {
        btn.setAttribute('aria-expanded', 'false');
        btn.querySelectorAll('.copy-chevron-icon').forEach((icon) => {
          icon.style.transform = '';
        });
      });
    }

    function openDropdown() {
      dropdownOpen = true;
      copyDropdownMenus.forEach((menu) => menu.classList.remove('hidden'));
      copyChevronBtns.forEach((btn) => {
        btn.setAttribute('aria-expanded', 'true');
        btn.querySelectorAll('.copy-chevron-icon').forEach((icon) => {
          icon.style.transform = 'rotate(180deg)';
        });
      });
      // Focus the first menu item in the first visible menu.
      const firstMenu = copyDropdownMenus.find((m) => !m.classList.contains('hidden'));
      if (firstMenu) {
        const firstItem = firstMenu.querySelector('[data-copy-format]');
        if (firstItem) firstItem.focus();
      }
    }

    function toggleDropdown() {
      if (dropdownOpen) {
        closeDropdown();
      } else {
        openDropdown();
      }
    }

    function performCopy(format) {
      const text = buildCopyText(format);
      if (text === null) {
        return;
      }
      // navigator.clipboard is only available in secure contexts (HTTPS or localhost).
      // Fall back to the legacy textarea + execCommand approach for plain HTTP access.
      if (navigator.clipboard) {
        navigator.clipboard
          .writeText(text)
          .then(() => {
            flashCopyButtonState('success');
          })
          .catch(() => {
            flashCopyButtonState('error');
          });
      } else {
        try {
          const ta = document.createElement('textarea');
          ta.value = text;
          ta.style.position = 'fixed';
          ta.style.opacity = '0';
          document.body.appendChild(ta);
          ta.focus();
          ta.select();
          document.execCommand('copy');
          document.body.removeChild(ta);
          flashCopyButtonState('success');
        } catch {
          flashCopyButtonState('error');
        }
      }
    }

    // Main button: copy TSV immediately, also close dropdown if open.
    copyMainBtns.forEach((btn) => {
      btn.addEventListener(
        'click',
        () => {
          if (dropdownOpen) {
            closeDropdown();
          }
          performCopy('tsv');
        },
        { signal },
      );
    });

    // Chevron: toggle dropdown.
    copyChevronBtns.forEach((btn) => {
      btn.addEventListener('click', toggleDropdown, { signal });
    });

    // Format menu items: copy in selected format, close dropdown.
    copyDropdownMenus.forEach((menu) => {
      menu.querySelectorAll('[data-copy-format]').forEach((item) => {
        item.addEventListener(
          'click',
          () => {
            const fmt = item.getAttribute('data-copy-format') || 'tsv';
            closeDropdown();
            performCopy(fmt);
          },
          { signal },
        );
      });

      // Keyboard navigation within the menu.
      menu.addEventListener(
        'keydown',
        (evt) => {
          const items = Array.from(menu.querySelectorAll('[data-copy-format]'));
          const focused = document.activeElement;
          const idx = items.indexOf(focused);

          if (evt.key === 'ArrowDown') {
            evt.preventDefault();
            const next = items[(idx + 1) % items.length];
            if (next) next.focus();
          } else if (evt.key === 'ArrowUp') {
            evt.preventDefault();
            const prev = items[(idx - 1 + items.length) % items.length];
            if (prev) prev.focus();
          } else if (evt.key === 'Escape' || evt.key === 'Tab') {
            evt.preventDefault();
            closeDropdown();
            // Return focus to the chevron of the group that owns this menu.
            const groupEl = menu.closest('[data-copy-group]');
            if (groupEl) {
              const chevron = groupEl.querySelector('[data-copy-chevron="true"]');
              if (chevron) chevron.focus();
            }
          }
        },
        { signal },
      );
    });

    // Close on Esc globally or outside click.
    document.addEventListener(
      'keydown',
      (evt) => {
        if (evt.key === 'Escape' && dropdownOpen) {
          closeDropdown();
        }
      },
      { signal },
    );

    document.addEventListener(
      'pointerdown',
      (evt) => {
        if (!dropdownOpen) {
          return;
        }
        const isInsideGroup = copyGroups.some((g) => g.contains(evt.target));
        if (!isInsideGroup) {
          closeDropdown();
        }
      },
      { signal },
    );

    // -------------------------------------------------------------------------
    // HTMX event handlers
    // -------------------------------------------------------------------------

    formatVisibleLogTimestamps(document);
    rebuildSummaryFromVisibleRows();

    document.body.addEventListener(
      'htmx:afterSwap',
      (evt) => {
        const target = evt.detail && evt.detail.target;
        if (!target) {
          return;
        }

        if (target.id === 'log-tbody') {
          formatVisibleLogTimestamps(target);
          rebuildSummaryFromVisibleRows();
          return;
        }

        if (target.id === 'pagination-row') {
          const appendedRows = Array.from(
            document.querySelectorAll('#log-tbody tr[data-log-row="true"].htmx-added'),
          );
          formatVisibleLogTimestamps(document);
          if (appendedRows.length > 0) {
            updateSummaryForAppendedRows(appendedRows);
          } else {
            rebuildSummaryFromVisibleRows();
          }
        }
      },
      { signal },
    );

    document.body.addEventListener(
      'htmx:beforeRequest',
      (evt) => {
        const requestPath = evt.detail?.pathInfo?.requestPath || '';
        if (!requestPath.includes('/api/logs/partial')) {
          return;
        }

        const shell = document.getElementById('logs-table-shell');
        if (shell) {
          shell.classList.add('is-loading');
        }
      },
      { signal },
    );

    document.body.addEventListener(
      'htmx:afterRequest',
      (evt) => {
        const requestPath = evt.detail?.pathInfo?.requestPath || '';
        if (!requestPath.includes('/api/logs/partial')) {
          return;
        }

        const shell = document.getElementById('logs-table-shell');
        if (shell) {
          shell.classList.remove('is-loading');
        }
      },
      { signal },
    );
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
