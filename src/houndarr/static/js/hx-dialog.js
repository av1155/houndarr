// Shared animated-close helper for <dialog> elements that use the
// two-phase `.is-closing` pattern: CSS animation keyed off the class,
// native `close()` scheduled for the end of the animation. The helper
// skips the animation under `prefers-reduced-motion: reduce`, guards
// against reentrant close calls via a flag stashed on the dialog
// element, and always clears `document.body.style.overflow` so the
// scroll lock that accompanied showModal is released.
//
// Usage:
//   hxCloseDialogAnimated(dialog, closeAnimationMs, () => {
//     // modal-specific cleanup (focus restore, content reset, etc.)
//   });

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
