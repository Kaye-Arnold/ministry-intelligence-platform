#!/bin/bash
# ============================================================
# Ministry Intelligence Platform (MIP) – Keep-Alive Daemon
# Prevents Oracle Cloud Always Free VM from being reclaimed
# due to inactivity. Scheduled via cron every 12 hours.
#
# Oracle reclaims idle instances when CPU usage stays near 0%
# for extended periods (typically ~7 days). This script runs
# stress-ng for 2 minutes to simulate activity.
#
# Cron entry (add via: crontab -e):
#   0 */12 * * * /opt/mip/scripts/keep_alive.sh >> /var/log/mip_keepalive.log 2>&1
# ============================================================

set -euo pipefail

LOG_FILE="/var/log/mip_keepalive.log"
STRESS_DURATION=120   # seconds
STRESS_CPU_WORKERS=1  # single worker is sufficient

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [KEEPALIVE] $*"
}

log "Keep-alive started. Running stress-ng for ${STRESS_DURATION}s..."

if ! command -v stress-ng &>/dev/null; then
    log "stress-ng not found. Installing..."
    apt-get install -y stress-ng >/dev/null 2>&1 || {
        log "Failed to install stress-ng. Falling back to dd loop."
        # Fallback: pure bash CPU activity
        for _ in $(seq 1 "$STRESS_DURATION"); do
            dd if=/dev/urandom bs=1M count=1 2>/dev/null | sha256sum >/dev/null
            sleep 1
        done
        log "Keep-alive complete (fallback method)."
        exit 0
    }
fi

stress-ng \
    --cpu "${STRESS_CPU_WORKERS}" \
    --timeout "${STRESS_DURATION}s" \
    --metrics-brief \
    2>&1 | tail -3

log "Keep-alive complete. Oracle VM activity signal sent."

# Also touch a heartbeat file (useful for external monitoring)
HEARTBEAT_FILE="/tmp/mip_keepalive_last_run"
echo "$(date '+%Y-%m-%d %H:%M:%S')" > "$HEARTBEAT_FILE"

exit 0
