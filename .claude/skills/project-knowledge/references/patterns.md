# Patterns

## Git Workflow

- Remote: `https://github.com/gur88/yt2mp3.git`
- Default branch: `master`
- No branch protection — commit directly to `master`
- Every push to `master` triggers `.github/workflows/deploy.yml`, which deploys straight to production (SSH + `git pull` + `systemctl restart`) — there is no staging/review step, so verify changes locally before pushing to `master`

## Testing

No automated tests yet.

## Business Rules

None beyond what's in `architecture.md` (format/codec selection table).

`static/privacy.html` states the exact IP retention period (rate-limit window) in prose. If `RATE_LIMIT_WINDOW` in `app.py` ever changes, update that wording too — it's a duplicated fact, not derived at build time.

`static/app.css`/`static/app.js` are shared by the four tool pages (`index.html`, `tiktok.html`, `soundcloud.html`, `vk.html`) via a manual cache-busting version query (`?v=1`). There's no build step to auto-bust the cache — whenever either file's content changes, bump the version number by hand in every page that references it, or returning visitors' browsers may keep serving the stale cached copy.
