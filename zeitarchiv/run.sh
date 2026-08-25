#!/usr/bin/env bash
set -Eeuo pipefail

# HTTP-Zugriffe protokolliert die Anwendung selbst entsprechend der gewählten
# Einstellung; Uvicorns pauschales Accesslog würde jeden Messwert doppeln.
uvicorn app.main:app --host 127.0.0.1 --port 8128 --no-access-log &
uvicorn_pid=$!

nginx -g 'daemon off;' &
nginx_pid=$!

shutdown() {
    trap - TERM INT
    kill -TERM "$uvicorn_pid" "$nginx_pid" 2>/dev/null || true
    wait "$uvicorn_pid" 2>/dev/null || true
    wait "$nginx_pid" 2>/dev/null || true
}

trap shutdown TERM INT EXIT

# Wenn einer der beiden Prozesse unerwartet endet, wird auch der andere
# beendet. So bleibt kein scheinbar gesunder Gateway- oder App-Prozess zurueck.
wait -n "$uvicorn_pid" "$nginx_pid"
