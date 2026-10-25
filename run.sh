#!/usr/bin/env bash
# netsim launcher: ./run.sh [start|stop|restart|status|build|logs]
# Default (start): builds whatever is missing or outdated, starts the daemon in
# the background and prints how to open the web UI.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
VENV="$ROOT/.venv"
STATE="$ROOT/.run"
PIDFILE="$STATE/netsim.pid"
LOG="$STATE/netsim.log"
PORT="${NETSIM_PORT:-8077}"
HOST="${NETSIM_BIND:-127.0.0.1}"
WEB_OUT="$ROOT/netsim/web/index.html"

say() { printf '\033[1;35m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

newer_than() { # newer_than TARGET PATH...  -> true if any PATH is newer than TARGET (or TARGET missing)
  local target=$1; shift
  [[ -e $target ]] || return 0
  [[ -n $(find "$@" -newer "$target" -print -quit 2>/dev/null) ]]
}

build_python() {
  if [[ ! -x $VENV/bin/python ]] || newer_than "$VENV/.built" pyproject.toml; then
    say "python environment"
    if command -v uv >/dev/null; then
      uv venv -q "$VENV" 2>/dev/null || true
      uv pip install -q -p "$VENV/bin/python" -e '.[dev]'
    else
      python3 -m venv "$VENV"
      "$VENV/bin/pip" install -q -e '.[dev]'
    fi
    touch "$VENV/.built"
  fi
}

build_web() {
  command -v npm >/dev/null || { [[ -e $WEB_OUT ]] && return; die "npm is needed to build the web UI"; }
  if [[ ! -d web/node_modules ]] || newer_than web/node_modules web/package-lock.json; then
    say "web dependencies (npm ci)"
    (cd web && npm ci --silent)
    touch web/node_modules
  fi
  if newer_than "$WEB_OUT" web/src web/index.html web/package.json web/vite.config.ts; then
    say "building web UI"
    (cd web && npm run -s build >/dev/null)
  fi
}

build() {
  build_python
  build_web
}

pid() { [[ -f $PIDFILE ]] && cat "$PIDFILE" 2>/dev/null || true; }

running() { # the daemon runs as root: check /proc instead of signalling it
  local p
  p=$(pid)
  [[ -n $p && -r /proc/$p/cmdline ]] && tr '\0' ' ' </proc/"$p"/cmdline | grep -q "netsim serve"
}

url() {
  local token
  token=$(cat /run/netsim/token 2>/dev/null || true)
  echo "http://127.0.0.1:$PORT/?token=$token"
}

info() {
  cat <<EOF

  Web UI   $(url)
  CLI      export PATH="$VENV/bin:\$PATH"      # then: netsim --help

  Try:
    netsim run -p ideal   -- curl -so /dev/null -w '%{time_total}s\n' https://example.com
    netsim run -p 3g-hspa -- curl -so /dev/null -w '%{time_total}s\n' https://example.com
    netsim up iot-gateway          # then open it in the UI -> Monitor

  More in QUICKSTART.md.  Stop with: ./run.sh stop   Logs: ./run.sh logs
EOF
}

start() {
  build
  if running; then
    say "netsim is already running (pid $(pid))"
    info
    return
  fi
  mkdir -p "$STATE"
  say "starting netsim daemon (needs sudo)"
  sudo -n true 2>/dev/null || sudo -v   # ask for the password only if needed
  # setsid + nohup: the daemon survives this terminal; the pid written is the python process
  sudo setsid nohup sh -c 'echo $$ > "$1"; shift; exec "$@"' sh "$PIDFILE" \
    "$VENV/bin/python" -m netsim serve --host "$HOST" --port "$PORT" --user "$(id -un)" \
    --user-path "$PATH" >"$LOG" 2>&1 </dev/null &
  for _ in $(seq 1 50); do
    if curl -sf -o /dev/null -H "Authorization: Bearer $(cat /run/netsim/token 2>/dev/null)" \
        "http://127.0.0.1:$PORT/api/info" 2>/dev/null; then
      say "netsim is up (pid $(pid), log $LOG)"
      info
      return
    fi
    sleep 0.2
  done
  tail -20 "$LOG" >&2 || true
  die "daemon did not come up, see $LOG"
}

stop() {
  if ! running; then
    say "netsim is not running"
    rm -f "$PIDFILE"
    return
  fi
  say "stopping netsim (labs are torn down)"
  sudo kill -TERM "$(pid)"
  for _ in $(seq 1 75); do
    running || { rm -f "$PIDFILE"; say "stopped"; return; }
    sleep 0.2
  done
  die "netsim did not stop; try: sudo kill -9 $(pid)"
}

status() {
  if running; then
    say "running (pid $(pid))"
    "$VENV/bin/netsim" status || true
    info
  else
    say "not running - start with ./run.sh"
  fi
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  restart) stop; start ;;
  status) status ;;
  build) build; say "build up to date" ;;
  logs) tail -f "$LOG" ;;
  -h|--help|help) sed -n '2,4p' "$0" | sed 's/^# //' ;;
  *) die "unknown command: $1 (start|stop|restart|status|build|logs)" ;;
esac
