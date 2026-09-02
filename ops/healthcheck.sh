#!/bin/sh
# Health thresholds for the production box. Single source of truth: the hourly
# GitHub workflow and the on-box systemd timer both run this, so a threshold
# edited here changes both. Kept POSIX sh — it is invoked over SSH, where the
# login shell is not guaranteed.
#
# Usage:
#   healthcheck.sh            print status, exit 0 healthy / 1 unhealthy
#   healthcheck.sh --notify   the same, plus a Telegram message on state change
#
# The two callers do different jobs on purpose. The timer runs often and
# notifies, giving a reliable cadence. The GitHub workflow runs whenever
# GitHub feels like it and only reports pass/fail — but it is the only one that
# can tell you the machine is unreachable, which a monitor living on that same
# machine can never do.
#
# Thresholds come from numbers actually observed, not round guesses; see
# deployment.md -> Monitoring for where each one comes from.

NOTIFY=0
[ "$1" = "--notify" ] && NOTIFY=1

ENV_FILE=/etc/yt2mp3-alerts.env
STATE_FILE=/var/lib/yt2mp3-healthcheck.state

FAILED=0
BREACHES=""
KEYS=""
# $1 is a stable key, $2 the human text. They are separate because the state
# used for de-duplication is built from the keys alone: the text carries the
# current reading, and hashing that made every fluctuating value look like a
# new problem. Two alerts arrived minutes apart on 2026-09-02 for the same
# ongoing condition, differing only in "836MB" versus "851MB".
note() {
    echo "UNHEALTHY: $2"
    BREACHES="${BREACHES}• $2
"
    KEYS="${KEYS}$1,"
    FAILED=1
}

DISK_PCT=$(df --output=pcent / | tail -1 | tr -dc '0-9')
DOWNLOADS_MB=$(du -sm /var/www/yt2mp3/downloads 2>/dev/null | cut -f1)
# `anon` from the cgroup, deliberately not systemd's MemoryCurrent. That figure
# includes the page cache, which grows with the sheer volume of file I/O a job
# performs — so it rises on exactly the large *legitimate* downloads this is not
# meant to flag. Measured 2026-09-02 mid-way through an ordinary audiobook
# conversion: MemoryCurrent read 830MB, of which 655MB was page cache the kernel
# drops on demand, and only 159MB was real process memory. Two false alarms came
# out of that before the metric was changed, and raising the threshold could
# never have fixed it — the wrong thing was being measured.
CGROUP=/sys/fs/cgroup/system.slice/yt2mp3.service
MEM_MB=$(awk '/^anon /{printf "%d", $2/1024/1024}' "$CGROUP/memory.stat" 2>/dev/null)
[ -z "$MEM_MB" ] && MEM_MB="unknown"
CRASHES=$(journalctl -u yt2mp3 --since "-70min" --no-pager 2>/dev/null \
          | grep -cE "WORKER TIMEOUT|was sent SIGKILL" || true)
HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 20 http://127.0.0.1:5000/ || echo "000")

SUMMARY="disk=${DISK_PCT}%  downloads=${DOWNLOADS_MB}MB  memory=${MEM_MB}MB  crashes_1h=${CRASHES}  http=${HTTP}"
echo "$SUMMARY"

# Disk went from comfortable to roughly fifteen minutes from full in under an
# hour on 2026-08-25, so this wants headroom rather than a last-moment warning.
[ "$DISK_PCT" -ge 80 ] && note "disk" "диск заполнен на ${DISK_PCT}%"

# downloads/ is transient: the janitor sweeps it hourly and it normally sits
# near empty. Sustained gigabytes means something is writing that will not stop
# on its own — during the incident this read 6.7GB.
[ -n "$DOWNLOADS_MB" ] && [ "$DOWNLOADS_MB" -ge 3000 ] && note "downloads" "downloads/ занимает ${DOWNLOADS_MB}MB"

# Now that this measures anon rather than page cache, the two states are far
# apart: 159MB while genuinely busy converting a 700MB audiobook, against 676MB
# with threads stuck on a livestream (2026-08-25). A four-fold gap, where
# MemoryCurrent left barely a hundred megabytes between them and could not be
# tuned into a usable signal at any threshold.
[ "$MEM_MB" != "unknown" ] && [ "$MEM_MB" -ge 400 ] && note "memory" "память воркера ${MEM_MB}MB"

[ "$CRASHES" -gt 0 ] && note "crashes" "воркер падал ${CRASHES} раз за час"

systemctl is-active --quiet yt2mp3 || note "inactive" "сервис не запущен"

[ "$HTTP" != "200" ] && note "http" "главная отвечает HTTP ${HTTP}"

# --- notification -------------------------------------------------------
# Only on a *change* of state. A breach that persists for hours would otherwise
# produce an identical message every run, which trains you to ignore them. The
# recovery message matters as much as the alert: without it silence is
# ambiguous, since it also describes a monitor that has quietly stopped running.
if [ "$NOTIFY" -eq 1 ]; then
    if [ "$FAILED" -eq 1 ]; then
        # Keys, not the rendered text: the text carries the live reading, so
        # hashing it made a drifting number ("836MB" then "851MB") read as a
        # brand-new problem and alert again. Kept unhashed — it is short, and a
        # state file saying "disk,memory," is worth more when debugging than a
        # hash. A genuinely new *kind* of breach still changes it and re-alerts.
        NEW_STATE="$KEYS"
        TEXT="⚠️ AudioGrab: проблема

${BREACHES}
${SUMMARY}"
    else
        NEW_STATE="ok"
        TEXT="✅ AudioGrab: снова в норме

${SUMMARY}"
    fi

    OLD_STATE=$(cat "$STATE_FILE" 2>/dev/null || echo "ok")

    # Nothing to say when the state has not moved, and nothing to say about a
    # first run that finds everything healthy.
    if [ "$NEW_STATE" != "$OLD_STATE" ]; then
        if [ -r "$ENV_FILE" ]; then
            . "$ENV_FILE"
            curl -s -o /dev/null --max-time 20 \
                 -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
                 -d "chat_id=${CHAT_ID}" \
                 --data-urlencode "text=${TEXT}"
        else
            echo "WARNING: $ENV_FILE unreadable, cannot notify"
        fi
    fi
    printf '%s' "$NEW_STATE" > "$STATE_FILE" 2>/dev/null
fi

[ "$FAILED" -eq 1 ] && exit 1
echo "healthy"
exit 0
