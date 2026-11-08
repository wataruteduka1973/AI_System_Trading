#!/bin/sh
# Keeps the trading worker running on macOS / Linux: the counterpart of
# scripts/windows/run_trading_worker.cmd. Call it from whatever keeps processes alive (systemd,
# launchd, tmux, a login item). Restarts the worker 30 seconds after it exits, so a crash or a
# database restart does not stop paper trading for good. Output goes to
# logs/trading-worker-autostart.log.
#
# Uses the project's Python (.venv, then .venv313), never one from PATH: that one has no project
# dependencies, and the worker would fail and restart forever.
#
# Start the launcher with `./start-local.sh --no-trading-worker` while this is running. A second
# trading worker does no harm (an evaluation is idempotent per bar) but doubles the work and the log.
set -u
cd "$(dirname "$0")/../.."
mkdir -p logs
LOG="logs/trading-worker-autostart.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >>"$LOG"
}

LOCAL_PYTHON=""
for venv in .venv .venv313; do
    if [ -x "$venv/bin/python" ]; then
        LOCAL_PYTHON="$venv/bin/python"
        break
    fi
done
if [ -z "$LOCAL_PYTHON" ]; then
    log "no project Python found (.venv or .venv313); see README.md"
    exit 1
fi

child=""
sleeper=""
stop() {
    [ -n "$sleeper" ] && kill "$sleeper" 2>/dev/null
    if [ -n "$child" ]; then
        kill -TERM "$child" 2>/dev/null
        wait "$child" 2>/dev/null
    fi
    log "stopped"
    exit 0
}
trap stop TERM INT

while true; do
    log "starting trading worker"
    "$LOCAL_PYTHON" -m app.trading.worker >>"$LOG" 2>&1 &
    child=$!
    wait "$child"
    code=$?
    child=""
    log "trading worker exited with $code; restarting in 30s"
    sleep 30 &
    sleeper=$!
    wait "$sleeper"
    sleeper=""
done
