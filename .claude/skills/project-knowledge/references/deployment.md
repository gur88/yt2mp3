# Deployment

## Platform

Local only. Run with:

```bash
pip install yt-dlp flask
python app.py
```

Serves on `http://localhost:5000`. Requires `ffmpeg` installed and on PATH.

## Environments

Single environment (developer's local machine). No staging/production split.

## CI/CD

Not configured.

## Environment Variables

None currently.

## Monitoring

Not configured — errors surface via `jobs[job_id]["error"]` returned from `/api/status/<job_id>`.
