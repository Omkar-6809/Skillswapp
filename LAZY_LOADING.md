# SkillSwap site-wide lazy loading

This build adds a shared lazy-loading strategy across the Flask templates:

- Profile/list avatars use native `loading="lazy"` and `decoding="async"`.
- Avatar dimensions are declared to reduce cumulative layout shift.
- Homepage demo MP4s keep `preload="none"` and are loaded only when near the viewport via `IntersectionObserver`; they pause when off-screen.
- A reusable `[data-lazy-src]` / `[data-lazy-bg]` helper is available in `static/js/main.js` for future images/backgrounds that should not receive a URL until needed.
- Below-the-fold landing sections use `content-visibility: auto` with an intrinsic size so the browser can skip unnecessary layout/paint work before they are visible.
- Bootstrap and the app JavaScript are both deferred so they do not block initial HTML parsing.
- Reduced-motion users get rendering without the deferred-section optimization and video autoplay is suppressed.

## Usage for future assets

For an image that should be loaded only near the viewport:

```html
<img data-lazy-src="/static/images/example.webp" alt="Example" width="800" height="450">
```

For a decorative background:

```html
<div data-lazy-bg="/static/images/example.webp"></div>
```

Prefer native `loading="lazy"` for ordinary images; use the data attributes when the URL itself must stay out of the initial request.
