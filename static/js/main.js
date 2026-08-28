/* ============================================================
   SkillSwap — main.js
   Handles: send-request modal population, rating modal
   population, show/hide password, and small UI niceties
   (auto-dismiss alerts, scroll-reveal animation).
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

document.addEventListener('DOMContentLoaded', function () {
  // Auto-dismiss flash messages after 5 seconds
  document.querySelectorAll('.sw-alert').forEach(function (alertEl) {
    setTimeout(function () {
      const alert = bootstrap.Alert.getOrCreateInstance(alertEl);
      if (alert) alert.close();
    }, 5000);
  });

  // Gentle scroll-reveal for landing-page sections
  const revealTargets = document.querySelectorAll(
    '.sw-step-card, .sw-feature-card, .sw-match-card, .sw-stat-card'
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
    revealTargets.forEach(function (el) {
      el.classList.add('sw-reveal-init');
      observer.observe(el);
    });
  }
});
