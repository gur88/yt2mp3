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
note() {
    echo "UNHEALTHY: $1"
    BREACHES="${BREACHES}• $1
"
    FAILED=1
}

DISK_PCT=$(df --output=pcent / | tail -1 | tr -dc '0-9')
DOWNLOADS_MB=$(du -sm /var/www/yt2mp3/downloads 2>/dev/null | cut -f1)
MEM_RAW=$(systemctl show yt2mp3 -p MemoryCurrent --value)
case "$MEM_RAW" in
    ''|*[!0-9]*) MEM_MB="unknown" ;;
    *)           MEM_MB=$((MEM_RAW / 1024 / 1024)) ;;
esac
CRASHES=$(journalctl -u yt2mp3 --since "-70min" --no-pager 2>/dev/null \
          | grep -cE "WORKER TIMEOUT|was sent SIGKILL" || true)
HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 20 http://127.0.0.1:5000/ || echo "000")

SUMMARY="disk=${DISK_PCT}%  downloads=${DOWNLOADS_MB}MB  memory=${MEM_MB}MB  crashes_1h=${CRASHES}  http=${HTTP}"
echo "$SUMMARY"

# Disk went from comfortable to roughly fifteen minutes from full in under an
# hour on 2026-08-25, so this wants headroom rather than a last-moment warning.
[ "$DISK_PCT" -ge 80 ] && note "диск заполнен на ${DISK_PCT}%"

# downloads/ is transient: the janitor sweeps it hourly and it normally sits
# near empty. Sustained gigabytes means something is writing that will not stop
# on its own — during the incident this read 6.7GB.
[ -n "$DOWNLOADS_MB" ] && [ "$DOWNLOADS_MB" -ge 3000 ] && note "downloads/ занимает ${DOWNLOADS_MB}MB"

# The weakest signal, tuned to fire only on clearly pathological state: 36-126MB
# at rest, but 755MB during a legitimate large download versus 852MB held for
# over an hour while stuck. Roughly a 100MB band, so treat disk and downloads/
# as the real detectors.
[ "$MEM_MB" != "unknown" ] && [ "$MEM_MB" -ge 800 ] && note "память воркера ${MEM_MB}MB"

[ "$CRASHES" -gt 0 ] && note "воркер падал ${CRASHES} раз за час"

systemctl is-active --quiet yt2mp3 || note "сервис не запущен"

[ "$HTTP" != "200" ] && note "главная отвечает HTTP ${HTTP}"

# --- notification -------------------------------------------------------
# Only on a *change* of state. A breach that persists for hours would otherwise
# produce an identical message every run, which trains you to ignore them. The
# recovery message matters as much as the alert: without it silence is
# ambiguous, since it also describes a monitor that has quietly stopped running.
if [ "$NOTIFY" -eq 1 ]; then
    if [ "$FAILED" -eq 1 ]; then
        NEW_STATE=$(printf '%s' "$BREACHES" | md5sum | cut -c1-32)
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
