#!/usr/bin/env bash
# Start the whole project in one terminal: API (:8000), worker, and web app (:3000).
# Logs are prefixed per service. Ctrl+C stops everything. If one service dies, all are stopped.
#
#   make up                 # or: ./scripts/dev.sh
#   API_PORT=8001 WEB_PORT=3001 make up
#
# Compatible with macOS's bash 3.2.

set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PY:-$ROOT/.venv/bin/python}"
API_PORT="${API_PORT:-8000}"
WEB_PORT="${WEB_PORT:-3000}"
WORKERS="${WORKERS:-1}"
# Restart workers when backend Python code changes (like the API's --reload). A run in progress is
# cancelled cooperatively and can be retried; finished stages are skipped. WORKER_RELOAD=0 disables it.
WORKER_RELOAD="${WORKER_RELOAD:-1}"

# Load optional settings (DATABASE_URL, DATA_PLATFORM_*, DP_SECRET_* ...).
if [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/.env"
  set +a
fi

if [ -t 1 ]; then
  BOLD=$'\033[1m'; RED=$'\033[31m'; GREEN=$'\033[32m'; DIM=$'\033[2m'; RESET=$'\033[0m'
  COLORS=($'\033[36m' $'\033[35m' $'\033[33m' $'\033[34m')
else
  BOLD=""; RED=""; GREEN=""; DIM=""; RESET=""; COLORS=("" "" "" "")
fi

say()  { printf '%s\n' "${BOLD}[up]${RESET} $*"; }
fail() { printf '%s\n' "${RED}${BOLD}[up]${RESET}${RED} $*${RESET}" >&2; exit 1; }

# ---------------------------------------------------------------- preflight

[ -x "$PY" ] || fail "Python virtualenv not found at $PY — run: make setup"
"$PY" -c "import fastapi, duckdb, osmium" 2>/dev/null || fail "Backend dependencies missing — run: make setup"

export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
[ -s "$NVM_DIR/nvm.sh" ] || fail "nvm not found (expected $NVM_DIR/nvm.sh)"
[ -d "$ROOT/frontend/node_modules" ] || fail "Frontend dependencies missing — run: make setup"

if command -v pg_isready >/dev/null 2>&1; then
  pg_isready -q || fail "PostgreSQL is not accepting connections — start it first (e.g. brew services start postgresql)"
fi

for port in "$API_PORT" "$WEB_PORT"; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    fail "Port $port is already in use: $(lsof -nP -iTCP:"$port" -sTCP:LISTEN | awk 'NR==2 {print $1" (pid "$2")"}'). Stop it or set API_PORT/WEB_PORT."
  fi
done

say "Applying migrations and registering sources…"
(cd "$ROOT/backend" && "$PY" -m data_platform migrate >/dev/null && "$PY" -m data_platform sync >/dev/null) \
  || fail "Migration/sync failed — check DATABASE_URL (currently: ${DATABASE_URL:-dbname=ds_companies})"

# ---------------------------------------------------------------- start services

set -m                       # every background job gets its own process group (clean shutdown of children)
PIDS=()
NAMES=()

start() {                    # start <name> <command...>
  local name="$1"; shift
  local color="${COLORS[$(( ${#PIDS[@]} % ${#COLORS[@]} ))]}"
  local label
  label=$(printf '%-7s' "$name")
  "$@" > >(awk -v p="${color}${label}|${RESET} " '{ print p $0; fflush() }') 2>&1 &
  PIDS+=($!)
  NAMES+=("$name")
}

stopping=0
shutdown() {
  [ "$stopping" = 1 ] && return
  stopping=1
  trap - INT TERM
  echo
  say "Stopping…"
  for pid in "${PIDS[@]}"; do kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null; done
  # Give processes a moment to finish (the worker cancels its current run cooperatively).
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    alive=0
    for pid in "${PIDS[@]}"; do kill -0 "$pid" 2>/dev/null && alive=1; done
    [ "$alive" = 0 ] && break
    sleep 0.5
  done
  for pid in "${PIDS[@]}"; do kill -KILL -- "-$pid" 2>/dev/null; done
  say "Stopped."
}
trap 'shutdown; exit 0' INT TERM

start api bash -c "cd '$ROOT/backend' && exec '$PY' -m uvicorn app.main:app --port $API_PORT --reload"
if [ "$WORKER_RELOAD" = 1 ] && [ -x "$ROOT/.venv/bin/watchfiles" ]; then
  WORKER_CMD="exec '$ROOT/.venv/bin/watchfiles' --filter python --sigint-timeout 30 --target-type command \"'$PY' -m data_platform worker\" data_platform"
else
  WORKER_CMD="exec '$PY' -m data_platform worker"
fi
for i in $(seq 1 "$WORKERS"); do
  start "worker$([ "$WORKERS" -gt 1 ] && echo "$i")" bash -c "cd '$ROOT/backend' && $WORKER_CMD"
done
start web bash -c "source '$NVM_DIR/nvm.sh' >/dev/null && cd '$ROOT/frontend' && nvm use >/dev/null && \
  NEXT_PUBLIC_API_URL=http://localhost:$API_PORT exec npm run dev -- --port $WEB_PORT"

say "Starting API, ${WORKERS} worker(s) and web app — Ctrl+C to stop."
(
  # Announce once both HTTP services answer.
  for _ in $(seq 1 120); do
    # Port checks only, so the probe adds no request noise to the logs.
    if nc -z localhost "$API_PORT" 2>/dev/null && nc -z localhost "$WEB_PORT" 2>/dev/null; then
      printf '\n%s\n' "${GREEN}${BOLD}Ready${RESET}"
      printf '  %-14s %s\n' "Data platform" "http://localhost:$WEB_PORT/data-platform"
      printf '  %-14s %s\n' "v1 dashboard" "http://localhost:$WEB_PORT/"
      printf '  %-14s %s\n' "API docs" "http://localhost:$API_PORT/docs"
      printf '%s\n\n' "${DIM}Run a pipeline: make pipeline-osm-pakistan PBF=~/Downloads/pakistan-261007.osm.pbf (or use the Sources page)${RESET}"
      exit 0
    fi
    sleep 1
  done
) &
READY_PID=$!

# ---------------------------------------------------------------- supervise

while :; do
  for i in "${!PIDS[@]}"; do
    if ! kill -0 "${PIDS[$i]}" 2>/dev/null; then
      wait "${PIDS[$i]}" 2>/dev/null
      code=$?
      kill "$READY_PID" 2>/dev/null
      printf '%s\n' "${RED}${BOLD}[up]${RESET}${RED} ${NAMES[$i]} exited (code $code) — stopping the rest.${RESET}" >&2
      shutdown
      exit 1
    fi
  done
  sleep 1
done
