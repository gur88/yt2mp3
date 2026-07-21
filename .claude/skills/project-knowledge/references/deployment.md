# Deployment

## Platform

Custom VPS. Local dev run:

```bash
pip install yt-dlp flask
python app.py
```

Serves on `http://localhost:5000`. Requires `ffmpeg` installed and on PATH (both locally and on the server).

## Environments

- **Production**: VPS `213.139.208.8`, app at `/var/www/yt2mp3`, run as a `systemd` service named `yt2mp3` via `gunicorn --workers 1 --bind 127.0.0.1:5000 app:app` (single worker is load-bearing: all in-memory state — `jobs`, `info_cache`, `request_times` — lives in one process with no cross-process sharing; adding workers would silently break rate limiting, the info cache, and job status polling)
- **Local**: developer machine, no staging environment

## CI/CD

GitHub Actions (`.github/workflows/deploy.yml`) deploys on every push to `master`: SSHes into the server, runs `git pull` in `/var/www/yt2mp3`, then `systemctl restart yt2mp3`. No test/build step — the app has no automated tests yet.

**Required GitHub Actions secrets** (repo Settings → Secrets and variables → Actions):

| Secret | Value | Used by |
|--------|-------|---------|
| `SSH_HOST` | `213.139.208.8` | deploy.yml |
| `SSH_USER` | `root` | deploy.yml |
| `SSH_PRIVATE_KEY` | private key matching a public key in the server's `~/.ssh/authorized_keys` for `root` | deploy.yml |

**Manual deploy** (if CI is down or for emergency fixes):

```bash
ssh root@213.139.208.8
cd /var/www/yt2mp3 && git pull && systemctl restart yt2mp3
```

**Rollback**: on the server, `git log --oneline` to find the last good commit, then `git reset --hard <commit>` and `systemctl restart yt2mp3`.

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

Verified-stable reference:

- `https://www.youtube.com/watch?v=9bZkp7q19f0` (PSY — Gangnam Style; long-running, extremely popular upload, very unlikely to be taken down) — confirmed working 2026-07-21 and 2026-07-22.

Keep at least this one link here and re-verify it occasionally; add a second (e.g. a short video) once you've actually confirmed it's stable — don't add an unverified one just to have two, that defeats the point.

## Analytics (Umami)

Self-hosted, privacy-focused analytics — an independent Docker stack on the **same VPS** (`213.139.208.8`), but **not part of this git repo and not deployed via CI/CD**. Provisioned once via direct SSH (the `CLAUDE.md` infra-provisioning exception), not something `git pull` or `deploy.yml` touches.

- `/opt/umami/docker-compose.yml`: `umami` (`ghcr.io/umami-software/umami:postgresql-latest`) + `postgres:15-alpine`, Umami bound to `127.0.0.1:3001` only (not exposed directly)
- Both containers run with `restart: always` — confirmed to survive a full VPS reboot untouched (2026-07-22) — so this isn't something that needs manual intervention after routine maintenance
- `DISABLE_TELEMETRY=1` set — no data leaves the VPS to Umami's own telemetry servers, consistent with `privacy.html`'s "self-hosted, nothing shared with third parties" claim
- Separate nginx server block (`analytics.audiograb.ru` → `127.0.0.1:3001`), independent from the yt2mp3 app's own nginx block — SSL via certbot
- Tracking script embedded on the four tool pages only (`index.html`, `tiktok.html`, `soundcloud.html`, `vk.html`, not `privacy.html`/`terms.html`/`404.html`): `<script defer src="https://analytics.audiograb.ru/script.js" data-website-id="...">` plus a `preconnect` hint
- To change anything about this stack (upgrade the image, adjust `docker-compose.yml`), it's a manual SSH operation on the VPS, not a code change in this repo
