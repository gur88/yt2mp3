#!/usr/bin/env python3
"""
Umami self-hosted export. Stdlib only (no pip installs).

Usage (run from the repo root):
    python ops/umami_export.py [days] [env_file]

    days      lookback window, default 90
    env_file  KEY=VALUE file, default ops/.env.umami

Required keys in the env file (or real environment variables):
    UMAMI_URL       e.g. https://analytics.audiograb.ru
    UMAMI_USERNAME  Umami login (the "admin" shown in the UI)
    UMAMI_PASSWORD  its password
    UMAMI_WEBSITE   website id (Audiograb = 19460776-616d-4b04-888d-509b1d7ebba3)

Writes JSON + CSV into ./umami_export_<timestamp>/ (git-ignored).
"""

import csv
import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

TZ = "Europe/Moscow"


def load_env(path):
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def need(key):
    val = os.environ.get(key)
    if not val:
        sys.exit(f"missing {key} (env var or env file)")
    return val.rstrip("/") if key == "UMAMI_URL" else val


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
        print(f"  ! {method} {url.split('?')[0]} -> HTTP {e.code} {e.read()[:200]!r}")
    except Exception as e:  # noqa: BLE001
        print(f"  ! {method} {url.split('?')[0]} -> {e}")
    return None


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    env_file = sys.argv[2] if len(sys.argv) > 2 else os.path.join("ops", ".env.umami")
    load_env(env_file)

    base = need("UMAMI_URL")
    user = need("UMAMI_USERNAME")
    pw = need("UMAMI_PASSWORD")
    site = need("UMAMI_WEBSITE")

    end = int(time.time() * 1000)
    start = end - days * 86400 * 1000
    qs = f"startAt={start}&endAt={end}"

    print(f"login {base} as {user} ...")
    auth = api("POST", f"{base}/api/auth/login", body={"username": user, "password": pw})
    if not auth or "token" not in auth:
        sys.exit("login failed")
    token = auth["token"]

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = f"umami_export_{stamp}"
    os.makedirs(out, exist_ok=True)
    print(f"window: {datetime.fromtimestamp(start/1000):%Y-%m-%d} .. "
          f"{datetime.fromtimestamp(end/1000):%Y-%m-%d}  ({days}d)")
    print(f"out: {out}/")

    def save(name, obj):
        if obj is None:
            return
        with open(f"{out}/{name}.json", "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
        print(f"  + {name}.json")

    def save_csv(name, rows, header):
        if not rows:
            return
        with open(f"{out}/{name}.csv", "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            w.writerows(rows)
        print(f"  + {name}.csv")

    w = f"{base}/api/websites/{site}"

    save("stats", api("GET", f"{w}/stats?{qs}", token))

    pv = api("GET", f"{w}/pageviews?{qs}&unit=day&timezone={TZ}", token)
    save("pageviews_daily", pv)
    if pv and isinstance(pv, dict):
        views = {r["x"]: r["y"] for r in pv.get("pageviews", [])}
        sess = {r["x"]: r["y"] for r in pv.get("sessions", [])}
        keys = sorted(set(views) | set(sess))
        save_csv("pageviews_daily", [[k, views.get(k, 0), sess.get(k, 0)] for k in keys],
                 ["date", "pageviews", "sessions"])

    # this Umami build rejects type=url; "path" is the working name for page paths
    for mtype in ("path", "referrer", "country", "browser", "os", "device", "event"):
        m = api("GET", f"{w}/metrics?{qs}&type={mtype}&limit=200", token)
        save(f"metric_{mtype}", m)
        if isinstance(m, list):
            save_csv(f"metric_{mtype}", [[r.get("x"), r.get("y")] for r in m],
                     [mtype, "count"])

    # Per-source event breakdown. The event-data value endpoints return [] on
    # this build, so page through the raw event stream and attribute each custom
    # event to a source by the page it fired on (one tool per page).
    rows = []
    page = 1
    while True:
        r = api("GET", f"{w}/events?{qs}&pageSize=200&page={page}", token)
        if not r or not r.get("data"):
            break
        rows += r["data"]
        if page * r["pageSize"] >= r.get("count", 0):
            break
        page += 1
    save("events_raw", rows)

    def source_of(e):
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
        return f"other({p or '/'})"

    custom = [e for e in rows if e.get("eventName")]
    breakdown = {}
    for e in custom:
        breakdown.setdefault(e["eventName"], {})
        s = source_of(e)
        breakdown[e["eventName"]][s] = breakdown[e["eventName"]].get(s, 0) + 1
    save("event_source_breakdown", breakdown)
    flat = [[ev, s, n] for ev, d in breakdown.items() for s, n in sorted(d.items())]
    save_csv("event_source_breakdown", flat, ["event", "source", "count"])

    print("done.")


if __name__ == "__main__":
    main()
