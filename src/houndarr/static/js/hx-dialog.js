// Shared animated-close helper for <dialog> elements that use the
// two-phase `.is-closing` pattern (CSS animation keyed off the class,
// finalize `close()` scheduled for the end of the animation). Extracted
// from the changelog modal + add-instance modal controllers; both used
// to hand-roll the same sequence.
//
// Usage:
//   hxCloseDialogAnimated(dialog, closeAnimationMs, () => {
//     // modal-specific cleanup (focus restore, content reset, etc.)
//   });
//
// The helper skips the animation when `prefers-reduced-motion: reduce`
// is set, guards against reentrant close calls via a flag stashed on
// the dialog element, and always ends with `document.body.style.overflow`
// cleared so the scroll lock that accompanied showModal is released.

function hxCloseDialogAnimated(dialog, closeAnimationMs, onFinalize) {
  if (!dialog || !dialog.open || dialog.__hxClosing) return;

  const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const finalize = () => {
    dialog.classList.remove('is-closing');
    if (dialog.open) dialog.close();
    document.body.style.overflow = '';
    dialog.__hxClosing = false;
    if (typeof onFinalize === 'function') onFinalize();
  };

  if (prefersReducedMotion) {
    finalize();
    return;
  }

  dialog.__hxClosing = true;
  dialog.classList.add('is-closing');
  window.setTimeout(finalize, closeAnimationMs);
}
