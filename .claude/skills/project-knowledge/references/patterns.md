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
