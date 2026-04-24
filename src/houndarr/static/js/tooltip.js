// Shared tooltip controller.  One floating panel at <body>, reused
// across every trigger on the page: saves a DOM node per trigger and
// keeps positioning logic in one place.  initTooltips(root) is
// idempotent; HTMX partial swaps call it again on the swapped subtree
// so newly-attached triggers pick up listeners without rebinding old
// ones.
//
// Two content modes:
//   * data-tip="Plain text"             inline string body
//   * data-tip-ref="<id>"               copies <template id="tip-<id>">
//                                       content into the panel
//
// Trigger nodes can be real <button class="tip-trigger"> elements
// (keyboard-focusable by default) or any element with data-tip for
// hover-only reveals (e.g. the dashboard's Searched value).

(function () {
  let panel = null;
  let caret = null;
  let contentHost = null;
  let activeTrigger = null;
  let hideTimer = null;

  function ensurePanel() {
    if (panel) return;
    panel = document.createElement("div");
    panel.id = "app-tooltip";
    panel.className = "app-tooltip";
    panel.setAttribute("role", "tooltip");
    caret = document.createElement("span");
    caret.className = "app-tooltip__caret";
    caret.setAttribute("aria-hidden", "true");
    contentHost = document.createElement("div");
    contentHost.className = "app-tooltip__content";
    panel.append(caret, contentHost);
    // Moving the pointer from the trigger onto the panel cancels the
    // pending hide so users can read multi-line content without it
    // disappearing under the cursor.
    panel.addEventListener("mouseenter", function () {
      clearTimeout(hideTimer);
    });
    panel.addEventListener("mouseleave", scheduleHide);
    document.body.appendChild(panel);
  }

  function loadContent(trigger) {
    const ref = trigger.dataset.tipRef;
    if (ref) {
      const tpl = document.getElementById("tip-" + ref);
      if (tpl && tpl.content) {
        contentHost.replaceChildren(...tpl.content.cloneNode(true).childNodes);
        return;
      }
    }
    const text = trigger.dataset.tip || "";
    const p = document.createElement("p");
    p.textContent = text;
    contentHost.replaceChildren(p);
  }

  function position(trigger) {
    const gutter = 8;
    const gap = 8;
    const rect = trigger.getBoundingClientRect();
    // Read offset* after content load forces a layout pass so the
    // measurements reflect the final panel size.
    const pw = panel.offsetWidth;
    const ph = panel.offsetHeight;
    // Prefer above the trigger; flip below when the top would clip.
    const placement = rect.top - ph - gap < gutter ? "bottom" : "top";
    const top =
      placement === "top" ? rect.top - ph - gap : rect.bottom + gap;
    let left = rect.left + rect.width / 2 - pw / 2;
    left = Math.max(gutter, Math.min(window.innerWidth - pw - gutter, left));
    panel.style.top = top + "px";
    panel.style.left = left + "px";
    panel.dataset.placement = placement;
    // Re-centre the caret on the trigger's horizontal midpoint after
    // any viewport clamping shifted the panel away from its ideal x.
    const caretX = rect.left + rect.width / 2 - left - 5;
    caret.style.left =
      Math.max(10, Math.min(pw - 20, caretX)) + "px";
  }

  function show(trigger) {
    clearTimeout(hideTimer);
    ensurePanel();
    loadContent(trigger);
    panel.classList.add("is-visible");
    position(trigger);
    activeTrigger = trigger;
    trigger.setAttribute("aria-describedby", "app-tooltip");
  }

  function hide() {
    if (!panel) return;
    panel.classList.remove("is-visible");
    if (activeTrigger) activeTrigger.removeAttribute("aria-describedby");
    activeTrigger = null;
  }

  function scheduleHide() {
    clearTimeout(hideTimer);
    // 150ms gives the pointer time to bridge the 8px gap between the
    // trigger and the panel without the panel flickering away.
    hideTimer = window.setTimeout(hide, 150);
  }

  function bind(el) {
    if (el.dataset.tipBound === "1") return;
    el.dataset.tipBound = "1";
    el.addEventListener("mouseenter", function () {
      show(el);
    });
    el.addEventListener("mouseleave", scheduleHide);
    el.addEventListener("focusin", function () {
      show(el);
    });
    el.addEventListener("focusout", scheduleHide);
    // Tap on mobile toggles; a second tap or a tap elsewhere hides.
    el.addEventListener("click", function (event) {
      event.preventDefault();
      if (activeTrigger === el) {
        hide();
        return;
      }
      show(el);
    });
  }

  function initTooltips(root) {
    const scope = root || document;
    scope
      .querySelectorAll("[data-tip], [data-tip-ref]")
      .forEach(bind);
  }

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") hide();
  });
  document.addEventListener("click", function (event) {
    if (!activeTrigger) return;
    if (activeTrigger.contains(event.target)) return;
    if (panel && panel.contains(event.target)) return;
    hide();
  });
  // Reposition an open tooltip if the viewport changes; scroll + resize
  // both shift the trigger's rect so the panel needs to follow.
  window.addEventListener(
    "scroll",
    function () {
      if (activeTrigger) position(activeTrigger);
    },
    { passive: true, capture: true },
  );
  window.addEventListener("resize", function () {
    if (activeTrigger) position(activeTrigger);
  });

  document.addEventListener("DOMContentLoaded", function () {
    initTooltips(document);
  });
  if (document.body) {
    document.body.addEventListener("htmx:afterSwap", function (event) {
      initTooltips(event.target);
    });
  }

  window.initTooltips = initTooltips;
})();
