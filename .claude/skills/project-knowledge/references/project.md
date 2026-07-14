# Project

## Overview

yt2mp3 (public site: audiograb.ru) extracts audio from YouTube (and other sites supported by yt-dlp) directly into an audio file — the video stream is never downloaded. Flask web app, deployed publicly for all users (see [[project-yt2mp3]] memory — not just a local/personal tool).

## Audience

Public, anonymous users on the open internet — no auth, no accounts, no multi-tenancy, IP-based rate limiting instead.

## Problem It Solves

Avoids downloading full video files when only the audio track is needed.

## Key Features

- Paste a URL, start an extraction job (MVP)
- Format selection: MP3, AAC (m4a), Opus — quality-preserving copy when the source codec already matches the target (Critical)
- Live progress reporting: download phase mapped to 0–90%, ffmpeg conversion phase mapped to 90–100% via `-progress pipe:1` parsing (Critical)
- Adjustable bitrate/quality for re-encoded formats (Important)
- Track preview before download: title, artist, thumbnail via `/api/info` (metadata only, no download), shown with a debounce after the user pastes a URL (Important)
- Embedded cover art: the video's thumbnail is muxed into the output file as attached-picture cover art (mp3/aac only — Opus's muxer doesn't support it, falls back to no cover), so it shows up in players like Яндекс Музыка (Important)
- Downloaded file is served once and then deleted from the server (Critical — no persistent storage of user files)
- Per-IP rate limiting: max 3 concurrent downloads, max 10/hour (Critical for public deployment)

## Scope Boundaries

- No user accounts, no history, no queue persistence across restarts (jobs live in an in-memory dict, single process)
- No video download or video processing
