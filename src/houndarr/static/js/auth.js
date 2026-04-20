/* Houndarr auth pages (login + setup) client behaviors:
 * password show/hide, caps-lock badge, password-strength meter,
 * submit-button loading state. */

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
    var wrap = input.closest('.input-wrap');
    if (!wrap) return;
    var badge = wrap.querySelector('.caps-badge');
    if (!badge) return;
    var update = function (e) {
      if (!e.getModifierState) return;
      badge.classList.toggle('is-on', !!e.getModifierState('CapsLock'));
    };
    input.addEventListener('keydown', update);
    input.addEventListener('keyup', update);
    input.addEventListener('blur', function () {
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

  function initSubmitLoading(form) {
    form.addEventListener('submit', function () {
      var btn = form.querySelector('.station-button');
      if (!btn) return;
      btn.classList.add('is-loading');
      btn.disabled = true;
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('[data-pw-toggle]').forEach(initPasswordToggle);
    document.querySelectorAll('input[data-pw-input]').forEach(initCapsBadge);
    var setupPw = document.querySelector('input[data-pw-input][data-strength-source]');
    if (setupPw) initStrengthMeter(setupPw);
    document.querySelectorAll('form[data-auth-form]').forEach(initSubmitLoading);
  });
})();
