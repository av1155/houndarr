/* Houndarr password-form client behaviors, shared by the auth pages
 * (login / setup) and the Admin > Security form on Settings:
 *   - Password show/hide toggle          (data-pw-toggle)
 *   - Caps-lock badge on password inputs (data-pw-input + .caps-badge)
 *   - Password-strength meter            (data-strength-source + data-strength)
 *   - Confirm-password match indicator   (data-pw-confirm + .pw-match)
 *   - Submit-button loading state        (form[data-auth-form])
 * Everything is data-attribute driven so the same module initialises on
 * any page that includes the required markup, without requiring the
 * .is-auth body class.
 */

(function () {
  'use strict';

  function initPasswordToggle(btn) {
    var wrap = btn.closest('.input-wrap');
    if (!wrap) return;
    var input = wrap.querySelector('input[data-pw-input]');
    if (!input) return;
    btn.addEventListener('click', function () {
      var hidden = input.type === 'password';
      input.type = hidden ? 'text' : 'password';
      btn.setAttribute('aria-label', hidden ? 'Hide password' : 'Show password');
      btn.setAttribute('aria-pressed', hidden ? 'true' : 'false');
    });
  }

  function initCapsBadge(input) {
    // The caps-badge lives in one of two places depending on the form:
    //   1. Inside the .input-wrap container (legacy auth layout).
    //   2. Inside the .field container alongside the label (setup.html
    //      and Admin > Security both follow this pattern; placing the
    //      badge in the label keeps the input's trailing-icon area
    //      uncluttered on narrow viewports).
    // Walk outward from the input, scoping by .field so a badge in a
    // neighbouring field does not flicker when this input is focused.
    var field = input.closest('.field');
    var badge = null;
    if (field) {
      badge = field.querySelector('.caps-badge');
    }
    if (!badge) {
      var wrap = input.closest('.input-wrap');
      if (wrap) badge = wrap.querySelector('.caps-badge');
    }
    if (!badge) return;
    var update = function (e) {
      if (!e.getModifierState) return;
      badge.classList.toggle('is-on', !!e.getModifierState('CapsLock'));
    };
    input.addEventListener('keydown', update);
    input.addEventListener('keyup', update);
    input.addEventListener('blur', function (e) {
      // Focus moving to a sibling inside the same field (eye toggle,
      // caps badge, etc.) is not a reason to hide the indicator; the
      // caps-lock state still applies and the browser has no API to
      // re-query it without a key event, so blur-hiding would strand
      // the badge off until the user types again.
      var scope = input.closest('.input-wrap') || input.closest('.field');
      if (scope && e.relatedTarget && scope.contains(e.relatedTarget)) {
        return;
      }
      badge.classList.remove('is-on');
    });
  }

  var STRENGTH_LABELS = ['—', 'Weak', 'Fair', 'Good', 'Strong'];

  function scorePassword(pw) {
    if (!pw) return 0;
    var score = 0;
    if (pw.length >= 8) score++;
    if (pw.length >= 12) score++;
    if (/[a-z]/.test(pw) && /[A-Z]/.test(pw)) score++;
    if (/\d/.test(pw)) score++;
    if (/[^A-Za-z0-9]/.test(pw)) score++;
    if (score > 4) score = 4;
    return score;
  }

  function initStrengthMeter(input) {
    var form = input.closest('form');
    if (!form) return;
    var meter = form.querySelector('[data-strength]');
    if (!meter) return;
    var label = meter.querySelector('.strength__label');
    var update = function () {
      var level = scorePassword(input.value);
      meter.setAttribute('data-level', String(level));
      meter.setAttribute('aria-valuenow', String(level));
      meter.setAttribute('aria-valuetext', STRENGTH_LABELS[level]);
      if (label) label.textContent = STRENGTH_LABELS[level];
    };
    input.addEventListener('input', update);
    update();
  }

  function initConfirmPassword(input) {
    // input has data-pw-confirm="<id-of-target-input>". On every input
    // event we compare the two values and update the sibling .pw-match
    // element so the Admin > Security form can preview whether the two
    // password fields agree without a round trip.
    var targetId = input.getAttribute('data-pw-confirm');
    if (!targetId) return;
    var target = document.getElementById(targetId);
    if (!target) return;
    var wrap = input.closest('.field') || input.parentElement;
    var match = wrap ? wrap.querySelector('.pw-match') : null;
    var update = function () {
      if (!match) return;
      match.classList.remove('is-match', 'is-mismatch');
      if (!input.value) {
        match.textContent = '\u00A0';
        return;
      }
      if (input.value === target.value) {
        match.textContent = '\u2713 Passwords match';
        match.classList.add('is-match');
      } else {
        match.textContent = '\u2717 Passwords don\u2019t match';
        match.classList.add('is-mismatch');
      }
    };
    input.addEventListener('input', update);
    target.addEventListener('input', update);
    update();
  }

  function initSubmitLoading(form) {
    form.addEventListener('submit', function () {
      var btn = form.querySelector('.station-button');
      if (!btn) return;
      btn.classList.add('is-loading');
      btn.disabled = true;
    });
  }

  function initErrorDismiss(form) {
    form.querySelectorAll('input[aria-invalid="true"]').forEach(function (input) {
      input.addEventListener('input', function () {
        input.removeAttribute('aria-invalid');
      }, { once: true });
    });

    var card = form.closest('.auth-card');
    var alert = card ? card.querySelector('.auth-alert') : null;
    if (!alert) return;
    // Focus + scroll the alert on render so screen readers announce it
    // and keyboard users land on the error instead of the first input.
    // tabindex=-1 keeps the alert out of tab order but focusable
    // programmatically; the attribute is idempotent so re-running this
    // initializer (HTMX swap) doesn't compound it.
    if (!alert.hasAttribute('tabindex')) {
      alert.setAttribute('tabindex', '-1');
    }
    try {
      alert.focus({ preventScroll: true });
    } catch (err) { /* old Safari: focus() without options */ alert.focus(); }
    if (typeof alert.scrollIntoView === 'function') {
      alert.scrollIntoView({ block: 'center', behavior: 'auto' });
    }
    var dismissAlert = function () {
      alert.remove();
      form.removeEventListener('input', dismissAlert);
    };
    form.addEventListener('input', dismissAlert);
  }

  function initAll(root) {
    root.querySelectorAll('[data-pw-toggle]').forEach(initPasswordToggle);
    root.querySelectorAll('input[data-pw-input]').forEach(initCapsBadge);
    root.querySelectorAll('input[data-pw-input][data-strength-source]').forEach(initStrengthMeter);
    root.querySelectorAll('input[data-pw-confirm]').forEach(initConfirmPassword);
    root.querySelectorAll('form[data-auth-form]').forEach(initSubmitLoading);
    root.querySelectorAll('form[data-auth-form]').forEach(initErrorDismiss);
  }

  document.addEventListener('DOMContentLoaded', function () {
    initAll(document);
  });
  // HTMX swaps partials into the Settings page without a full reload, so
  // re-run the initializers when new password-form markup lands.
  document.body.addEventListener('htmx:afterSwap', function (evt) {
    if (evt.detail && evt.detail.target) {
      initAll(evt.detail.target);
    }
  });
})();
