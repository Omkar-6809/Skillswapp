# SkillSwap performance optimizations

Implemented in this build:

- Product demo MP4s are lazy-loaded only when close to the viewport, then paused while off-screen.
- Landing-page demo videos use `preload="none"` and retain poster images for fast visual placeholders.
- Removed the Google Fonts network dependency and switched the app to system font stacks.
- Added cache headers for static CSS/JS (1 day) and media/images (7 days).
- Added PostgreSQL indexes for common user, skill, rating, request, message, and team-membership queries.
- Batched profile/match queries to remove common N+1 query patterns in those code paths.
- Reduced chat polling from every 2.5 seconds to every 8 seconds; polling still pauses while the tab is hidden.
- Added cache-busting query versions to the main CSS/JS links so future UI deployments can update cleanly.

## Further production steps

For the biggest additional gains, serve static files through a CDN/object store and consider WebSockets/SSE for real-time chat instead of polling.
