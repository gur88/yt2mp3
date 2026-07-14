# Architecture

## Tech Stack

- **Backend**: Python 3.9+, Flask — chosen for a minimal single-file server, no build step needed for a local tool
- **Extraction**: `yt-dlp` — downloads best available audio-only stream, never touches video
- **Conversion**: system `ffmpeg` binary, invoked via `subprocess`, parsed with `-progress pipe:1` for live percentage — chosen over yt-dlp's built-in postprocessors specifically to get granular conversion progress
- **Frontend**: static `index.html` served by Flask (`static/`), no framework

## Project Structure

```
yt2mp3/
├── app.py              # Flask app: routes, download/convert job logic
├── static/
│   └── index.html      # single-page UI
├── downloads/          # scratch dir for in-flight jobs; files deleted after serving
└── README.md
```

## Key Dependencies

- `yt-dlp` — audio extraction
- `flask` — HTTP server
- `ffmpeg` — external system binary, must be on PATH (not a pip dependency)

## Data Flow

1. `POST /api/download` with `{url, format, quality}` → creates a `job_id`, spawns a background thread, returns immediately
2. Background thread: yt-dlp downloads best audio stream (progress hook updates `jobs[job_id]` 0→90%), then `ffmpeg` re-encodes/remuxes to the target format (progress parsed from ffmpeg's own `-progress` output, mapped 90→100%)
3. Client polls `GET /api/status/<job_id>` for `{status, stage, percent, filename, error}`
4. When `status == "done"`, client fetches `GET /api/file/<job_id>` — file is streamed as an attachment; job entry is removed from memory immediately, and file deletion is handed off to a background thread (`_cleanup_file`) that retries with backoff (up to 5 attempts, 0.5s apart) before giving up and logging a warning
5. The frontend stops polling `/api/status` as soon as the job reaches `done`/`error`, or the user clicks the download button — guarded by a `pollStopped` flag checked before acting on any response, so a slow in-flight request can't act on a job that's already gone (this was previously a source of stray 404s)

**Windows note:** `send_file()` can still hold the OS file handle open for a moment after the response is generated, so an immediate `unlink()` right after `send_file` returns a `PermissionError` on Windows. This is why deletion retries in the background rather than deleting inline in `after_this_request`.

## Format Handling

| Format | Container ext | Codec strategy |
|--------|---------------|-----------------|
| mp3    | `.mp3`        | always re-encode with `libmp3lame` at requested bitrate |
| aac    | `.m4a`        | stream-copy if source is already `.m4a`/`.mp4`, else re-encode with `aac` @192k |
| opus   | `.opus`       | stream-copy if source is `.webm`/`.opus`, else re-encode with `libopus` @160k |

## Track Preview (`/api/info`)

`POST /api/info {url}` runs `yt_dlp.extract_info(url, download=False)` — metadata only, no file ever touches disk — and returns `{title, artist, thumbnail, duration}` (`artist` falls back through `artist → uploader → channel`, since not every video sets an explicit artist tag). The frontend calls this on a 600ms debounce after the URL input changes (`static/index.html`), guarding against out-of-order responses with a `previewRequestId` counter so a stale response for an old URL can't overwrite the preview for a newer one.

## Cover Art Embedding

During conversion, `run_download` fetches `info["thumbnail"]` (already available from the same `extract_info` call used for the download) via `urllib.request` into a temp file, then `run_ffmpeg_with_progress` muxes it in as attached-picture cover art: second `-i` input, `-map 0:a -map 1:v`, `-c:v mjpeg -disposition:v:0 attached_pic`.

- Works for **mp3** and **aac/m4a** — verified with `ffprobe` that `attached_pic=1` shows up in players like Яндекс Музыка
- **Does not work for opus** — the `opus` muxer itself rejects a video stream (`Unsupported codec id in stream 1`), a hard ffmpeg limitation, not a bug to fix
- `run_ffmpeg_with_progress` always tries with the cover first, and on ffmpeg failure retries once without it (`_build_ffmpeg_cmd`), logging a warning — this is what makes the opus case degrade to a plain audio file instead of breaking the conversion
- **Gotcha already hit once:** the opus copy-path codec args used to hardcode `-vn` (added originally to strip the source webm's own video/thumbnail track when not embedding a cover). That flag silently discarded the mapped cover stream too, so the "failure" path never triggered and covers just silently vanished with no error or log. Fixed by only adding `-vn` when there's no `thumbnail_path` to map — when a cover *is* being embedded, the explicit `-map` calls already say exactly which streams go in, so `-vn` isn't needed and must not be added.

## Data Model

None — no database. State is an in-memory `jobs: dict[str, dict]` in `app.py`, lost on process restart.

## Rate Limiting

Per-IP, in-memory, no external dependency (mirrors the `jobs` dict pattern — lost on restart, doesn't scale across multiple processes/workers):

- Max **3 concurrent** downloads per IP (`concurrent_counts`)
- Max **10 downloads per rolling hour** per IP (`request_times`, timestamps pruned on each check)
- Client IP resolution checks `X-Forwarded-For` first (for when a reverse proxy sits in front in production), falls back to `request.remote_addr`
- Both checks + the reservation happen atomically under one `threading.Lock` (`check_rate_limit`) to avoid a race between checking and incrementing
- Concurrent slot is released in `run_download`'s `finally` block (`release_concurrent_slot`), so it's freed on both success and failure
- Exceeding either limit returns HTTP 429 with a Russian-language error message, surfaced as-is by the frontend's existing error handling
