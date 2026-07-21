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

## Monitoring

Not configured — errors surface via `jobs[job_id]["error"]` returned from `/api/status/<job_id>`.

## Analytics (Umami)

Self-hosted, privacy-focused analytics — an independent Docker stack on the **same VPS** (`213.139.208.8`), but **not part of this git repo and not deployed via CI/CD**. Provisioned once via direct SSH (the `CLAUDE.md` infra-provisioning exception), not something `git pull` or `deploy.yml` touches.

- `/opt/umami/docker-compose.yml`: `umami` (`ghcr.io/umami-software/umami:postgresql-latest`) + `postgres:15-alpine`, Umami bound to `127.0.0.1:3001` only (not exposed directly)
- `DISABLE_TELEMETRY=1` set — no data leaves the VPS to Umami's own telemetry servers, consistent with `privacy.html`'s "self-hosted, nothing shared with third parties" claim
- Separate nginx server block (`analytics.audiograb.ru` → `127.0.0.1:3001`), independent from the yt2mp3 app's own nginx block — SSL via certbot
- Tracking script embedded on the four tool pages only (`index.html`, `tiktok.html`, `soundcloud.html`, `vk.html`, not `privacy.html`/`terms.html`/`404.html`): `<script defer src="https://analytics.audiograb.ru/script.js" data-website-id="...">` plus a `preconnect` hint
- To change anything about this stack (upgrade the image, adjust `docker-compose.yml`), it's a manual SSH operation on the VPS, not a code change in this repo
