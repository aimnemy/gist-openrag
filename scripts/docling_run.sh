#!/usr/bin/env bash
# Host-side docling-serve controller for aimlab_openrag.
#
# OpenRAG's backend runs in Docker and calls docling at
# `host.docker.internal:5001`, so docling-serve must run on the HOST bound
# to 0.0.0.0. Launch command mirrors `src/tui/managers/docling_manager.py`
# so behavior matches the in-app "native docling" button.
#
# Uses uvx → no permanent install; first run pulls ~2 GB of models and
# wheels; later runs hit uv's cache.
#
# Usage:
#   ./scripts/docling_run.sh start      # start in background, wait for /health
#   ./scripts/docling_run.sh stop
#   ./scripts/docling_run.sh status
#   ./scripts/docling_run.sh logs       # tail last 60 log lines
#   DOCLING_WORKERS=2 ./scripts/docling_run.sh start
#
# Env overrides: DOCLING_PORT, DOCLING_HOST, DOCLING_WORKERS, DOCLING_STARTUP_TIMEOUT

set -euo pipefail

PORT="${DOCLING_PORT:-5001}"
HOST="${DOCLING_HOST:-0.0.0.0}"
WORKERS="${DOCLING_WORKERS:-1}"
STARTUP_TIMEOUT="${DOCLING_STARTUP_TIMEOUT:-600}"

LOG_DIR="$HOME/.openrag/tui"
LOG_FILE="$LOG_DIR/docling-serve.log"
PID_FILE="$LOG_DIR/docling-serve.pid"

mkdir -p "$LOG_DIR"

log() { printf '\033[1;34m[docling]\033[0m %s\n' "$*"; }
warn(){ printf '\033[1;33m[docling]\033[0m %s\n' "$*" >&2; }

is_up() {
  curl -sfm2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1
}

pid_alive() {
  [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

start_cmd() {
  if pid_alive; then
    log "already running (PID $(cat "$PID_FILE"))"
    return 0
  fi
  if is_up; then
    log "already reachable on :$PORT (no PID file — not started by this script)"
    return 0
  fi

  local extras
  case "$(uname -s)" in
    Darwin) extras="ocrmac,easyocr,rapidocr,vlm" ;;
    *)      extras="easyocr,rapidocr,vlm" ;;
  esac

  log "starting docling-serve on $HOST:$PORT (workers=$WORKERS)"
  log "logs  : $LOG_FILE"
  log "(first run pulls ~2 GB; subsequent starts are fast — uv cache)"

  # nohup + start_new_session equivalent: setsid where available, else just & disown
  if command -v setsid >/dev/null 2>&1; then
    setsid nohup uvx \
      --from "docling-serve[ui]==1.15.1" \
      --with onnxruntime \
      --with easyocr \
      --with "docling[$extras]" \
      --with "docling-core==2.71.0" \
      docling-serve run --host "$HOST" --port "$PORT" --workers "$WORKERS" \
      >"$LOG_FILE" 2>&1 &
  else
    nohup uvx \
      --from "docling-serve[ui]==1.15.1" \
      --with onnxruntime \
      --with easyocr \
      --with "docling[$extras]" \
      --with "docling-core==2.71.0" \
      docling-serve run --host "$HOST" --port "$PORT" --workers "$WORKERS" \
      >"$LOG_FILE" 2>&1 &
  fi
  echo $! >"$PID_FILE"
  log "PID $(cat "$PID_FILE"); waiting up to ${STARTUP_TIMEOUT}s for /health"

  local elapsed=0 step=5
  while (( elapsed < STARTUP_TIMEOUT )); do
    if is_up; then
      log "ready (after ${elapsed}s) → http://127.0.0.1:$PORT"
      log "OpenRAG backend reaches it at http://host.docker.internal:$PORT"
      return 0
    fi
    if ! pid_alive; then
      warn "process died during startup — last 30 log lines:"
      tail -n 30 "$LOG_FILE" >&2 || true
      return 1
    fi
    sleep "$step"
    elapsed=$((elapsed + step))
    printf '\r\033[1;34m[docling]\033[0m waiting... %ss elapsed' "$elapsed"
  done
  echo
  warn "timed out after ${STARTUP_TIMEOUT}s — check $LOG_FILE"
  return 1
}

stop_cmd() {
  if pid_alive; then
    local pid; pid="$(cat "$PID_FILE")"
    log "stopping PID $pid"
    kill -TERM "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
    sleep 3
    kill -KILL "$pid" 2>/dev/null || true
    rm -f "$PID_FILE"
    log "stopped"
  else
    log "not running"
    rm -f "$PID_FILE"
  fi
}

status_cmd() {
  if pid_alive; then
    log "PID $(cat "$PID_FILE") — $(is_up && echo 'healthy' || echo 'process up, /health not responding yet')"
  else
    log "stopped"
  fi
  is_up && curl -sm2 "http://127.0.0.1:$PORT/health" || true
  echo
}

logs_cmd() {
  [[ -f "$LOG_FILE" ]] || { warn "no log file yet: $LOG_FILE"; return 1; }
  tail -n 60 "$LOG_FILE"
}

case "${1:-start}" in
  start)  start_cmd ;;
  stop)   stop_cmd ;;
  status) status_cmd ;;
  logs)   logs_cmd ;;
  restart) stop_cmd; start_cmd ;;
  *) echo "usage: $0 {start|stop|status|restart|logs}" >&2; exit 2 ;;
esac
