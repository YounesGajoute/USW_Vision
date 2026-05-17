#!/usr/bin/env bash
# Master-side wrapper (US Machine). Loads backend/.env, prefers VISION_URL, calls vision_master_client.py.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CLIENT="$SCRIPT_DIR/vision_master_client.py"

# Stale shell export overrides backend/.env — especially http://127.0.0.1:5000/api
if [[ -n "${VISION_SLAVE_URL:-}" ]]; then
  case "$VISION_SLAVE_URL" in
    *127.0.0.1*|*localhost*)
      echo "vision-master: unsetting VISION_SLAVE_URL=$VISION_SLAVE_URL (vision API is not on the master)" >&2
      unset VISION_SLAVE_URL
      ;;
  esac
fi

if [[ -f "$REPO_ROOT/backend/.env" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "$REPO_ROOT/backend/.env"
  set +a
elif [[ -f "$REPO_ROOT/.env" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "$REPO_ROOT/.env"
  set +a
fi

if [[ -z "${VISION_URL:-}" && -z "${VISION_SLAVE_URL:-}" ]]; then
  echo "vision-master: set VISION_URL in backend/.env (e.g. http://192.168.10.2:5000)" >&2
  echo "See docs/MASTER_VISION_CONNECTIVITY.md" >&2
  exit 1
fi

exec python3 "$CLIENT" "$@"
