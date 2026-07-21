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
│   ├── index.html      # main tool page (YouTube-focused)
│   ├── tiktok.html      # /tiktok — same tool, TikTok-focused SEO content
│   ├── soundcloud.html  # /soundcloud — same tool, SoundCloud-focused SEO content
│   ├── vk.html           # /vk — same tool, VK Video-focused SEO content
│   ├── app.css          # shared styling for the four tool pages above
│   ├── app.js            # shared tool logic for the four tool pages above
│   ├── privacy.html     # /privacy — legal, noindex
│   ├── terms.html       # /terms — legal, noindex
│   └── 404.html         # error page, noindex
├── downloads/          # scratch dir for in-flight jobs; files deleted after serving
└── README.md
```

Two distinct page patterns, deliberately different:

- **Tool pages** (`index.html`, `tiktok.html`, `soundcloud.html`, `vk.html`) share `app.css`/`app.js` via `<link>`/`<script src>` — the tool card markup (ids, structure) is duplicated per page since there's no templating, but all styling and behavior lives in the two shared files. No build step, so a manual cache-busting version query (`app.css?v=1`, `app.js?v=1`) must be bumped by hand in every page that references them whenever either file changes — see `patterns.md`.
- **Standalone pages** (`privacy.html`, `terms.html`, `404.html`) stay fully self-contained with their own inline `:root` CSS copy, no shared stylesheet — they're simple enough that sharing isn't worth the coupling, and unlike the tool pages they don't need the interactive JS at all.

Flask routes to all of these via `send_static_file` (same pattern for `/`, `/tiktok`, `/soundcloud`, `/vk`, `/privacy`, `/terms`), so URLs stay extension-less even though Flask's static folder is served at the root.

## Landing Pages (`/tiktok`, `/soundcloud`, `/vk`)

Same fully-functional tool card as `/`, wrapped in source-specific SEO content: unique `<title>`/description/canonical/OG/Twitter tags, unique H1 + intro copy, and a unique `FAQPage` JSON-LD matching the visible FAQ 1-for-1 (verified by comparing `<summary>` count to JSON entity count — must stay equal whenever a page's FAQ changes). `SoftwareApplication` JSON-LD stays on the main page only — one canonical entity per site, not per landing page.

Each page's input placeholder is source-specific (e.g. `https://vkvideo.ru/video-...` on `/vk`); format defaults are unchanged (AAC preselected) since `app.js` doesn't vary behavior per page.

Cross-linking: a `.source-links` nav block on every tool page links to the other three, always omitting a link to itself (main page links to the 3 landing pages; each landing page links to the main page and its two siblings).

SEO copy makes only claims verified against actual `yt-dlp`/`ffmpeg` behavior — e.g. the SoundCloud page doesn't claim Opus ever avoids re-encoding (tested: SoundCloud never serves a native webm/opus stream to yt-dlp, so `opus` requests always re-encode) and only says AAC *may* copy without re-encoding, since that depends on whether the specific track happens to have an HLS-AAC source (confirmed both ways: one test track only had MP3, another had `hls_aac_160k` which the AAC format request does stream-copy — verified via `ffprobe` bitrate matching the source's 160k exactly, since a re-encode would show the code's fixed 192k target instead).

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

The stream-copy paths above are disabled whenever a trim is active — see Trim below.

## Trim (Audio Fragment)

`POST /api/download` accepts optional `trim_start`/`trim_end` (float seconds). Server validates both are non-negative and `start < end` when both are given — checked right after `validate_url`, before `check_rate_limit` (same ordering rationale: an invalid request shouldn't burn quota). Duration-based validation (`end` ≤ track length) is client-side only — the server doesn't know the track's duration until yt-dlp extracts it inside `run_download`, well after the request already returned a job id.

**Forced re-encode.** Whenever a trim is requested, stream-copy is disabled for aac/opus (`ffmpeg_codec_args(..., trimming=True)`) — `-ss`/`-to` with `-c copy` cuts on packet boundaries and can produce leading garbage/silence; an accurate cut needs decoding.

**ffmpeg invocation.** `-ss <start>`/`-to <end>` are added as INPUT options, placed before the audio `-i` in `_build_ffmpeg_cmd`. The cover-art thumbnail is a *second*, later `-i` with no trim flags of its own, so the trim only ever applies to the audio input.

**Progress mapping.** `run_ffmpeg_with_progress`'s 90→100% mapping is driven entirely by the `duration` argument it's given; `run_download` computes that as `trim_end - trim_start` (falling back to the source's full duration on either open end) before calling it, so the bar doesn't stall on a fraction when trimming is active. The download phase (0–90%) still fetches the whole source — yt-dlp can't partial-download — that's expected, not a bug.

**Corrupt-output guard.** After a successful ffmpeg run, `run_download` checks `output_path` exists and is at least `MIN_OUTPUT_BYTES` (2048 bytes) — guards against e.g. a `trim_start` past the track's real end, which ffmpeg can "succeed" on (return code 0) while writing a near-empty file. This bypasses client-side duration validation via a direct API call, since the server has no duration to check against at request time. Fails the job with a Russian error (`jobs[job_id]["status"] = "error"`) instead of serving a broken file.

**Cover art** embedding is unaffected by trimming (still mp3/aac only, per Format Handling above) — the thumbnail's own `-i` never gets a trim flag.

**Client-side (`static/app.js`):** the trim fields are cleared and the section collapses whenever the previewed track's URL changes (`currentTrackUrl`), so a trim range left over from a previously downloaded track can't silently carry over onto the next one. "By"-field prefill with the track duration only happens when the field is currently empty.

## Track Preview (`/api/info`)

`POST /api/info {url}` runs `yt_dlp.extract_info(url, download=False)` — metadata only, no file ever touches disk — and returns `{title, artist, thumbnail, duration}` (`artist` falls back through `artist → uploader → channel`, since not every video sets an explicit artist tag). The frontend calls this on a 600ms debounce after the URL input changes (`static/index.html`), guarding against out-of-order responses with a `previewRequestId` counter so a stale response for an old URL can't overwrite the preview for a newer one.

Results are cached in-memory for 10 minutes (`info_cache`, keyed by the raw URL string — no normalization, so `youtu.be/X` and `youtube.com/watch?v=X` cache separately). Only successful lookups are cached; errors are never cached, so a transient extraction failure doesn't stick for the full TTL.

## Editable Tags (Title/Artist)

`/api/info`'s `title`/`artist` populate two plain `<input>` fields in the preview box (`static/app.js`), styled to look like static text until focused (`.preview-title-input`/`.preview-artist-input` in `app.css`). Editing them is optional — `POST /api/download` accepts optional `title`/`artist` strings, sanitized server-side (`sanitize_tag`: strip control characters, trim whitespace, cap at 200 chars, empty-after-trim treated as absent).

When provided, `_build_ffmpeg_cmd` adds `-metadata title=...`/`-metadata artist=...` — works for all three formats (opus lands these in Vorbis comments). When *not* provided, no `-metadata` flags are added at all, so a plain conversion with no tag edits produces the same output as before this feature existed (aac/opus stream-copy paths untouched, no extra mux pass added).

The download filename still goes through the existing `sanitize_filename`, now applied to the custom title when given, falling back to the extracted title otherwise — dangerous filesystem characters get stripped the same way regardless of source.

If the user never triggered a preview (pasted a URL and hit download immediately), the tag inputs are empty placeholders — nothing is sent, so behavior is identical to before: extracted metadata only.

## Playlist URLs Are Rejected

Both `/api/info` and `run_download` reject playlist URLs instead of processing them. A bare `yt_dlp.extract_info` call on a playlist link (no `extract_flat`) resolves every video's full metadata synchronously — confirmed to hang past 60s on a 96-video playlist — and in `run_download` would silently download every video while only converting/serving one arbitrary file, leaking the rest to disk.

The fix is two-layered: `noplaylist: True` in the yt-dlp options handles the mixed case (`watch?v=X&list=Y` — takes the single video), and a cheap upfront `extract_flat: True` probe checks `info.get("_type") == "playlist"` to reject bare playlist links (`playlist?list=Y`) in ~1-2s before the expensive full extraction ever runs. `noplaylist` alone does *not* cover the bare-playlist case — there's no video to fall back to, so yt-dlp has no choice but to resolve the whole thing.

Same detection works for non-YouTube playlist URLs (verified against a VK Video playlist link) since the `_type` check is generic to whatever extractor yt-dlp picks.

## Cover Art Embedding

During conversion, `run_download` fetches `info["thumbnail"]` (already available from the same `extract_info` call used for the download) via `urllib.request` into a temp file, then `run_ffmpeg_with_progress` muxes it in as attached-picture cover art: second `-i` input, `-map 0:a -map 1:v`, `-c:v mjpeg -disposition:v:0 attached_pic`.

- Works for **mp3** and **aac/m4a** — verified with `ffprobe` that `attached_pic=1` shows up in players like Яндекс Музыка
- **Does not work for opus** — the `opus` muxer itself rejects a video stream (`Unsupported codec id in stream 1`), a hard ffmpeg limitation, not a bug to fix
- `run_ffmpeg_with_progress` always tries with the cover first, and on ffmpeg failure retries once without it (`_build_ffmpeg_cmd`), logging a warning — this is what makes the opus case degrade to a plain audio file instead of breaking the conversion
- **Gotcha already hit once:** the opus copy-path codec args used to hardcode `-vn` (added originally to strip the source webm's own video/thumbnail track when not embedding a cover). That flag silently discarded the mapped cover stream too, so the "failure" path never triggered and covers just silently vanished with no error or log. Fixed by only adding `-vn` when there's no `thumbnail_path` to map — when a cover *is* being embedded, the explicit `-map` calls already say exactly which streams go in, so `-vn` isn't needed and must not be added.

## SEO Content & FAQ Accordion

Below the tool card, `static/index.html` has a static SEO content section (intro copy + 7-question FAQ) with matching `FAQPage` JSON-LD for search rich snippets. Targets audiograb.ru's core search terms (download audio from YouTube, YouTube to MP3/AAC, extract sound from video).

FAQ items are native `<details>`/`<summary>`, but with custom JS instead of default browser behavior, for two reasons: only one item open at a time, and an animated open/close instead of the instant native toggle.

- Summary clicks are intercepted (`preventDefault`) and `item.open` is set manually by `openFaqItem`/`closeFaqItem`, so the visual state and the semantic `open` attribute can be sequenced deliberately (e.g. the details element isn't marked closed until the collapse animation actually finishes)
- The browser's default UA rule hides a closed `<details>`'s content via `display:none`, which can't be animated — `.faq-content` overrides this to `display:block` unconditionally and uses `height` + `overflow:hidden` instead, so it's always in the layout and animatable
- Height animation is triggered by setting the start height, forcing a synchronous reflow (reading `.offsetHeight`), then setting the end height — not `requestAnimationFrame`. rAF depends on the tab actually compositing frames, which doesn't happen in some automated/headless/backgrounded contexts (hit this while testing in the browser-preview tool here); the forced-reflow technique doesn't have that dependency and works the same in every real browser tab

## Cookie Consent Banner

`static/index.html` shows a fixed bottom banner (non-blocking, just a notice — no accept/reject choice since the only cookie is the technical session cookie for rate limiting, not tracking) on first visit, styled with the same `:root` CSS variables as the rest of the page. Dismissal is recorded in `localStorage` (`cookie_consent` key) so it doesn't reappear.

## Data Model

None — no database. State is an in-memory `jobs: dict[str, dict]` in `app.py`, lost on process restart.

## Rate Limiting

Per-IP, in-memory, no external dependency (mirrors the `jobs` dict pattern — lost on restart, doesn't scale across multiple processes/workers):

- Max **3 concurrent** downloads per IP (`concurrent_counts`)
- Max **10 downloads per rolling hour** per IP (`request_times`, timestamps pruned on each check)
- Client IP resolution reads `X-Real-IP` (set by nginx via `proxy_set_header X-Real-IP $remote_addr;` in production), falls back to `request.remote_addr`. Deliberately **not** `X-Forwarded-For`: nginx's default config appends to it rather than replacing it, so the first element is client-controlled — sending an arbitrary `X-Forwarded-For` used to bypass both rate limits entirely
- Both checks + the reservation happen atomically under one `threading.Lock` (`check_rate_limit`) to avoid a race between checking and incrementing
- Concurrent slot is released in `run_download`'s `finally` block (`release_concurrent_slot`), so it's freed on both success and failure
- Exceeding either limit returns HTTP 429 with a Russian-language error message, surfaced as-is by the frontend's existing error handling
- The hourly limit's 429 response also includes `retry_after_seconds` (computed from the oldest timestamp in the IP's window), which the frontend turns into a live countdown in `#statusSub`. The concurrent-downloads limit never includes it — that slot frees up whenever a running download finishes, not on a fixed schedule, so there's no meaningful countdown to show

## 404 Page

`app.py` registers `@app.errorhandler(404)` returning `static/404.html` — a standalone page following the same self-contained CSS-variable pattern as `privacy.html`/`terms.html` (own `:root` copy, no shared stylesheet, `noindex`).

## Background Janitor

A daemon thread (`_janitor`, started at module import) runs every `JANITOR_INTERVAL` (10 min) and independently sweeps the app's four unbounded-growth points, each wrapped in its own try/except so one failure doesn't stop the others:

- `jobs` entries older than `JOB_MAX_AGE` (1h, tracked via a `created_at` monotonic timestamp added at job creation) — catches jobs that ended in `status == "error"`, or finished jobs whose file the user never fetched (closed the tab)
- Files in `downloads/` with `mtime` older than `FILE_MAX_AGE` (1h) — age-based and independent of the `jobs` dict by design, so it also catches raw/partial files left behind by a mid-conversion crash, not just files tied to a still-known job
- `info_cache` entries past `INFO_CACHE_TTL`
- `request_times` IPs with no timestamp left inside `RATE_LIMIT_WINDOW` after pruning (`concurrent_counts` is untouched — it's already correctly maintained by `release_concurrent_slot`)

Safe as a single in-process thread only because production runs gunicorn with exactly one worker (see `deployment.md`) — with multiple workers each would run its own janitor over its own process-local state, same as the rate-limiting/cache/jobs correctness this whole app depends on.

## SSRF Protection (`validate_url`)

Both `/api/info` and `/api/download` call `validate_url(url)` before any yt-dlp invocation — yt-dlp's generic extractor will happily fetch any URL it's given, including internal targets (e.g. Umami on `127.0.0.1:3001`, cloud metadata IPs, RFC1918 ranges). It parses the URL, requires `http`/`https` with a hostname, resolves via `socket.getaddrinfo` (A and AAAA), and rejects if any resolved address is private/loopback/link-local/multicast/reserved/unspecified. One generic Russian error message regardless of which check failed, so nothing about the internal network is leaked.

In `/api/info` it runs *after* the `info_cache` lookup (cached URLs were already validated when first cached, so this avoids adding resolver latency to every preview keystroke) but before any yt-dlp call. In `/api/download` it runs before `check_rate_limit`, so a rejected URL doesn't burn a rate-limit slot.

**Known accepted limitation:** validation resolves DNS once here; yt-dlp resolves again independently later. A DNS-rebinding window exists between the two lookups (attacker's DNS answers public at validation time, private by the time yt-dlp connects). Out of scope for this threat model — not worth pinning the resolved address through to yt-dlp for it.

## HTTP Security Headers

Browser-facing security headers (`X-Content-Type-Options`, `Referrer-Policy`, `X-Frame-Options`, `Strict-Transport-Security`) are set at the nginx layer, not in `app.py` — see `deployment.md` → Security Headers for the exact directives and why `Content-Security-Policy` isn't there yet.
