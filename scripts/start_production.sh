#!/bin/bash
# Used by systemd (inspection-vision.service): build if needed, clear stale binds,
# start Next (3000) + Flask (5000), wait until both answer HTTP, then supervise npm.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Socket.IO inherit auth: bake remote key into Next client bundle at build time.
load_backend_env() {
  if [[ -f backend/.env ]]; then
    set -a
    # shellcheck source=/dev/null
    . ./backend/.env
    set +a
  fi
  if [[ -n "${VISION_REMOTE_API_KEY:-}" ]]; then
    export NEXT_PUBLIC_VISION_SOCKETIO_KEY="$VISION_REMOTE_API_KEY"
  fi
}

load_backend_env

socketio_build_marker() {
  echo -n "${NEXT_PUBLIC_VISION_SOCKETIO_KEY:-}" | sha256sum | awk '{print $1}'
}

# Free stale listeners from a crashed / timed-out prior run (same user; avoids EADDRINUSE).
free_ports() {
  if command -v fuser >/dev/null 2>&1; then
    fuser -k -TERM 3000/tcp 2>/dev/null || true
    fuser -k -TERM 5000/tcp 2>/dev/null || true
    sleep 1
  fi
}

recursive_sig() {
  local sig=$1
  local p=$2
  local c
  [[ -z "$p" ]] && return 0
  for c in $(pgrep -P "$p" 2>/dev/null || true); do
    recursive_sig "$sig" "$c"
  done
  kill -s "$sig" "$p" 2>/dev/null || true
}

MARKER=".next/.vision_socketio_key_hash"
CURRENT_HASH="$(socketio_build_marker)"
STORED_HASH=""
[[ -f "$MARKER" ]] && STORED_HASH="$(cat "$MARKER")"

if [[ ! -f .next/BUILD_ID ]]; then
  echo "inspection-vision: no .next production build found; running npm run build (first deploy or after clean)..." >&2
  /usr/bin/npm run build
  echo "$CURRENT_HASH" >"$MARKER"
elif [[ -n "$CURRENT_HASH" && "$CURRENT_HASH" != "$STORED_HASH" ]]; then
  echo "inspection-vision: Socket.IO auth key changed; rebuilding Next.js bundle..." >&2
  /usr/bin/npm run build
  echo "$CURRENT_HASH" >"$MARKER"
elif [[ -n "$CURRENT_HASH" && ! -f "$MARKER" ]]; then
  echo "inspection-vision: rebuilding Next.js so Socket.IO auth is embedded (one-time)..." >&2
  /usr/bin/npm run build
  echo "$CURRENT_HASH" >"$MARKER"
fi

free_ports

# Flask answers here once the server thread is up; Next on /. Camera init runs before bind — allow long wait.
wait_for_stack() {
  local max_s="${1:-600}"
  local i
  local ok_next ok_api

  for ((i = 1; i <= max_s; i++)); do
    ok_next=1
    ok_api=1
    if command -v curl >/dev/null 2>&1; then
      curl -sf --max-time 2 "http://127.0.0.1:3000/" >/dev/null 2>&1 || ok_next=0
      curl -sf --max-time 2 "http://127.0.0.1:5000/api/health/live" >/dev/null 2>&1 || ok_api=0
    else
      # No curl: TCP-only check (install curl on Pi for stronger readiness).
      timeout 1 bash -c "echo >/dev/tcp/127.0.0.1/3000" 2>/dev/null || ok_next=0
      timeout 1 bash -c "echo >/dev/tcp/127.0.0.1/5000" 2>/dev/null || ok_api=0
    fi
    if [[ "$ok_next" -eq 1 && "$ok_api" -eq 1 ]]; then
      echo "inspection-vision: stack ready after ${i}s (Next :3000, Flask :5000)." >&2
      return 0
    fi
    sleep 1
  done
  echo "inspection-vision: timed out after ${max_s}s waiting for ports 3000 and 5000." >&2
  return 1
}

/usr/bin/npm run start:all &
STACK_PID=$!

if ! wait_for_stack 600; then
  recursive_sig TERM "$STACK_PID"
  sleep 2
  recursive_sig KILL "$STACK_PID"
  wait "$STACK_PID" 2>/dev/null || true
  free_ports
  exit 1
fi

wait "$STACK_PID"
