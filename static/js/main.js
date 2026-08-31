/* ============================================================
   SkillSwap — main.js
   Handles: send-request modal population, rating modal
   population, show/hide password, and small UI niceties
   (auto-dismiss alerts, scroll-reveal animation, toasts,
   animated counters, button micro-interactions).

   NOTE: every function/behavior from the original file is kept
   exactly as-is (same names, same signatures) so existing inline
   onclick="" handlers and event listeners in the templates keep
   working unmodified. Everything below the original code is new
   and purely additive.
============================================================= */

/**
 * Toggles a password input between visible and hidden,
 * and swaps the eye icon accordingly.
 */
function togglePassword(inputId, btn) {
  const input = document.getElementById(inputId);
  if (!input) return;
  const isHidden = input.type === 'password';
  input.type = isHidden ? 'text' : 'password';
  btn.innerHTML = isHidden ? '<i class="bi bi-eye-slash"></i>' : '<i class="bi bi-eye"></i>';
  btn.setAttribute('aria-label', isHidden ? 'Hide password' : 'Show password');
}

/**
 * Opens the "Send Swap Request" modal and fills it in for a
 * specific student.
 * @param {number} receiverId - id of the student being contacted
 * @param {string} receiverName - display name of that student
 * @param {string[]} theirOfferedSkills - skills that student offers
 *        (used to populate the "what you want to learn" dropdown)
 */
function openRequestModal(receiverId, receiverName, theirOfferedSkills) {
  const modalEl = document.getElementById('requestModal');
  if (!modalEl) return;

  document.getElementById('reqModalReceiverId').value = receiverId;
  document.getElementById('reqModalName').textContent = receiverName;

  const wantedSelect = document.getElementById('reqModalSkillWanted');
  wantedSelect.innerHTML = '<option value="">Choose a skill they offer</option>';
  (theirOfferedSkills || []).forEach(function (skill) {
    const opt = document.createElement('option');
    opt.value = skill;
    opt.textContent = skill;
    wantedSelect.appendChild(opt);
  });

  const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
  modal.show();
}

/**
 * Opens the rating modal for a completed swap request.
 * @param {number} requestId - id of the completed request being rated
 * @param {number} receiverId - id of the student being rated
 * @param {string} receiverName - display name of that student
 */
function openRatingModal(requestId, receiverId, receiverName) {
  const modalEl = document.getElementById('ratingModal');
  if (!modalEl) return;

  document.getElementById('ratingRequestId').value = requestId;
  document.getElementById('ratingReceiverId').value = receiverId;
  document.getElementById('ratingModalName').textContent = receiverName;

  const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
  modal.show();
}

/* ============================================================
   NEW (additive) — toast notifications
   Usage: showToast('Skill added!', 'success')
   Safe no-op if the #swToastStack container isn't present.
============================================================= */
function showToast(message, type) {
  type = type || 'info';
  const stack = document.getElementById('swToastStack');
  if (!stack) return;

  const icons = {
    success: 'bi-check-circle',
    danger: 'bi-exclamation-triangle',
    warning: 'bi-exclamation-circle',
    info: 'bi-info-circle'
  };

  const toast = document.createElement('div');
  toast.className = 'sw-toast';
  toast.innerHTML = '<i class="bi ' + (icons[type] || icons.info) + '"></i><span>' +
    (message || '').replace(/</g, '&lt;') + '</span>';
  stack.appendChild(toast);

  setTimeout(function () {
    toast.classList.add('sw-toast-out');
    setTimeout(function () { toast.remove(); }, 220);
  }, 3800);
}

/* ============================================================
   NEW (additive) — animated number counters
   Any element with [data-counter="123"] will count up from 0
   to 123 once it scrolls into view. Non-numeric / missing
   values are ignored safely.
============================================================= */
function initCounters() {
  const counters = document.querySelectorAll('[data-counter]');
  if (!counters.length || !('IntersectionObserver' in window)) return;

  const animate = function (el) {
    const target = parseFloat(el.getAttribute('data-counter'));
    if (isNaN(target)) return;
    const isDecimal = String(target).indexOf('.') !== -1 || (el.getAttribute('data-counter') || '').indexOf('.') !== -1;
    const duration = 900;
    const start = performance.now();

    function tick(now) {
      const progress = Math.min((now - start) / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3); // ease-out-cubic
      const value = target * eased;
      el.textContent = isDecimal ? value.toFixed(1) : Math.round(value).toString();
      if (progress < 1) {
        requestAnimationFrame(tick);
      } else {
        el.textContent = isDecimal ? target.toFixed(1) : target.toString();
      }
    }
    requestAnimationFrame(tick);
  };

  const observer = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (entry.isIntersecting) {
        animate(entry.target);
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.4 });

  counters.forEach(function (el) { observer.observe(el); });
}

/* ============================================================
   NEW (additive) — subtle button "glow follows cursor" effect
   Purely cosmetic; sets --x/--y custom properties consumed by
   the .btn::after radial-gradient in style.css.
============================================================= */
function initButtonGlow() {
  document.addEventListener('pointermove', function (e) {
    const btn = e.target.closest && e.target.closest('.btn');
    if (!btn) return;
    const rect = btn.getBoundingClientRect();
    btn.style.setProperty('--x', ((e.clientX - rect.left) / rect.width * 100) + '%');
    btn.style.setProperty('--y', ((e.clientY - rect.top) / rect.height * 100) + '%');
  }, { passive: true });
}

document.addEventListener('DOMContentLoaded', function () {
  // Auto-dismiss flash messages after 5 seconds
  document.querySelectorAll('.sw-alert').forEach(function (alertEl) {
    setTimeout(function () {
      const alert = bootstrap.Alert.getOrCreateInstance(alertEl);
      if (alert) alert.close();
    }, 5000);
  });

  // Gentle scroll-reveal for landing-page + dashboard sections
  const revealTargets = document.querySelectorAll(
    '.sw-step-card, .sw-feature-card, .sw-match-card, .sw-stat-card, .sw-team-card, .sw-result-card, .sw-req-card'
  );
  if ('IntersectionObserver' in window && revealTargets.length) {
    const observer = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            entry.target.classList.add('sw-revealed');
            observer.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.1 }
    );
    revealTargets.forEach(function (el, i) {
      el.classList.add('sw-reveal-init');
      el.style.transitionDelay = Math.min(i * 40, 320) + 'ms';
      observer.observe(el);
    });
  }

  initCounters();
  initButtonGlow();
});
