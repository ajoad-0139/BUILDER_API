#!/usr/bin/env bash
# ============================================================================
# run.sh — one-command launcher for BUILDER_API (Django + PostgreSQL + Redis)
#
#   1. Creates .env (from .env.example) and a Python virtualenv if missing
#   2. Starts PostgreSQL and Redis in Docker
#   3. Applies migrations and starts the Django dev server
#   4. (optional, --test) opens ANOTHER terminal and runs pytest with coverage
#
# Usage:  ./run.sh [options]      (see ./run.sh --help)
# ============================================================================
set -Eeuo pipefail

# ── Config ───────────────────────────────────────────────────────────────────
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
RUN_DIR="$PROJECT_DIR/.run"
HOST="127.0.0.1"
PORT="8000"
MIN_PY_MAJOR=3
MIN_PY_MINOR=12          # Django 6.x requires Python 3.12+

RUN_TESTS=false
INSTALL_DEPS=true
STOP_ONLY=false
DOWN_ON_EXIT=false
CREATE_SUPERUSER=false

# ── Pretty output ────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  C_RST=$'\033[0m'; C_RED=$'\033[31m'; C_GRN=$'\033[32m'; C_YLW=$'\033[33m'; C_BLU=$'\033[34m'
else
  C_RST=""; C_RED=""; C_GRN=""; C_YLW=""; C_BLU=""
fi
info() { echo "${C_BLU}[run]${C_RST} $*"; }
ok()   { echo "${C_GRN}[ ok]${C_RST} $*"; }
warn() { echo "${C_YLW}[warn]${C_RST} $*"; }
die()  { echo "${C_RED}[fail]${C_RST} $*" >&2; exit 1; }

usage() {
  cat <<USAGE
Usage: ./run.sh [options]

Options:
  -t, --test            After the app is up, run the test suite WITH COVERAGE
                        in a separate terminal window (falls back to the
                        background of this terminal if none can be opened)
  -p, --port PORT       Port for the Django dev server (default: ${PORT})
      --no-install      Skip pip install (faster restarts)
      --superuser       Interactively create a Django superuser before starting
      --down            Stop the Docker containers when the app exits
      --stop            Stop the Docker containers and exit
  -h, --help            Show this help

Examples:
  ./run.sh                  # DB + Redis + app
  ./run.sh --test           # DB + Redis + app + tests with coverage
  ./run.sh -t -p 9000 --down
  ./run.sh --stop
USAGE
}

# ── Args ─────────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    -t|--test)     RUN_TESTS=true ;;
    -p|--port)     [[ $# -ge 2 ]] || die "--port needs a value"; PORT="$2"; shift ;;
    --no-install)  INSTALL_DEPS=false ;;
    --superuser)   CREATE_SUPERUSER=true ;;
    --down)        DOWN_ON_EXIT=true ;;
    --stop)        STOP_ONLY=true ;;
    -h|--help)     usage; exit 0 ;;
    *)             usage; die "Unknown option: $1" ;;
  esac
  shift
done

cd "$PROJECT_DIR"
mkdir -p "$RUN_DIR"

# ── Docker compose wrapper (v2 plugin or legacy v1 binary) ───────────────────
compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose "$@"
  else
    docker-compose "$@"
  fi
}

check_docker() {
  command -v docker >/dev/null 2>&1 || die "Docker is not installed. Install it from https://docs.docker.com/get-docker/"
  docker info >/dev/null 2>&1 || die "Docker daemon is not running. Start Docker and retry."
  if ! docker compose version >/dev/null 2>&1 && ! command -v docker-compose >/dev/null 2>&1; then
    die "Docker Compose not found. Install the Compose plugin."
  fi
}

if $STOP_ONLY; then
  check_docker
  info "Stopping Docker containers…"
  compose stop db redis
  ok "Stopped."
  exit 0
fi

# ── Prerequisites ────────────────────────────────────────────────────────────
find_python() {
  for c in python3.13 python3.12 python3 python; do
    if command -v "$c" >/dev/null 2>&1 &&
       "$c" -c "import sys; sys.exit(0 if sys.version_info >= ($MIN_PY_MAJOR, $MIN_PY_MINOR) else 1)" 2>/dev/null; then
      echo "$c"; return 0
    fi
  done
  return 1
}

check_docker
PYTHON_BIN="$(find_python)" || die "Python ${MIN_PY_MAJOR}.${MIN_PY_MINOR}+ is required (Django 6.x)."

# ── .env ─────────────────────────────────────────────────────────────────────
if [[ ! -f .env ]]; then
  [[ -f .env.example ]] || die ".env.example is missing; cannot create .env"
  cp .env.example .env
  ok "Created .env from .env.example (edit it to change credentials)"
fi

# ── Virtualenv + dependencies ────────────────────────────────────────────────
if [[ ! -d "$VENV_DIR" ]]; then
  info "Creating virtualenv ($PYTHON_BIN)…"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1091
if [[ -f "$VENV_DIR/bin/activate" ]]; then source "$VENV_DIR/bin/activate"; else source "$VENV_DIR/Scripts/activate"; fi

if $INSTALL_DEPS; then
  REQ_HASH="$( (sha256sum requirements.txt 2>/dev/null || shasum -a 256 requirements.txt) | cut -d' ' -f1)"
  if [[ "$(cat "$RUN_DIR/req.hash" 2>/dev/null || true)" != "$REQ_HASH" ]]; then
    info "Installing dependencies…"
    python -m pip install --quiet --upgrade pip
    python -m pip install --quiet -r requirements.txt
    echo "$REQ_HASH" > "$RUN_DIR/req.hash"
    ok "Dependencies installed"
  else
    ok "Dependencies up to date"
  fi
fi

# ── Docker services: PostgreSQL + Redis ──────────────────────────────────────
info "Starting PostgreSQL and Redis containers…"
compose up -d db redis

wait_healthy() {  # $1 = container name, $2 = label
  local name="$1" label="$2" status
  for _ in $(seq 1 60); do
    status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$name" 2>/dev/null || echo missing)"
    if [[ "$status" == "healthy" || "$status" == "running" ]]; then ok "$label is ready"; return 0; fi
    sleep 1
  done
  docker logs --tail 20 "$name" || true
  die "$label did not become healthy in time"
}
wait_healthy presto_db "PostgreSQL"
wait_healthy presto_redis "Redis"

# ── Background-job cleanup ───────────────────────────────────────────────────
TEST_PID=""
cleanup() {
  local code=$?
  trap - EXIT INT TERM
  if [[ -n "$TEST_PID" ]] && kill -0 "$TEST_PID" 2>/dev/null; then kill "$TEST_PID" 2>/dev/null || true; fi
  if $DOWN_ON_EXIT; then
    info "Stopping Docker containers…"
    compose stop db redis >/dev/null 2>&1 || true
  fi
  exit "$code"
}
trap cleanup EXIT INT TERM

# ── Migrations (+ optional superuser) ────────────────────────────────────────
info "Applying migrations…"
python manage.py migrate --noinput
ok "Database is up to date"

if $CREATE_SUPERUSER; then
  info "Creating superuser…"
  python manage.py createsuperuser
fi

# ── Tests (optional, separate terminal) ──────────────────────────────────────
# Runs after the app answers HTTP, so the API is live while tests execute.
launch_tests() {
  local script="$RUN_DIR/run_tests.sh"
  cat > "$script" <<TESTSCRIPT
#!/usr/bin/env bash
cd "$PROJECT_DIR"
if [[ -f "$VENV_DIR/bin/activate" ]]; then source "$VENV_DIR/bin/activate"; else source "$VENV_DIR/Scripts/activate"; fi

echo "Waiting for the app on http://$HOST:$PORT …"
for _ in \$(seq 1 60); do
  if curl -s -o /dev/null "http://$HOST:$PORT/api/v1/" 2>/dev/null; then break; fi
  sleep 1
done

echo "Running tests with coverage…"
python -m pytest --cov=sites --cov=config --cov-report=term-missing --cov-report=html
status=\$?
echo
if [[ \$status -eq 0 ]]; then echo "✔ Tests passed. HTML report: $PROJECT_DIR/htmlcov/index.html"; else echo "✘ Tests failed (exit \$status)"; fi
echo "Press Enter to close this window."
read -r _
exit \$status
TESTSCRIPT
  chmod +x "$script"

  info "Launching tests with coverage in a new terminal…"
  if [[ "$(uname -s)" == "Darwin" ]] && command -v osascript >/dev/null 2>&1; then
    chmod +x "$script"; open -a Terminal "$script" && return 0
  elif command -v gnome-terminal >/dev/null 2>&1; then
    gnome-terminal -- bash "$script" && return 0
  elif command -v konsole >/dev/null 2>&1; then
    konsole -e bash "$script" & return 0
  elif command -v x-terminal-emulator >/dev/null 2>&1; then
    x-terminal-emulator -e bash "$script" & return 0
  elif command -v xterm >/dev/null 2>&1; then
    xterm -e bash "$script" & return 0
  fi

  warn "No terminal emulator found — running tests in the background of this terminal."
  bash "$script" </dev/null &
  TEST_PID=$!
}

if $RUN_TESTS; then
  launch_tests
fi

# ── Start the app (foreground) ───────────────────────────────────────────────
ok "Starting Django on http://$HOST:$PORT  (Ctrl+C to stop)"
python manage.py runserver "$HOST:$PORT"
