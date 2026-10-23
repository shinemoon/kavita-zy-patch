#!/usr/bin/env bash
# 补丁后端 API 生命周期
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/patch.conf"

RUN="$HERE/run"
mkdir -p "$RUN" "$HERE/state/logs"
PID="$RUN/api.pid"

is_up() { [ -s "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; }

start_api() {
  is_up && { echo "已在跑 (pid $(cat "$PID"))"; return 0; }
  nohup python3 "$HERE/src/api_server.py" >> "$HERE/state/logs/api.out" 2>&1 &
  echo $! > "$PID"
  sleep 1.2
  is_up || { echo "启动失败, 看 state/logs/api.out"; tail -20 "$HERE/state/logs/api.out"; return 1; }
  echo "已启动 pid=$(cat "$PID")  http://$API_BIND:$API_PORT"
}

stop_api() {
  if is_up; then
    local p; p="$(cat "$PID")"
    kill "$p" 2>/dev/null || true
    sleep 0.6
    is_up && kill -9 "$p" 2>/dev/null || true
    echo "已停 (pid $p)"
  else
    echo "没在跑"
  fi
  rm -f "$PID"
}

status_api() {
  if is_up; then
    echo "api UP   pid=$(cat "$PID")  http://$API_BIND:$API_PORT"
    curl -s -m 4 -H "X-ZY-Token: $(cat "$TOKEN_FILE" 2>/dev/null || true)" \
      "http://127.0.0.1:$API_PORT/zy/api/health" && echo
  else
    echo "api DOWN"
  fi
}

case "${1:-status}" in
  start) start_api ;;
  stop) stop_api ;;
  restart-api) stop_api; sleep 0.4; start_api ;;
  status) status_api ;;
  logs) tail -n "${2:-40}" "$HERE/state/logs/api.out" ;;
  *) echo "usage: $0 {start|stop|restart-api|status|logs}"; exit 2 ;;
esac