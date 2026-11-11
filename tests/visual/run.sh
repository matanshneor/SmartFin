#!/usr/bin/env bash
# Screenshot harness: starts the five demo servers, shoots, stops them.
#   PY=.venv/bin/python tests/visual/run.sh [groups...]      (groups: A B C D E S)
#   SHOTS=before tests/visual/run.sh A    → tests/visual/out/before/
# Needs requirements.txt installed in $PY, and Node Playwright with Chromium.
# Nothing touches the network or Supabase: see serve.py.
set -euo pipefail
cd "$(dirname "$0")"
PY="${PY:-python3}"
pids=()
start() { "$PY" -B serve.py "$1" "$2" ${3:-} >"out/log-$1.txt" 2>&1 & pids+=($!); }
mkdir -p out
start rich 8099
start new 8100
start onb 8101
start cards 8102 2027-01-01
start stress 8103
trap 'kill "${pids[@]}" 2>/dev/null || true' EXIT
for port in 8099 8100 8101 8102 8103; do
  for _ in $(seq 50); do curl -s -o /dev/null "http://127.0.0.1:$port/health" && break; sleep 0.2; done
done
node shoot.cjs "$@"
