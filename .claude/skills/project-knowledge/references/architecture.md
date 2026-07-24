# Architecture

## Tech Stack

- **Backend**: Python 3.9+, Flask — chosen for a minimal single-file server, no build step needed for a local tool
- **Extraction**: `yt-dlp` — downloads best available audio-only stream, never touches video
- **Conversion**: system `ffmpeg` binary, invoked via `subprocess`, parsed with `-progress pipe:1` for live percentage — chosen over yt-dlp's built-in postprocessors specifically to get granular conversion progress
- **Frontend**: static `index.html` served by Flask (`static/`), no framework

## Project Structure

```
yt2mp3/
├── app.py                    # Flask app: routes, download/convert job logic
├── static/
│   ├── index.html            # main tool page (YouTube-focused)
│   ├── tiktok.html            # /tiktok — same tool, TikTok-focused SEO content
│   ├── soundcloud.html        # /soundcloud — same tool, SoundCloud-focused SEO content
│   ├── vk.html                 # /vk — same tool, VK Video-focused SEO content
│   ├── app.css                # shared styling for the four tool pages above
│   ├── app.js                  # shared tool logic for the four tool pages above
│   ├── privacy.html           # /privacy — legal, noindex
│   ├── terms.html             # /terms — legal, noindex
│   ├── 404.html               # error page, noindex
│   ├── manifest.webmanifest   # PWA manifest — see PWA section below
│   ├── sw.js                  # service worker — see PWA section below
│   ├── offline.html           # SW's offline fallback page
│   └── icon-*.png             # PWA icons (192, 512, 512-maskable)
├── downloads/                # scratch dir for in-flight jobs; files deleted after serving
└── README.md
```

Two distinct page patterns, deliberately different:

- **Tool pages** (`index.html`, `tiktok.html`, `soundcloud.html`, `vk.html`) share `app.css`/`app.js` via `<link>`/`<script src>` — the tool card markup (ids, structure) is duplicated per page since there's no templating, but all styling and behavior lives in the two shared files. No build step, so a manual cache-busting version query (`app.css?v=1`, `app.js?v=1`) must be bumped by hand in every page that references them whenever either file changes — see `patterns.md`.
- **Standalone pages** (`privacy.html`, `terms.html`, `404.html`) stay fully self-contained with their own inline `:root` CSS copy, no shared stylesheet — they're simple enough that sharing isn't worth the coupling, and unlike the tool pages they don't need the interactive JS at all.

Flask routes to all of these via `send_static_file` (same pattern for `/`, `/tiktok`, `/soundcloud`, `/vk`, `/privacy`, `/terms`), so URLs stay extension-less even though Flask's static folder is served at the root.

## Landing Pages (`/tiktok`, `/soundcloud`, `/vk`)

Same fully-functional tool card as `/`, wrapped in source-specific SEO content: unique `<title>`/description/canonical/OG/Twitter tags, unique H1 + intro copy, and a unique `FAQPage` JSON-LD matching the visible FAQ 1-for-1 (verified by comparing `<summary>` count to JSON entity count — must stay equal whenever a page's FAQ changes). `SoftwareApplication` JSON-LD stays on the main page only — one canonical entity per site, not per landing page.

Each page's input placeholder is source-specific (e.g. `https://vkvideo.ru/video-...` on `/vk`); format defaults are unchanged (AAC preselected) since `app.js` doesn't vary behavior per page.

Cross-linking: a `.source-links` nav block on every tool page links to the other three, always omitting a link to itself (main page links to the 3 landing pages; each landing page links to the main page and its two siblings). The under-button hint line (`.hint`, "Работает с YouTube, TikTok, SoundCloud, VK Видео и многими другими сайтами.") follows the same self-omission rule at a second location on the page — three of the four source names are links to their pages, the page's own source stays plain text. Link styling is deliberately subdued (`.hint a`): underlined by default so it still reads as a link against plain muted text, lightening only slightly on hover — no accent red, so it never competes visually with the CTA button just above it.

**Card header.** The card's `.logo-text` is a plain `<div>`, not a heading — the page's actual (and only) `<h1>` lives in the SEO content block below, so changing the card header never touches heading semantics. All four pages show the brand "AudioGrab" plus a page-specific subtitle in `.logo-subtitle` (`аудио из видео` / `звук из TikTok` / `треки с SoundCloud` / `аудио из VK Видео`) — replacing the earlier literal "YT → Audio", which read as a YouTube-only tool even on the TikTok/SoundCloud/VK pages and undercut the actual brand.

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

The stream-copy paths above are disabled whenever a trim or loudness normalization is active — see Trim and Loudness Normalization below.

## Trim (Audio Fragment)

`POST /api/download` accepts optional `trim_start`/`trim_end` (float seconds). Server validates both are non-negative and `start < end` when both are given — checked right after `validate_url`, before `check_rate_limit` (same ordering rationale: an invalid request shouldn't burn quota). Duration-based validation (`end` ≤ track length) is client-side only — the server doesn't know the track's duration until yt-dlp extracts it inside `run_download`, well after the request already returned a job id.

**Forced re-encode.** Whenever a trim is requested, stream-copy is disabled for aac/opus (`ffmpeg_codec_args(..., force_reencode=True)` — see Loudness Normalization below for why this parameter isn't named `trimming`) — `-ss`/`-to` with `-c copy` cuts on packet boundaries and can produce leading garbage/silence; an accurate cut needs decoding.

**ffmpeg invocation.** `-ss <start>`/`-to <end>` are added as INPUT options, placed before the audio `-i` in `_build_ffmpeg_cmd`. The cover-art thumbnail is a *second*, later `-i` with no trim flags of its own, so the trim only ever applies to the audio input.

**Progress mapping.** `run_ffmpeg_with_progress`'s 90→100% mapping is driven entirely by the `duration` argument it's given; `run_download` computes that as `trim_end - trim_start` (falling back to the source's full duration on either open end) before calling it, so the bar doesn't stall on a fraction when trimming is active. The download phase (0–90%) still fetches the whole source — yt-dlp can't partial-download — that's expected, not a bug.

**Corrupt-output guard.** After a successful ffmpeg run, `run_download` checks `output_path` exists and is at least `MIN_OUTPUT_BYTES` (2048 bytes) — guards against e.g. a `trim_start` past the track's real end, which ffmpeg can "succeed" on (return code 0) while writing a near-empty file. This bypasses client-side duration validation via a direct API call, since the server has no duration to check against at request time. Fails the job with a Russian error (`jobs[job_id]["status"] = "error"`) instead of serving a broken file.

**Cover art** embedding is unaffected by trimming (still mp3/aac only, per Format Handling above) — the thumbnail's own `-i` never gets a trim flag.

**Client-side (`static/app.js`):** the trim fields are cleared and the section collapses whenever the previewed track's URL changes (`currentTrackUrl`), so a trim range left over from a previously downloaded track can't silently carry over onto the next one. "By"-field prefill with the track duration only happens when the field is currently empty.

## Loudness Normalization

`POST /api/download` accepts an optional `normalize` boolean (strict type check — anything other than an actual JSON boolean is a 400, same "don't burn quota on garbage" ordering as trim, checked right after it).

**Forced re-encode, shared mechanism with trim.** `ffmpeg_codec_args`'s bool parameter was renamed from `trimming` to `force_reencode` — it was never really about trim specifically, it's "does something need to run on the decoded audio that a stream copy can't do." `run_ffmpeg_with_progress` computes `force_reencode = (trim active) or normalize` and passes that single flag through; there's deliberately no separate normalize-specific re-encode path.

**Filter and its pitfall.** When active, `_build_ffmpeg_cmd` adds `-af loudnorm=I=-16:TP=-1.5:LRA=11 -ar 48000`. Single-pass loudnorm (measure-and-apply in one go) is less accurate than two-pass (measure first, then apply the exact measured gain in a second pass), but two-pass doubles conversion time — not worth it at this scale. The `-ar 48000` is load-bearing, not cosmetic: loudnorm's internal processing upsamples to 192kHz, and without an explicit output rate that 192kHz leaks into the shipped file — needlessly large for no audible benefit. 48kHz was picked as a safe, standard rate compatible with all three target codecs (including Opus, whose native/preferred rate this literally is).

**Trim + normalize together.** Since `-ss`/`-to` are input options (seek/cut before decoding) and `-af` operates on the decoded stream, loudnorm only ever measures and normalizes the already-trimmed audio, not the full source — confirmed by running both together and checking the output duration matches the trim exactly, not a hint of the pre-trim loudness leaking in.

## Track Preview (`/api/info`)

`POST /api/info {url}` runs `yt_dlp.extract_info(url, download=False)` — metadata only, no file ever touches disk — and returns `{title, artist, thumbnail, duration}` (`artist` falls back through `artist → uploader → channel`, since not every video sets an explicit artist tag). The frontend calls this on a 600ms debounce after the URL input changes (`static/index.html`), guarding against out-of-order responses with a `previewRequestId` counter so a stale response for an old URL can't overwrite the preview for a newer one.

Results are cached in-memory for 10 minutes (`info_cache`, keyed by the raw URL string — no normalization, so `youtu.be/X` and `youtube.com/watch?v=X` cache separately). Only successful lookups are cached; errors are never cached, so a transient extraction failure doesn't stick for the full TTL.

## Programmatic URL Input (`setUrlAndPreview`)

Anything that sets the URL input other than the user actually typing — the Web Share Target handoff and the paste button — goes through one shared helper, `setUrlAndPreview(url)` in `static/app.js`: it sets `urlInput.value` and dispatches a synthetic `input` event, which is what the manual-typing debounce → `fetchPreview` listener actually reacts to. Neither caller duplicates that logic; `history.replaceState` (share-target only, to clean the query string) is the one thing that stays outside the shared helper since it doesn't apply to a paste.

**Paste button.** Feature-detected (`navigator.clipboard?.readText`) — not rendered at all when unavailable (Firefox desktop, some WebViews), so there's never a dead control on screen. A denied permission or read failure is a silent no-op, no `alert`, no console noise — matches the existing rejection-handling tone elsewhere in the app (e.g. `fetchPreview`'s catch block).

## Format Memory

The chosen format persists in `localStorage` under `preferredFormat`. `selectFormat(fmt)` in `app.js` is the single function that applies a format choice to the UI (active button, format note, quality-section visibility) and saves it — used by both the click handler and the on-load restore, so the two paths can't drift apart. On load, the stored value is restored *before* any other UI wiring runs, specifically so the file-size estimate (below) computes against the right bitrate from the first render. An unrecognized or corrupted stored value (anything not in `['mp3', 'aac', 'opus']`) is ignored, falling back to the page's hard-coded default (AAC) rather than crashing.

## File Size Estimate

A muted `≈ X,X МБ` line in the preview card, computed client-side as `duration_seconds × bitrate_kbps × 1000 / 8`, converted to megabytes and formatted with a comma decimal (Russian convention). Bitrate is 192 for AAC, 160 for Opus (the fixed re-encode targets — see Format Handling), and for MP3 it's whatever `selectedQuality` currently is (128/192/256/320), since MP3's bitrate is the one user-adjustable value in the UI — using anything else there would just be wrong, not merely imprecise.

Always prefixed with `≈`: for a stream-copy case the real output bitrate is the source's own, which isn't known client-side, so the estimate is honestly approximate rather than falsely precise.

**Recalculated on:** format change, MP3 quality change, trim range edits (both fields' `input` event, plus the trim-section expand/collapse toggle and the reset button), and the normalize checkbox (doesn't change the number — same bitrates — but the hook is wired so it doesn't silently break if that ever changes). Duration used is the trimmed range when trim is active, via `getEffectiveDuration()` — a deliberately read-only sibling of `validateTrim()` that never writes to `trimError`, so recalculating the estimate on an unrelated click (e.g. a quality button) can't silently clear an error message the user hasn't addressed yet. Any unparseable/invalid trim state just falls back to the full track duration for estimate purposes rather than surfacing an error — this is a ballpark number, not a submission gate.

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

## Thumbnail Downloader (`/thumbnail`)

A fourth, deliberately cheap landing page — no yt-dlp, no ffmpeg, no jobs. YouTube-only (the search cluster it targets is YouTube-specific, and YouTube's thumbnail URLs are predictable without any extraction; other sources would need a full yt-dlp info call to even locate a thumbnail URL, which defeats the "cheap" premise).

**Not a tool page.** `static/thumbnail.html` reuses `app.css` for the shared visual shell but does **not** load `app.js` — it has its own `static/thumb.js`. No `manifest.webmanifest` link, no `theme-color` meta, no service-worker registration: this is a lightweight utility page without jobs or an offline mode, so PWA installability doesn't apply to it directly. (The installed PWA's scope is still `/`, so `/thumbnail` opens fine *inside* an already-installed app — it just doesn't itself prompt installation.)

**Duplicated logic (two places to edit).** Because this page doesn't load `app.js`, two small pieces of its logic are copy-pasted into `thumb.js` instead of being extracted into a shared module — extracting would mean touching the file all four tool pages depend on, for the sake of one utility page:
- The cookie-banner wiring (`localStorage` check + accept handler)
- The FAQ accordion (`openFaqItem`/`closeFaqItem`, height-animation-via-forced-reflow)

If either behavior changes, it must be changed in **both** `app.js` and `thumb.js`, or the two will drift.

**Video ID extraction (client-side, `thumb.js`).** Parses the pasted URL and pulls the 11-character ID from `watch?v=`, `youtu.be/`, `/shorts/`, `/embed/`, and `music.youtube.com` — playlist params (`&list=...`) are simply ignored since only `v`/the path segment is read.

**Quality probing.** Four variants are probed via `new Image()` (`onload`/`onerror`): `maxresdefault` (1280×720), `sddefault` (640×480), `hqdefault` (480×360), `mqdefault` (320×180) — real `naturalWidth`/`naturalHeight` are read from the loaded image rather than hardcoded, since sizes occasionally differ.

**maxresdefault placeholder guard — planned defense, not reproduced live.** YouTube's CDN has a long-documented history (widely reported by yt-dlp/thumbnail-grabber tooling) of returning HTTP 200 with a 120×90 grey placeholder for a missing `maxresdefault`, instead of a clean 404 — which would make naive `onload`-only probing show a broken/fake "HD" result. `thumb.js` guards against this by checking `naturalWidth === 120 && naturalHeight === 90` after load. **However**, live verification during this implementation (2026-07-24, against several real videos including `jNQXAC9IVRw` — "Me at the zoo", confirmed to lack `maxresdefault`/`sddefault`) did **not** reproduce the placeholder: every missing quality returned a genuine HTTP 404, correctly caught by the existing `onerror` path with no help from the size guard. The guard is kept anyway as cheap defense-in-depth (costs nothing, and the historic behavior may resurface, vary by video age/region, or have applied only under conditions not covered by this test batch) — but treat it as unverified insurance, not a confirmed-necessary fix.

**`/api/thumbnail` — SSRF-proof by construction.** `GET /api/thumbnail?video_id=<11-char-id>&quality=<allowlisted>`: both params are validated (`^[A-Za-z0-9_-]{11}$` for the ID, an exact-match allowlist for quality) and the server **builds** the `i.ytimg.com` URL itself from these two validated parts — no user-supplied URL is ever accepted or fetched, so unlike `/api/info`/`/api/download` this endpoint needs no `validate_url` call and cannot be turned into a generic proxy no matter what's passed in. Fetches via `urllib.request` (5s timeout, response capped at `MAX_THUMB_BYTES` = 5MB, read in one shot rather than true chunked streaming — simple and fine at this size cap), returns the bytes with `Content-Disposition: attachment` so the "Скачать" button forces a save instead of just navigating to an image. Upstream 404 → clean 404; any other upstream failure → 502, both with Russian error text.

**Separate rate-limit bucket (`thumb_request_times`).** Deliberately not sharing the download rate limiter (`request_times`/`concurrent_counts`): a thumbnail fetch is a single cheap proxied request, not an ffmpeg job, so it shouldn't compete for the same hourly budget as real downloads in either direction. 30/hour per IP, no concurrency limit (nothing to hold a slot for — it's a synchronous fetch-and-return, not a background job). Same window constant pattern as the download limiter but its own dict, lock (`thumb_rate_lock`), and constant (`THUMB_RATE_LIMIT_PER_HOUR`); pruned by the same `_janitor` loop.

**Cross-linking.** All four tool pages link to `/thumbnail` via a second `.source-links` line, "Инструменты: Обложка YouTube" — deliberately not folded into the existing "Скачать из:" line, since a thumbnail isn't an audio source and mixing the two would read as semantically wrong. `/thumbnail` links back via its own "Скачать аудио из:" line listing all four tool pages.

## Blob-Once Download/Share Pattern

`/api/file/<job_id>` is one-shot — it deletes the file and pops the job entry right after serving. That means "Скачать файл" and "Поделиться" (mobile Web Share with files) can't each independently hit that endpoint: whichever comes second would 404 against an already-consumed job.

**`getFileBlob(jobId)` (`app.js`).** Both buttons call this instead of touching `/api/file` directly. It fetches the file exactly once per job and memoizes the in-flight/resolved `fetch(...).then(r => r.blob())` promise in `fileFetchPromise`; a second caller (whichever button is clicked second, in either order) gets the same promise/Blob instead of triggering a second request. On fetch failure the memoized promise is cleared so a retry is possible, and the UI surfaces it via the existing `setError()` path — a failure mode that didn't exist under the old flow, since the old "Скачать файл" just did `window.location.href = '/api/file/...'` and let the browser's native download handle (or silently swallow) any failure.

- **Скачать файл**: `getFileBlob()` → `URL.createObjectURL(blob)` → a programmatically-clicked `<a download>` → the object URL is revoked in the same `setTimeout` that already resets the UI 2s after the click (pre-existing timing; not changed by this feature — see the note below).
- **Поделиться**: `getFileBlob()` → `new File([blob], filename, {type})` → `navigator.share({files:[file]})`. Feature-detected once at script load via a probe `File` passed to `navigator.canShare` — `shareSupported` is computed once, not re-checked per click, and desktop browsers mostly fail it, so the button simply never becomes visible there (no dead control). `AbortError` (user cancelled the share sheet) and any other `navigator.share` rejection are both silent no-ops, matching the existing rejection-handling tone elsewhere (`fetchPreview`, the paste button).
- **Filename/MIME** are derived from `payload.format` — the format the job was actually *started* with, captured in the `startBtn` closure — not the live `selectedFmt`, for the same reason `job_done`/`job_error`/`share_used` already use the closure-captured `url`/`format`: the format buttons stay clickable during an in-flight conversion, so by the time the job finishes the user may be looking at a different pending selection. `FORMAT_EXT`/`FORMAT_MIME` in `app.js` mirror `FORMATS` in `app.py` (ext + MIME per format) — a small fact duplicated across client and server specifically so the Blob path can build a correct `<a download>` filename and `File` MIME type without an extra round trip; keep both in sync if a format is ever added.
- **Cleanup**: `reset()` clears `fileFetchPromise` (drops the Blob reference so a large file isn't held in memory once the user moves to a new track) and hides `shareBtn`, mirroring what it already did for `downloadBtn`.
- **Known pre-existing timing quirk, not introduced by this feature**: the 2-second `setTimeout` that resets the UI after a download click has always existed; if a user starts a *new* job within that 2s window, the stale timeout still fires and calls `reset()` against the new in-flight job. This edge case predates the Blob change (the old code had the exact same `setTimeout(() => { reset(); ... }, 2000)` shape) and wasn't in scope to fix here.

**`share_used` Umami event.** Fired on successful `navigator.share()` (not on cancel/failure), same `{source}` shape and `getSourceLabel`/`trackEvent` machinery as `job_done`/`job_error` — tells whether the feature earns its place before investing more in it.

## SEO Content & FAQ Accordion

Below the tool card, `static/index.html` has a static SEO content section (intro copy + FAQ accordion, grown incrementally as features ship — each new user-facing feature adds one matching FAQ entry) with matching `FAQPage` JSON-LD for search rich snippets. Targets audiograb.ru's core search terms (download audio from YouTube, YouTube to MP3/AAC, extract sound from video).

FAQ items are native `<details>`/`<summary>`, but with custom JS instead of default browser behavior, for two reasons: only one item open at a time, and an animated open/close instead of the instant native toggle.

- Summary clicks are intercepted (`preventDefault`) and `item.open` is set manually by `openFaqItem`/`closeFaqItem`, so the visual state and the semantic `open` attribute can be sequenced deliberately (e.g. the details element isn't marked closed until the collapse animation actually finishes)
- The browser's default UA rule hides a closed `<details>`'s content via `display:none`, which can't be animated — `.faq-content` overrides this to `display:block` unconditionally and uses `height` + `overflow:hidden` instead, so it's always in the layout and animatable
- Height animation is triggered by setting the start height, forcing a synchronous reflow (reading `.offsetHeight`), then setting the end height — not `requestAnimationFrame`. rAF depends on the tab actually compositing frames, which doesn't happen in some automated/headless/backgrounded contexts (hit this while testing in the browser-preview tool here); the forced-reflow technique doesn't have that dependency and works the same in every real browser tab

## Cookie Consent Banner

`static/index.html` shows a fixed bottom banner (non-blocking, just a notice — no accept/reject choice since the only cookie is the technical session cookie for rate limiting, not tracking) on first visit, styled with the same `:root` CSS variables as the rest of the page. Dismissal is recorded in `localStorage` (`cookie_consent` key) so it doesn't reappear.

## Analytics Events (Umami)

Custom Umami events (`window.umami.track(name, data)`) give per-source error tracking a graph instead of relying on user complaints — the realistic failure mode here is source-specific (e.g. YouTube tightening anti-bot on datacenter IPs, or TikTok/VK breaking their extractor after a site update).

Three events, all fired client-side from `static/app.js`:
- `preview_error` — `/api/info` returned an error (earliest signal of an extractor problem, before the user even attempts a download)
- `job_error` — `/api/status` polling reported a failed job
- `job_done` — the job completed successfully; also carries `format` (mp3/aac/opus) — free product-usage insight, and the denominator for computing an error *rate* per source (raw error counts alone just track traffic, not health)

Every event carries `source`, never the full URL or video ID (see `getSourceLabel(url)`): the URL's hostname, normalized to `youtube` / `tiktok` / `soundcloud` / `vk` / `other`. Matching is by `endsWith`-subdomain, not an exact-host list — `music.youtube.com` and `m.youtube.com` both land under `youtube` rather than `other`, which matters in practice since mobile share-target links (see PWA below) commonly arrive as `m.youtube.com`. Both of VK's live video domains are covered (`vk.com` — what copy-pasting a link from the VK web UI produces — and `vkvideo.ru`, the site's own `/vk` placeholder domain) and normalize to the same `vk` label. An unparseable/empty URL (`new URL()` throws) → `other`, never a crash.

**Correctness note:** `job_error`/`job_done` use the `url`/`format` values captured in the `startBtn` click-handler closure at job-start time (`payload.format`, the `url` const), not a live re-read of `urlInput.value`/`selectedFmt` when the status poll resolves. Both the URL field and the format buttons stay interactive during an in-flight conversion, so by the time a job finishes the user may already be looking at a different link or format — tracking the live DOM state at that point would silently mislabel the event.

**Robustness.** `trackEvent(name, data)` wraps every call: silent no-op with zero console output if `window.umami` isn't present at all (adblock, script blocked, analytics host down), never awaited (fire-and-forget, can't delay or break the core download flow).

**Accepted limitation:** client-side-only events miss adblock users entirely, and this is trend monitoring, not accounting — a server-side event push would be more complete but isn't worth the added complexity for what this is used for (spotting a source-specific failure-rate trend, not precise counting).

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

## PWA (Manifest, Icons, Share Target)

`static/manifest.webmanifest` is served via a dedicated `/manifest.webmanifest` route (`send_from_directory(..., mimetype="application/manifest+json")`) rather than relying on the generic static handler's extension-based MIME guessing, since `.webmanifest` isn't reliably in every Python install's `mimetypes` database. Linked from the four tool pages only (`index.html`, `tiktok.html`, `soundcloud.html`, `vk.html`), not `privacy.html`/`terms.html`/`404.html`.

- `theme_color`/`background_color` are `#0f0f0f` — the site's actual `--bg` from `app.css`, not the red accent, so the installed app's title bar/Android status bar tint stays subtle rather than a bright red. A matching `<meta name="theme-color">` is set in each tool page's `<head>` too, so the address-bar tint applies even before install (the manifest's `theme_color` only takes effect once installed).
- Icons (`icon-192.png`, `icon-512.png`, `icon-512-maskable.png`) are re-rendered from the same logo design as the inline `<svg>` in the tool-page header (not upscaled from `apple-touch-icon.png`, which turned out to be an older/simpler version of the mark missing the note-beam element below the play circle) — generated with Pillow (circle/triangle/line/rect primitives at 4x supersampling then downscaled), since no SVG rasterizer (rsvg-convert/Inkscape/ImageMagick) was available. The maskable variant scales the glyph to 45% of the canvas, centered, so it stays within the ~40%-radius safe zone Android's circular/squircle icon masks require — the flat ends of the horizontal bar element are the part that would clip first under an aggressive mask, that's what the safe-zone math was checked against.
- `share_target` (`action: "/"`, `method: "GET"`, params `title`/`text`/`url`) is why Android's share sheet hands a link off as `/?title=...&text=...&url=...` — see the share-target handling below for how the query string is consumed.

**Incoming Web Share Target.** On every page load, `app.js` checks `location.search` for a URL: `url` param first, then `text` (Android apps are inconsistent — YouTube commonly puts the link in `text`, others use `url`), via a plain `https?:\/\/\S+` regex over the raw param value. If one's found, it goes through `setUrlAndPreview` (see Programmatic URL Input below) rather than a second copy of that logic, and the query string is then dropped via `history.replaceState` so the address bar reads `/` again (also keeps `/?text=...` variants out of Umami's page-URL stats). No match → silent no-op. `title` is accepted in the manifest's `share_target.params` (some senders may include it) but never read for URL extraction — only `url`/`text` are.

Share Target is Android/Chrome-only (no iOS support); iOS users get an installable icon via "Add to Home Screen" with no share-target behavior, which is expected platform behavior, not a gap to close. The share sheet only offers AudioGrab as a target *after* the PWA has been installed — that's also platform behavior (Android indexes share targets from installed manifests), not something the app can trigger itself.

**Service worker (`static/sw.js`).** Deliberately minimal — a SW is required for installability, but caching bugs on a dynamic service (stale `app.js` served against a changed API) are silent and painful, so it caches exactly one thing: `offline.html` (precached on `install`). The `fetch` handler only intercepts navigation requests (`event.request.mode === 'navigate'`) — network-first, falling back to the cached `offline.html` on failure. Everything else (`app.css`, `app.js`, every `/api/*` call) is never touched by `respondWith` at all and goes straight to the network, so the SW can't become a second, competing cache layer alongside the existing `?v=` query-string versioning (see `patterns.md`) — a stale SW cache silently serving old `app.js` against a since-changed `/api/download` contract is exactly the failure mode this avoids. `skipWaiting()` + `clients.claim()` on install/activate so a new SW version takes over immediately rather than waiting for every open tab to close.

Served via its own `/sw.js` route (required — a service worker's scope is limited to its own directory and above, so it must be at the site root) with an explicit `Cache-Control: no-cache` response header, so browsers re-check the SW script itself promptly instead of pinning an old version for a long time.

`offline.html` follows the same self-contained standalone-page pattern as `404.html`/`privacy.html`/`terms.html` (own inline `:root` CSS copy, no shared stylesheet, `noindex`) — just swaps the copy for an offline notice and a "Повторить" button that does `location.reload()`.
