# Deployment

## Platform

Custom VPS. Local dev run:

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt   # Windows: .venv\Scripts\pip install -r requirements.txt
.venv/bin/python app.py                     # Windows: .venv\Scripts\python app.py
```

Serves on `http://localhost:5000`. Requires `ffmpeg` installed and on PATH (both locally and on the server). `.venv/` is gitignored — every clone creates its own, from `requirements.txt`.

## Environments

- **Production**: VPS `213.139.208.8`, app at `/var/www/yt2mp3`, run as a `systemd` service named `yt2mp3` via `gunicorn --workers 1 --bind 127.0.0.1:5000 app:app` (single worker is load-bearing: all in-memory state — `jobs`, `info_cache`, `request_times` — lives in one process with no cross-process sharing; adding workers would silently break rate limiting, the info cache, and job status polling)
- **Local**: developer machine, no staging environment

## CI/CD

GitHub Actions (`.github/workflows/deploy.yml`) deploys on every push to `master`: SSHes into the server, runs `git pull` in `/var/www/yt2mp3`, then `venv/bin/pip install -r requirements.txt --quiet`, then `systemctl restart yt2mp3`. No test/build step — the app has no automated tests yet.

The `pip install` step is what makes `requirements.txt` the actual source of truth for what's running — without it, bumping a pin in git would silently do nothing on the server (see Dependency Versioning below). Since every pin is exact, this is a fast no-op on any deploy that didn't touch dependencies; if it ever fails (network blip reaching PyPI, a bad pin), it fails *before* `systemctl restart`, so the old code and old dependencies keep running rather than landing in a half-updated state.

**Required GitHub Actions secrets** (repo Settings → Secrets and variables → Actions):

| Secret | Value | Used by |
|--------|-------|---------|
| `SSH_HOST` | `213.139.208.8` | deploy.yml |
| `SSH_USER` | `root` | deploy.yml |
| `SSH_PRIVATE_KEY` | private key matching a public key in the server's `~/.ssh/authorized_keys` for `root` | deploy.yml |

**Manual deploy** (if CI is down or for emergency fixes):

```bash
ssh root@213.139.208.8
cd /var/www/yt2mp3 && git pull && venv/bin/pip install -r requirements.txt --quiet && systemctl restart yt2mp3
```

**Rollback**: on the server, `git log --oneline` to find the last good commit, then `git reset --hard <commit>` and `systemctl restart yt2mp3`.

## Dependency Versioning

All Python dependencies are pinned exactly in `requirements.txt` (an exact snapshot of what's actually installed, not a range) — `blinker`, `click`, `Flask`, `gunicorn`, `itsdangerous`, `Jinja2`, `MarkupSafe`, `packaging`, `Werkzeug`, `yt-dlp`. Before this, nothing was pinned anywhere: `deploy.yml` only ran `git pull`, so the venv on the server was whatever had been installed manually at some point in the past — confirmed drifted in practice (dev had `yt-dlp==2026.6.9`, prod had `2026.7.4`, same day, no one had touched either deliberately).

**`yt-dlp` specifically is updated reactively, not on a schedule.** Trigger: the Umami per-source `job_error`/`job_done` dashboard shows degradation on a source, or a fresh extractor traceback shows up in `journalctl` (see `patterns.md` → Background Thread Error Handling) — or occasionally, deliberately, when convenient. Never automatic, never calendar-based: an unreviewed nightly extractor regression should never reach prod on its own.

**The other nine packages need the same discipline for a different reason: security.** Pinning them stops `pip install -r requirements.txt` (now run on every deploy) from ever silently pulling in a newer version — which also means it stops silently pulling in a security fix for `Flask`/`Werkzeug`/`gunicorn`/etc. the way an incidental manual `pip install` sometimes used to. Reproducibility traded away the "someone else's upgrade fixes it for free" safety net. When a CVE lands in any pinned package, bump it deliberately the same way as a yt-dlp update — don't wait for a symptom, since these rarely fail loudly the way a broken extractor does.

**Update procedure** (yt-dlp or any other pin):
1. Locally: `.venv/bin/pip install --upgrade yt-dlp`, note the new version.
2. Update the `yt-dlp==` line in `requirements.txt`.
3. Run the regression checklist below against the *local* venv — this only means something because the local venv is pinned to the same baseline as prod (see Local dev run above; before `requirements.txt` existed, a local check could pass against a version prod didn't even have).
4. Passed → commit (message states old→new version and what triggered it) → push to `master`.
5. CI/CD installs and restarts automatically — no manual server step.
6. One quick spot-check against the live site (the smoke-test links below) — prod's IP/network can behave differently than dev's.

**Regression checklist** (~10 minutes, one real preview + download per source, not just a preview):

| # | Source | Action | Check | Time |
|---|--------|--------|-------|------|
| 1 | YouTube | paste the YouTube reference link below, download AAC | preview shows title/thumbnail/duration; AAC stream-copies (no re-encode note); cover art embedded | ~2 min |
| 2 | TikTok | paste the TikTok reference link, download MP3 | preview ok; download completes and plays | ~2 min |
| 3 | SoundCloud | paste the SoundCloud reference link, download AAC | preview ok; download completes | ~2 min |
| 4 | VK | paste the VK reference link, download Opus | preview ok; download completes — VK is currently the least stable extractor, don't skip this one | ~2 min |
| 5 | any source | enable "Обрезать фрагмент", download a short trim | output duration matches the trimmed range — exercises `-ss`/`-to` + forced re-encode, a separate code path from a plain download | ~1–2 min |

**Rollback**: `git revert <the bump commit>` (or hand-edit the pin back), push. CI/CD reinstalls the exact previous version — `pip install pkg==<old>` downgrades cleanly, no special flags needed. `git log -- requirements.txt` is the version history; nothing extra to maintain.

The one discipline this depends on: actually looking at the Umami dashboard occasionally, and treating a CVE announcement for any pinned package the same way as a broken extractor. Reactive only works if someone reacts.

## Environment Variables

None currently.

## OS Maintenance

Ubuntu 24.04 on the VPS, kernel `6.8.0-136-generic` as of the 2026-07-22 infra session (check `uname -r` for the current value — this drifts as patches land).

- `unattended-upgrades` is active (`/etc/apt/apt.conf.d/20auto-upgrades`: daily package-list refresh + security-pocket upgrade) — security patches install themselves without a manual `apt-get upgrade`.
- `Unattended-Upgrade::Automatic-Reboot` is explicitly set to `"false"` in `/etc/apt/apt.conf.d/50unattended-upgrades` (made explicit 2026-07-22 — previously an implicit default via a commented-out line, same effective behavior). A kernel/library update that needs a reboot to take effect does **not** reboot the box on its own.
- Check `/var/run/reboot-required` periodically (present = a reboot is pending, e.g. a new kernel package is already installed but not yet running — `uname -r` vs `dpkg -l 'linux-image-*'` will disagree). When it appears, reboot manually via direct SSH during a chosen maintenance window — this is the one-off infra-provisioning exception in `CLAUDE.md`, never something CI/CD does.
- Docker containers (Umami + Postgres, see Analytics below) are configured with `restart: always` — they come back on their own after a reboot; still worth a `docker ps` check post-reboot to confirm.
- Before any manual nginx (or other server config) edit, back up the affected file(s) to `/root/backup-nginx-<date>/` on the server first — convention established in the 2026-07-22 session, not automated by anything.

## Security Headers (nginx)

Both server blocks — `/etc/nginx/sites-available/yt2mp3` and `/etc/nginx/sites-available/analytics.audiograb.ru` — send these on their `443 ssl` block (not the plain-HTTP redirect block):

```
add_header X-Content-Type-Options nosniff always;
add_header Referrer-Policy strict-origin-when-cross-origin always;
add_header X-Frame-Options DENY always;
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
```

Added 2026-07-22, verified via securityheaders.com (Grade B at the time). No `Content-Security-Policy` or `Permissions-Policy` yet — deliberately deferred: the app has inline style attributes and inline JSON-LD, so CSP needs to start as `Content-Security-Policy-Report-Only` in its own dedicated session rather than risk silently breaking the site. Neither domain's config had any pre-existing `add_header` before this change, so there was no nested-`location` header-inheritance risk to route around (nginx drops all parent `add_header`s if a nested `location` defines its own — worth rechecking if a `location`-level `add_header` is ever added later).

## Smoke-Test Reference Links

For manual post-deploy/post-infra checks against prod (not automated, not a test suite — just "does a real download still work"), use a known-good, stable video rather than the first ID that comes to mind. An old or removed video returning "Video unavailable" from yt-dlp looks identical to yt-dlp being blocked by YouTube's anti-bot defenses, and the two got conflated once already (July 2026, during the trim-feature review) before being sorted out as "just a dead test video."

Verified-stable references, one per source (used by the Dependency Versioning regression checklist above — pick official/large-account content specifically because it's the least likely to vanish, not because it's more "correct" than any other video):

- **YouTube**: `https://www.youtube.com/watch?v=9bZkp7q19f0` (PSY — Gangnam Style; long-running, extremely popular upload, very unlikely to be taken down) — confirmed working 2026-07-21 and 2026-07-22.
- **TikTok**: `https://www.tiktok.com/@tiktok/video/7666214645006421278` (TikTok's own official account, about the For You Feed — platform's own content, essentially never gets taken down) — confirmed working 2026-07-27.
- **SoundCloud**: `https://soundcloud.com/marshmellomusic/alone` (Marshmello — Alone, official verified artist account, uploaded 2016, 74M+ plays) — confirmed working 2026-07-27.
- **VK**: `https://vkvideo.ru/video-18403220_456239696` (Руслан Усачев — large, long-established Russian creator's official public; content is news-commentary so it'll read as dated, but the channel itself is the stable part, not the topic) — confirmed working end-to-end (real download via the live API) 2026-07-27. VK's extractor is currently the least stable of the four (see the `vk.py` subtitle crash noted in `architecture.md` → Data Flow), so don't skip this one when regression-testing.

Re-verify these occasionally (they're what "10-minute regression checklist" assumes exist) — replace one only after confirming its replacement is actually stable, don't swap in an unverified link just to keep the list fresh.

## Analytics (Umami)

Self-hosted, privacy-focused analytics — an independent Docker stack on the **same VPS** (`213.139.208.8`), but **not part of this git repo and not deployed via CI/CD**. Provisioned once via direct SSH (the `CLAUDE.md` infra-provisioning exception), not something `git pull` or `deploy.yml` touches.

- `/opt/umami/docker-compose.yml`: `umami` (`ghcr.io/umami-software/umami:postgresql-latest`) + `postgres:15-alpine`, Umami bound to `127.0.0.1:3001` only (not exposed directly)
- Both containers run with `restart: always` — confirmed to survive a full VPS reboot untouched (2026-07-22) — so this isn't something that needs manual intervention after routine maintenance
- `DISABLE_TELEMETRY=1` set — no data leaves the VPS to Umami's own telemetry servers, consistent with `privacy.html`'s "self-hosted, nothing shared with third parties" claim
- Separate nginx server block (`analytics.audiograb.ru` → `127.0.0.1:3001`), independent from the yt2mp3 app's own nginx block — SSL via certbot
- Tracking script embedded on the four tool pages only (`index.html`, `tiktok.html`, `soundcloud.html`, `vk.html`, not `privacy.html`/`terms.html`/`404.html`): `<script defer src="https://analytics.audiograb.ru/script.js" data-website-id="...">` plus a `preconnect` hint
- To change anything about this stack (upgrade the image, adjust `docker-compose.yml`), it's a manual SSH operation on the VPS, not a code change in this repo
