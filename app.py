import re
import subprocess
import threading
import time
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
            return ["-vn", "-codec:a", "copy"]
        return ["-codec:a", "libopus", "-b:a", "160k"]
    return []


def run_ffmpeg_with_progress(job_id: str, input_path: Path, output_path: Path,
                              duration: float, quality: int, fmt: str) -> bool:
    """
    Run ffmpeg and parse its -progress output to update jobs[job_id]['percent']
    from 90.00 → 99.99 during conversion.
    """
    codec_args = ffmpeg_codec_args(fmt, input_path, quality)

    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        *codec_args,
        "-progress", "pipe:1",
        "-nostats",
        str(output_path),
    ]

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
        # No yt-dlp postprocessors — we run ffmpeg manually for full progress
    }

    try:
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

        # Output path
        ext         = FORMATS[fmt]["ext"]
        output_path = OUTPUT_DIR / f"{job_id}_out.{ext}"

        # Convert / remux with live progress
        jobs[job_id]["stage"] = "converting"
        ok = run_ffmpeg_with_progress(job_id, input_path, output_path, duration, quality, fmt)

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
        release_concurrent_slot(ip)


@app.route("/")
def index():
    return app.send_static_file("index.html")


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
