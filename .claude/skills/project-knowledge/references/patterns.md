# Patterns

## Git Workflow

- Remote: `https://github.com/gur88/yt2mp3.git`
- Default branch: `master`
- No branch protection — commit directly to `master`
- Every push to `master` triggers `.github/workflows/deploy.yml`, which deploys straight to production (SSH + `git pull` + `systemctl restart`) — there is no staging/review step, so verify changes locally before pushing to `master`

## Testing

No automated test suite. The working manual-verification pattern for ffmpeg-related changes (trim, normalize, tags, cover art): import `app.py` directly in a throwaway script and call its internal functions (`run_ffmpeg_with_progress`, `ffmpeg_codec_args`, etc.) against a synthetic source generated with `ffmpeg -f lavfi -i "sine=..."` — this exercises the real ffmpeg subprocess plumbing without touching yt-dlp or the network at all, which matters because real YouTube/TikTok/etc. requests from a datacenter IP are unreliable in a sandboxed dev environment (anti-bot blocks, or simply a dead test video ID — see `deployment.md` → Smoke-Test Reference Links for that distinction). Verify with `ffprobe`/`ffmpeg -af ebur128` (duration, codec, sample rate, loudness). Follow up with at least one real end-to-end run through the actual HTTP API before calling a feature done — the direct-function test doesn't exercise the Flask route, job-polling, or yt-dlp's own extraction quirks.

## Business Rules

None beyond what's in `architecture.md` (format/codec selection table).

`static/privacy.html` states the exact IP retention period (rate-limit window) in prose. If `RATE_LIMIT_WINDOW` in `app.py` ever changes, update that wording too — it's a duplicated fact, not derived at build time.

`static/app.css`/`static/app.js` are shared by the four tool pages (`index.html`, `tiktok.html`, `soundcloud.html`, `vk.html`) via a manual cache-busting version query (`?v=1`). There's no build step to auto-bust the cache — whenever either file's content changes, bump the version number by hand in every page that references it, or returning visitors' browsers may keep serving the stale cached copy.
