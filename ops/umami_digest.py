#!/usr/bin/env python3
"""
Weekly Umami digest -> Telegram. Stdlib only, meant to run on the box from a
systemd timer, the same way ops/healthcheck.sh does.

    python3 ops/umami_digest.py [days] [env_file]

    days      window length, default 7. The digest compares this window with
              the equal window before it.
    env_file  KEY=VALUE file. On the box: /etc/yt2mp3-digest.env (off git,
              owned by the yt2mp3 user). Local dry run: ops/.env.umami.

Keys read (env var or env file):
    UMAMI_USERNAME  Umami login
    UMAMI_PASSWORD  its password
    UMAMI_URL       default http://127.0.0.1:3001 (Umami is bound to localhost
                    on the box, so no HTTPS round trip is needed there)
    UMAMI_WEBSITE   default the Audiograb site id
    BOT_TOKEN       Telegram bot token   } already in /etc/yt2mp3-alerts.env
    CHAT_ID         Telegram chat id     }

With no BOT_TOKEN the digest is printed to stdout instead of sent — that is the
local dry run (pass ops/.env.umami as the env file).
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

# The digest text is UTF-8 (Cyrillic + an emoji); a non-UTF-8 console (Windows
# dry run) would otherwise crash on print. No effect on the box or on the
# Telegram send path.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

DEFAULT_URL = "http://127.0.0.1:3001"
DEFAULT_SITE = "19460776-616d-4b04-888d-509b1d7ebba3"


def load_env(path):
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def api(method, url, token=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"! {method} {url.split('?')[0]} -> HTTP {e.code} {e.read()[:200]!r}",
              file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"! {method} {url.split('?')[0]} -> {e}", file=sys.stderr)
    return None


def source_of(e):
    """One tool per page, so the page an event fired on identifies the source."""
    p = (e.get("urlPath") or "").rstrip("/")
    t = e.get("pageTitle") or ""
    if p == "/vk" or "VK" in t:
        return "vk"
    if p == "/tiktok" or "TikTok" in t:
        return "tiktok"
    if p == "/soundcloud" or "SoundCloud" in t:
        return "soundcloud"
    if p == "/thumbnail":
        return "thumbnail"
    if p in ("", "/") and "YouTube" in t:
        return "youtube"
    if "static/" in p:
        return "localdev"
    return "other"


def pct(num, den):
    return f"{round(100 * num / den)}%" if den else "—"


def delta(cur, prev):
    if not prev:
        return ""
    d = cur - prev
    return f" ({'+' if d >= 0 else ''}{d} к пред.)"


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    env_file = sys.argv[2] if len(sys.argv) > 2 else "/etc/yt2mp3-digest.env"
    load_env(env_file)

    base = os.environ.get("UMAMI_URL", DEFAULT_URL).rstrip("/")
    site = os.environ.get("UMAMI_WEBSITE", DEFAULT_SITE)
    user = os.environ.get("UMAMI_USERNAME")
    pw = os.environ.get("UMAMI_PASSWORD")
    if not user or not pw:
        sys.exit("missing UMAMI_USERNAME / UMAMI_PASSWORD")

    auth = api("POST", f"{base}/api/auth/login",
               body={"username": user, "password": pw})
    if not auth or "token" not in auth:
        sys.exit("umami login failed")
    token = auth["token"]

    now = int(time.time() * 1000)
    span = days * 86400 * 1000
    cur_start, prev_start = now - span, now - 2 * span
    w = f"{base}/api/websites/{site}"

    def stats(a, b):
        s = api("GET", f"{w}/stats?startAt={a}&endAt={b}", token) or {}
        return s.get("visitors", 0), s.get("visits", 0)

    cur_visitors, cur_visits = stats(cur_start, now)
    prev_visitors, prev_visits = stats(prev_start, cur_start)

    # one pass over the raw event stream for the whole two-window range
    rows, page = [], 1
    while True:
        r = api("GET", f"{w}/events?startAt={prev_start}&endAt={now}"
                        f"&pageSize=200&page={page}", token)
        if not r or not r.get("data"):
            break
        rows += r["data"]
        if page * r["pageSize"] >= r.get("count", 0):
            break
        page += 1

    # Umami's createdAt is UTC ISO ("...Z"); this prefix compares lexically
    # against it to split events into the current vs previous window.
    cur_iso = datetime.fromtimestamp(cur_start / 1000, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S")

    def split(name):
        c = [e for e in rows if e.get("eventName") == name and e["createdAt"] >= cur_iso]
        p = [e for e in rows if e.get("eventName") == name and e["createdAt"] < cur_iso]
        return c, p

    done_c, done_p = split("job_done")
    perr_c, _ = split("preview_error")
    pok_c, _ = split("preview_ok")
    jerr_c, _ = split("job_error")
    dstart_c, _ = split("download_started")

    npok, ndstart, ndone = len(pok_c), len(dstart_c), len(done_c)
    # preview_ok / download_started ship 2026-09-08. Until a full window has
    # passed they undercount against job_done, so any rate built on them reads
    # as a wild >100%. Trust the funnel rates only once the counts sit in the
    # order a real funnel must: preview_ok >= download_started >= job_done.
    funnel_ready = npok >= ndstart >= ndone

    # per-source health, current window
    src_lines = []
    for src in ("vk", "youtube", "tiktok", "soundcloud"):
        pok = sum(1 for e in pok_c if source_of(e) == src)
        perr = sum(1 for e in perr_c if source_of(e) == src)
        done = sum(1 for e in done_c if source_of(e) == src)
        jerr = sum(1 for e in jerr_c if source_of(e) == src)
        if pok + perr + done + jerr == 0:
            continue
        # a finished download implies a shown preview, so pok < done means the
        # preview_ok stream for this source has not caught up yet
        prev = pct(pok, pok + perr) if pok >= done and pok else "н/д"
        src_lines.append(
            f"  {src}: превью {prev}, "
            f"загрузка {pct(done, done + jerr)} (n={done + jerr})")

    lines = [
        f"📊 AudioGrab — срез за {days} дн.",
        "",
        f"Посетители: {cur_visitors}{delta(cur_visitors, prev_visitors)}",
        f"Визиты: {cur_visits}{delta(cur_visits, prev_visits)}",
        f"Скачано: {ndone}{delta(ndone, len(done_p))}",
        "",
        "Воронка:",
    ]
    if funnel_ready:
        lines.append(
            f"  preview_ok {npok} → download {ndstart} "
            f"({pct(ndstart, npok)}) → done {ndone} ({pct(ndone, ndstart)})")
    else:
        lines.append(f"  preview_ok {npok} · download {ndstart} · done {ndone}")
        lines.append("  (preview_ok / download_started собираются с 8 сен — "
                     "проценты позже)")
    if src_lines:
        lines += ["", "Здоровье по источникам:"] + src_lines
    lines += ["", f"Полная выгрузка: ops/umami_export.py {days}"]
    text = "\n".join(lines)

    bot, chat = os.environ.get("BOT_TOKEN"), os.environ.get("CHAT_ID")
    if not bot or not chat:
        print(text)
        return
    api("POST", f"https://api.telegram.org/bot{bot}/sendMessage",
        body={"chat_id": chat, "text": text})


if __name__ == "__main__":
    main()
