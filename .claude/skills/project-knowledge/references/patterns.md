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

## Background Thread Error Handling

Every `except` block in a background thread (currently just `run_download`, spawned per job from `/api/download`) must call `logger.exception(e)` before recording the failure anywhere else. A background thread has no HTTP response to fail loudly with — an unlogged exception there is invisible outside the process, since `job_error` in Umami is client-side telemetry (fires only if the browser is still polling and JS runs), not a substitute for a server-side traceback. This was a real gap: a yt-dlp extractor bug crashed downloads for two real users and left zero trace in `journalctl` until this was fixed (see `architecture.md` → Data Flow).

**Distinguishing an expected rejection from an unexplained crash:** don't do it by exception type (`ValueError`/`RuntimeError` are also plausible types for a genuine bug to raise) or by matching message text (breaks the moment the message wording changes). Use a dedicated marker exception (`UserFacingError` in `app.py`) raised only from a plain condition check — never as a wrapper around a caught exception — so there's no way for a real failure to get relabeled as "expected" and lose its traceback. Catch it in its own `except` clause, ahead of the general `except Exception`, with no logging (it's routine, not a bug); let everything else fall through to the logged, generically-worded branch. If the same rejection condition is user-visible from more than one endpoint (e.g. a playlist link, rejected by both `/api/info` and `/api/download`), keep the message text identical between them — verified by grep, not just review, before shipping.
