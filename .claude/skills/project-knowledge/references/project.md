# Project

## Overview

yt2mp3 extracts audio from YouTube (and other sites supported by yt-dlp) directly into an audio file — the video stream is never downloaded. Runs as a local Flask web app.

## Audience

Single user, local use (`localhost:5000`), no auth, no multi-tenancy.

## Problem It Solves

Avoids downloading full video files when only the audio track is needed.

## Key Features

- Paste a URL, start an extraction job (MVP)
- Format selection: MP3, AAC (m4a), Opus — quality-preserving copy when the source codec already matches the target (Critical)
- Live progress reporting: download phase mapped to 0–90%, ffmpeg conversion phase mapped to 90–100% via `-progress pipe:1` parsing (Critical)
- Adjustable bitrate/quality for re-encoded formats (Important)
- Downloaded file is served once and then deleted from the server (Critical — no persistent storage of user files)

## Scope Boundaries

- No user accounts, no history, no queue persistence across restarts (jobs live in an in-memory dict)
- No video download or video processing
- Single-machine/local deployment only — not designed for concurrent multi-user production use
