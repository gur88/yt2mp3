import re
import subprocess
import threading
import time
import urllib.request
import uuid
from pathlib import Path

from flask import Flask, jsonify, request, send_file, after_this_request
import yt_dlp

app = Flask(__name__, static_folder="static", static_url_path="")
logger = app.logger

OUTPUT_DIR = Path("downloads")
OUTPUT_DIR.mkdir(exist_ok=True)

jobs: dict[str, dict] = {}

# --- Rate limiting (in-memory, per client IP) ---
RATE_LIMIT_WINDOW    = 3600  # seconds
RATE_LIMIT_PER_HOUR  = 10
MAX_CONCURRENT_PER_IP = 3

rate_lock       = threading.Lock()
request_times: dict[str, list[float]] = {}   # ip -> timestamps of started downloads in the window
concurrent_counts: dict[str, int] = {}       # ip -> number of downloads currently running


def get_client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def check_rate_limit(ip: str) -> str | None:
    """Return a Russian error message if the IP is over a limit, else None. Reserves a slot on success."""
    now = time.monotonic()
    with rate_lock:
        times = [t for t in request_times.get(ip, []) if now - t < RATE_LIMIT_WINDOW]

        if concurrent_counts.get(ip, 0) >= MAX_CONCURRENT_PER_IP:
            request_times[ip] = times
            return (f"Слишком много одновременных скачиваний ({MAX_CONCURRENT_PER_IP}). "
                     f"Дождитесь завершения текущих загрузок.")

        if len(times) >= RATE_LIMIT_PER_HOUR:
            request_times[ip] = times
            return (f"Превышен лимит {RATE_LIMIT_PER_HOUR} скачиваний в час с одного IP. "
                     f"Попробуйте позже.")

        times.append(now)
        request_times[ip] = times
        concurrent_counts[ip] = concurrent_counts.get(ip, 0) + 1
        return None


def release_concurrent_slot(ip: str) -> None:
    with rate_lock:
        if ip in concurrent_counts:
            concurrent_counts[ip] -= 1
            if concurrent_counts[ip] <= 0:
                del concurrent_counts[ip]

FORMATS = {
    "mp3":  {"ext": "mp3",  "mime": "audio/mpeg"},
    "aac":  {"ext": "m4a",  "mime": "audio/mp4"},
    "opus": {"ext": "opus", "mime": "audio/ogg"},
}

FMT_SELECTORS = {
    "mp3":  "bestaudio/best",
    "aac":  "bestaudio[ext=m4a]/bestaudio/best",
    "opus": "bestaudio[ext=webm]/bestaudio/best",
}

def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "_", name).strip()


def _cleanup_file(filepath: Path, attempts: int = 5, delay: float = 0.5) -> None:
    """
    Delete filepath, retrying briefly if the OS still holds a handle open
    (observed on Windows dev server right after send_file finishes).
    """
    for attempt in range(attempts):
        try:
            filepath.unlink(missing_ok=True)
            return
        except OSError:
            if attempt == attempts - 1:
                logger.warning("Could not delete %s after %d attempts", filepath, attempts)
                return
            time.sleep(delay)


def ffmpeg_codec_args(fmt: str, input_path: Path, quality: int) -> list[str]:
    """Return ffmpeg codec arguments for the given format."""
    ext = input_path.suffix.lower()
    if fmt == "mp3":
        return ["-codec:a", "libmp3lame", "-b:a", f"{quality}k"]
    elif fmt == "aac":
        # Copy if source is already aac/m4a, else re-encode
        if ext in (".m4a", ".mp4"):
            return ["-codec:a", "copy"]
        return ["-codec:a", "aac", "-b:a", "192k"]
    elif fmt == "opus":
        # Copy opus stream if source is webm/opus
        if ext in (".webm", ".opus"):
            return ["-codec:a", "copy"]
        return ["-codec:a", "libopus", "-b:a", "160k"]
    return []


def _build_ffmpeg_cmd(input_path: Path, output_path: Path, codec_args: list[str],
                       thumbnail_path: Path | None) -> list[str]:
    cmd = ["ffmpeg", "-y", "-i", str(input_path)]
    if thumbnail_path:
        cmd += ["-i", str(thumbnail_path), "-map", "0:a", "-map", "1:v"]
    else:
        # No cover to embed — make sure a video stream in the source (e.g. a
        # thumbnail track inside a webm) isn't picked up by default.
        cmd += ["-vn"]
    cmd += codec_args
    if thumbnail_path:
        cmd += ["-c:v", "mjpeg", "-disposition:v:0", "attached_pic",
                "-metadata:s:v", "title=Album cover", "-metadata:s:v", "comment=Cover (front)"]
    cmd += ["-progress", "pipe:1", "-nostats", str(output_path)]
    return cmd


def run_ffmpeg_with_progress(job_id: str, input_path: Path, output_path: Path,
                              duration: float, quality: int, fmt: str,
                              thumbnail_path: Path | None = None) -> bool:
    """
    Run ffmpeg and parse its -progress output to update jobs[job_id]['percent']
    from 90.00 → 99.99 during conversion. If thumbnail_path is given, embeds it
    as cover art; on failure, retries once without the cover so a container/codec
    quirk with the thumbnail doesn't break the whole conversion.
    """
    codec_args = ffmpeg_codec_args(fmt, input_path, quality)

    def attempt(with_cover: bool) -> bool:
        cmd = _build_ffmpeg_cmd(input_path, output_path, codec_args,
                                 thumbnail_path if with_cover else None)
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
        )
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("out_time_ms="):
                try:
                    ms = int(line.split("=")[1])
                    if duration > 0 and ms >= 0:
                        # Map 0..duration → 90.00..99.99
                        conv_pct = min(100.0, (ms / 1_000_000) / duration * 100)
                        jobs[job_id]["percent"] = round(90.0 + conv_pct * 0.0999, 2)
                except (ValueError, ZeroDivisionError):
                    pass
        proc.wait()
        return proc.returncode == 0

    if thumbnail_path and attempt(with_cover=True):
        return True
    if thumbnail_path:
        logger.warning("Cover art embed failed for %s, retrying without cover", input_path)
    return attempt(with_cover=False)


def run_download(job_id: str, url: str, fmt: str, quality: int, ip: str) -> None:
    output_template = str(OUTPUT_DIR / f"{job_id}_%(title)s.%(ext)s")
    downloaded_file: list[Path | None] = [None]

    def progress_hook(d):
        if d["status"] == "downloading":
            raw = d.get("_percent_str", "0%").strip()
            m = re.search(r"([\d.]+)", raw)
            raw_pct = float(m.group(1)) if m else 0
            # Download phase occupies 0 → 90 on the bar
            jobs[job_id]["percent"] = round(raw_pct * 0.9, 2)
            jobs[job_id]["stage"]   = "downloading"
        elif d["status"] == "finished":
            downloaded_file[0]      = Path(d["filename"])
            jobs[job_id]["percent"] = 90.0
            jobs[job_id]["stage"]   = "converting"

    ydl_opts = {
        "format":         FMT_SELECTORS[fmt],
        "outtmpl":        output_template,
        "progress_hooks": [progress_hook],
        "quiet":          True,
        "no_warnings":    True,
        "noplaylist":     True,
        # No yt-dlp postprocessors — we run ffmpeg manually for full progress
    }

    thumb_path: Path | None = None
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": True}) as ydl:
            flat_info = ydl.extract_info(url, download=False)
        if flat_info.get("_type") == "playlist":
            raise ValueError("Ссылки на плейлисты пока не поддерживаются, вставьте ссылку на конкретное видео")

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info     = ydl.extract_info(url, download=True)
            title    = sanitize_filename(info.get("title", job_id))
            duration = float(info.get("duration") or 0)

        # Locate the downloaded file
        input_path = downloaded_file[0]
        if not input_path or not input_path.exists():
            candidates = [f for f in OUTPUT_DIR.iterdir() if f.name.startswith(job_id)]
            if not candidates:
                raise FileNotFoundError("Downloaded audio file not found")
            input_path = candidates[0]

        thumb_url = info.get("thumbnail")
        if thumb_url:
            thumb_path = OUTPUT_DIR / f"{job_id}_thumb"
            try:
                with urllib.request.urlopen(thumb_url, timeout=10) as resp:
                    thumb_path.write_bytes(resp.read())
            except Exception:
                logger.warning("Could not fetch thumbnail for %s", job_id)
                thumb_path = None

        # Output path
        ext         = FORMATS[fmt]["ext"]
        output_path = OUTPUT_DIR / f"{job_id}_out.{ext}"

        # Convert / remux with live progress
        jobs[job_id]["stage"] = "converting"
        ok = run_ffmpeg_with_progress(job_id, input_path, output_path, duration, quality, fmt,
                                       thumbnail_path=thumb_path)

        # Remove raw source file
        input_path.unlink(missing_ok=True)

        if not ok:
            raise RuntimeError("FFmpeg conversion failed")

        jobs[job_id].update({
            "status":   "done",
            "filename": output_path.name,
            "title":    title,
            "percent":  100.0,
        })

    except Exception as e:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"]  = str(e)
    finally:
        if thumb_path:
            thumb_path.unlink(missing_ok=True)
        release_concurrent_slot(ip)


@app.route("/")
def index():
    return app.send_static_file("index.html")


@app.route("/privacy")
def privacy():
    return app.send_static_file("privacy.html")


@app.errorhandler(404)
def not_found(e):
    return app.send_static_file("404.html"), 404


@app.route("/terms")
def terms():
    return app.send_static_file("terms.html")


@app.route("/api/info", methods=["POST"])
def get_info():
    data = request.get_json(force=True)
    url  = (data.get("url") or "").strip()

    if not url:
        return jsonify({"error": "URL is required"}), 400

    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": True}) as ydl:
            flat_info = ydl.extract_info(url, download=False)
        if flat_info.get("_type") == "playlist":
            return jsonify({"error": "Ссылки на плейлисты пока не поддерживаются, вставьте ссылку на конкретное видео"}), 400

        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True}) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        logger.exception(e)
        return jsonify({"error": "Не удалось получить информацию о видео. Проверьте ссылку."}), 400

    return jsonify({
        "title":     info.get("title"),
        "artist":    info.get("artist") or info.get("uploader") or info.get("channel"),
        "thumbnail": info.get("thumbnail"),
        "duration":  info.get("duration"),
    })


@app.route("/api/download", methods=["POST"])
def start_download():
    data    = request.get_json(force=True)
    url     = (data.get("url") or "").strip()
    fmt     = data.get("format", "aac")
    quality = int(data.get("quality", 192))

    if not url:
        return jsonify({"error": "URL is required"}), 400
    if fmt not in FORMATS:
        return jsonify({"error": "Unknown format"}), 400

    ip = get_client_ip()
    limit_error = check_rate_limit(ip)
    if limit_error:
        return jsonify({"error": limit_error}), 429

    job_id = uuid.uuid4().hex[:10]
    jobs[job_id] = {
        "status": "pending", "stage": "pending",
        "percent": 0.0, "filename": None, "error": None,
    }

    threading.Thread(
        target=run_download, args=(job_id, url, fmt, quality, ip), daemon=True
    ).start()

    return jsonify({"job_id": job_id})


@app.route("/api/status/<job_id>")
def job_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Unknown job"}), 404
    return jsonify(job)


@app.route("/api/file/<job_id>")
def download_file(job_id: str):
    job = jobs.get(job_id)
    if not job or job["status"] != "done":
        return jsonify({"error": "File not ready"}), 404

    filepath = OUTPUT_DIR / job["filename"]
    if not filepath.exists():
        return jsonify({"error": "File missing on disk"}), 404

    ext     = filepath.suffix.lstrip(".")
    mime    = FORMATS.get(ext, {}).get("mime", "audio/mpeg")
    dl_name = f"{job['title']}.{ext}"

    @after_this_request
    def remove_file(response):
        jobs.pop(job_id, None)
        threading.Thread(target=_cleanup_file, args=(filepath,), daemon=True).start()
        return response

    return send_file(filepath, as_attachment=True,
                     download_name=dl_name, mimetype=mime)


if __name__ == "__main__":
    print("🎵  YT → Audio  |  http://localhost:5000")
    app.run(debug=False, port=5000)
