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


  // Product-demo videos are intentionally lazy: the browser does not download
  // the MP4 until the video is near the viewport. This keeps the initial page
  // request light while preserving autoplay/loop behavior for visible videos.
  const demoVideos = document.querySelectorAll('.sw-demo-video[data-video-src]');
  const reducedMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (demoVideos.length) {
    const prepareVideo = function (video) {
      if (!video.dataset.loaded) {
        const src = video.dataset.videoSrc;
        if (src) {
          video.src = src;
          video.dataset.loaded = '1';
          video.load();
        }
      }
      if (!reducedMotion && !document.hidden) {
        video.play().catch(function () {});
      }
    };

    if ('IntersectionObserver' in window) {
      const videoObserver = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          const video = entry.target;
          if (entry.isIntersecting) {
            prepareVideo(video);
          } else if (video.dataset.loaded) {
            video.pause();
          }
        });
      }, { rootMargin: '600px 0px', threshold: 0.01 });
      demoVideos.forEach(function (video) { videoObserver.observe(video); });
    } else {
      // Old browsers: preserve functionality without blocking first paint.
      demoVideos.forEach(prepareVideo);
    }
  }

  // Generic lazy asset support for future images/backgrounds.
  // Native loading=lazy remains the primary mechanism; this helper covers
  // assets intentionally kept out of src until they are near the viewport.
  const lazyAssets = document.querySelectorAll('[data-lazy-src], [data-lazy-bg]');
  if (lazyAssets.length) {
    const loadLazyAsset = function (el) {
      if (el.dataset.lazySrc && !el.dataset.lazyLoaded) {
        el.src = el.dataset.lazySrc;
        el.dataset.lazyLoaded = '1';
      }
      if (el.dataset.lazyBg && !el.dataset.lazyLoaded) {
        el.style.backgroundImage = 'url("' + el.dataset.lazyBg.replace(/"/g, '\\"') + '")';
        el.dataset.lazyLoaded = '1';
      }
    };

    if ('IntersectionObserver' in window) {
      const assetObserver = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            loadLazyAsset(entry.target);
            assetObserver.unobserve(entry.target);
          }
        });
      }, { rootMargin: '300px 0px', threshold: 0.01 });
      lazyAssets.forEach(function (el) { assetObserver.observe(el); });
    } else {
      // Fallback for older browsers.
      lazyAssets.forEach(loadLazyAsset);
    }
  }

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
