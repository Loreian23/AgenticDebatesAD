#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

check_pair() {
  local legacy="$1"
  local canonical="$2"

  if [[ ! -d "$legacy" || ! -d "$canonical" ]]; then
    return 0
  fi

  local out
  out="$(diff -qr "$legacy" "$canonical" \
    -x '__pycache__' -x '*.pyc' -x '*.pyo' -x '*.pyd' 2>/dev/null || true)"

  if [[ -n "$out" ]]; then
    echo "Drift detected between $legacy and $canonical"
    echo "$out"
    return 1
  fi

  echo "OK: $legacy matches $canonical"
}

status=0
check_pair "elo" "src/elo" || status=1
check_pair "federation" "src/federation" || status=1
check_pair "tournaments" "src/tournaments" || status=1

if [[ $status -ne 0 ]]; then
  echo "\nDuplicate-tree drift detected. Canonical tree is src/*." >&2
  if [[ "${STRICT:-0}" == "1" ]]; then
    exit 1
  fi
  echo "Running in non-strict mode (STRICT=0), continuing." >&2
fi

exit 0
