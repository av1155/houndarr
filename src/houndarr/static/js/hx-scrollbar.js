// Custom overlay scrollbar. Paired with the hidden native scrollbar in
// app.css (see that comment for the reserved-gutter strip-bug). One
// module drives two bar flavours:
//   - `.hx-scrollbar--window` for the page-level window scroll (static
//     element in base.html).
//   - `.hx-scrollbar--modal`  for each <dialog> with a child marked
//     `[data-modal-scroll]`; injected on open, torn down on close.
// Thumbs are click-and-drag via `hxAttachThumbDrag`; the track itself
// stays `pointer-events: none` so content flush with the right edge
// (e.g. form inputs in the add-instance modal) still receives clicks.

// Drag-scroll helper. `read()` returns the current scroll state of the
// target; `apply(newScrollTop)` sets it. Shared by the window bar and
// per-dialog modal bars so both behave identically on pointer drag.
// Pointer capture keeps move events flowing even when the cursor leaves
// the 6px thumb.
function hxAttachThumbDrag(thumb, read, apply) {
  var startY = 0;
  var startScroll = 0;
  var activePointerId = null;

  thumb.addEventListener('pointerdown', function (e) {
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    e.preventDefault();
    var state = read();
    startY = e.clientY;
    startScroll = state.scrollTop;
    activePointerId = e.pointerId;
    try { thumb.setPointerCapture(activePointerId); } catch (_) { /* noop */ }
    thumb.classList.add('is-dragging');
  });

  thumb.addEventListener('pointermove', function (e) {
    if (activePointerId === null || e.pointerId !== activePointerId) return;
    var state = read();
    var thumbTravel = state.viewH - state.thumbH;
    var scrollRange = state.scrollH - state.viewH;
    if (thumbTravel <= 0 || scrollRange <= 0) return;
    var dy = e.clientY - startY;
    var next = startScroll + (dy * scrollRange) / thumbTravel;
    apply(Math.max(0, Math.min(scrollRange, next)));
  });

  function end(e) {
    if (activePointerId === null || e.pointerId !== activePointerId) return;
    try { thumb.releasePointerCapture(activePointerId); } catch (_) { /* noop */ }
    activePointerId = null;
    thumb.classList.remove('is-dragging');
  }
  thumb.addEventListener('pointerup', end);
  thumb.addEventListener('pointercancel', end);
}

// Window bar: sizes + positions `.hx-scrollbar__thumb` against window
// scroll. Stays visible the whole time the page is scrollable (no fade).
(function () {
  var bar = document.querySelector('.hx-scrollbar--window');
  var thumb = bar && bar.querySelector('.hx-scrollbar__thumb');
  if (!bar || !thumb) return;

  var rafId = null;
  var MIN_THUMB_PX = 32;

  function update() {
    rafId = null;
    var doc = document.documentElement;
    var viewH = window.innerHeight;
    var docH = Math.max(doc.scrollHeight, document.body.scrollHeight);
    if (docH - viewH < MIN_THUMB_PX / 2) {
      // Ignore sub-pixel rounding and incidental border/padding overflow
      // that is not meaningfully scrollable. No point showing a thumb
      // whose travel distance is smaller than the thumb's own minimum
      // height.
      bar.classList.remove('is-visible');
      return;
    }
    bar.classList.add('is-visible');
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

  window.addEventListener('scroll', schedule, { passive: true });
  window.addEventListener('resize', schedule, { passive: true });

  // Recalculate when HTMX swaps change content height, or when any other
  // DOM mutation (modals, async content) changes scrollable area.
  document.body.addEventListener('htmx:afterSettle', schedule);
  document.body.addEventListener('htmx:afterSwap', schedule);
  var observer = new MutationObserver(schedule);
  observer.observe(document.body, { childList: true, subtree: true });
  // ResizeObserver catches CSS-animated height changes (e.g. the Settings
  // admin-dropdown collapse) that do not trigger a DOM mutation. Without
  // this the thumb keeps its old size after the panel shrinks and
  // technically scrolling stops being possible, leaving a phantom bar.
  var resizeObserver = new ResizeObserver(schedule);
  resizeObserver.observe(document.body);

  hxAttachThumbDrag(
    thumb,
    function () {
      var doc = document.documentElement;
      return {
        scrollTop: window.scrollY,
        scrollH: Math.max(doc.scrollHeight, document.body.scrollHeight),
        viewH: window.innerHeight,
        thumbH: thumb.offsetHeight,
      };
    },
    function (top) {
      window.scrollTo({ top: top, left: 0, behavior: 'auto' });
    }
  );

  // Defer the initial measurement so layout has settled.
  window.requestAnimationFrame(schedule);
})();

// Per-dialog overlay: pins a thumb over the scroll region of any
// <dialog> with a `[data-modal-scroll]` child, only while that dialog
// is open. Detaches the moment `.is-closing` appears so the bar
// vanishes before the modal's out-animation (no clip-in flash) and
// never paints while the dialog is hidden.
(function () {
  var INSTANCE_KEY = '__hxModalScrollbar';
  var MIN_THUMB_PX = 32;

  function attach(dialog, target) {
    var bar = document.createElement('div');
    bar.className = 'hx-scrollbar hx-scrollbar--modal';
    bar.setAttribute('aria-hidden', 'true');
    var thumb = document.createElement('div');
    thumb.className = 'hx-scrollbar__thumb';
    bar.appendChild(thumb);
    // Inside the <dialog> so the bar shares the dialog's top layer
    // (showModal() renders the dialog above every fixed-position sibling
    // in document order; a bar appended to document.body would be
    // obscured by the backdrop).
    dialog.appendChild(bar);

    var rafId = null;

    function update() {
      rafId = null;
      // offset* gives unscaled coords relative to the dialog. Using
      // getBoundingClientRect here would drift while the dialog's
      // open animation interpolates scale, since visual rects include
      // the transform but inline styles are applied pre-transform.
      bar.style.top = target.offsetTop + 'px';
      bar.style.height = target.offsetHeight + 'px';
      bar.style.right =
        (dialog.clientWidth - target.offsetLeft - target.offsetWidth + 2) + 'px';
      var scrollH = target.scrollHeight;
      var viewH = target.clientHeight;
      if (scrollH - viewH < MIN_THUMB_PX / 2) {
        bar.classList.remove('is-visible');
        return;
      }
      bar.classList.add('is-visible');
      var ratio = viewH / scrollH;
      var thumbH = Math.max(MIN_THUMB_PX, Math.round(ratio * viewH));
      var maxThumbTop = viewH - thumbH;
      var scrollRatio = target.scrollTop / (scrollH - viewH);
      var thumbY = Math.round(scrollRatio * maxThumbTop);
      thumb.style.height = thumbH + 'px';
      thumb.style.transform = 'translateY(' + thumbY + 'px)';
    }

    function schedule() {
      if (rafId !== null) return;
      rafId = window.requestAnimationFrame(update);
    }

    target.addEventListener('scroll', schedule, { passive: true });
    window.addEventListener('resize', schedule, { passive: true });
    // Form rows grow + shrink as the user edits; keep the thumb sized.
    var observer = new MutationObserver(schedule);
    observer.observe(target, { childList: true, subtree: true });
    // ResizeObserver catches CSS-animated height changes inside the
    // modal (collapsible sections, async-loaded form rows) that do not
    // trigger a DOM mutation.
    var resizeObserver = new ResizeObserver(schedule);
    resizeObserver.observe(target);

    hxAttachThumbDrag(
      thumb,
      function () {
        return {
          scrollTop: target.scrollTop,
          scrollH: target.scrollHeight,
          viewH: target.clientHeight,
          thumbH: thumb.offsetHeight,
        };
      },
      function (top) {
        target.scrollTop = top;
      }
    );

    schedule();

    return function detach() {
      if (rafId !== null) window.cancelAnimationFrame(rafId);
      target.removeEventListener('scroll', schedule);
      window.removeEventListener('resize', schedule);
      observer.disconnect();
      resizeObserver.disconnect();
      bar.remove();
    };
  }

  function sync(dialog) {
    var target = dialog.querySelector('[data-modal-scroll]');
    if (!target) return;
    // `.is-closing` is added at the start of the close animation, before
    // the `open` attribute is cleared; treat it as closed so the overlay
    // is gone before the dialog fades.
    var isClosing = dialog.classList.contains('is-closing');
    var isOpen = dialog.hasAttribute('open') && !isClosing;
    if (isOpen && !dialog[INSTANCE_KEY]) {
      dialog[INSTANCE_KEY] = attach(dialog, target);
    } else if (!isOpen && dialog[INSTANCE_KEY]) {
      dialog[INSTANCE_KEY]();
      dialog[INSTANCE_KEY] = null;
    }
  }

  function wire(dialog) {
    if (dialog.__hxWired) return;
    dialog.__hxWired = true;
    new MutationObserver(function () { sync(dialog); }).observe(dialog, {
      attributes: true,
      attributeFilter: ['open', 'class'],
    });
    sync(dialog);
  }

  function scan() {
    var dialogs = document.querySelectorAll('dialog');
    for (var i = 0; i < dialogs.length; i += 1) wire(dialogs[i]);
  }

  scan();
  // HTMX swaps can inject or replace dialog markup (e.g. add-instance
  // modal body is swapped in from the server).
  document.body.addEventListener('htmx:afterSettle', scan);
  document.body.addEventListener('htmx:afterSwap', scan);
})();
