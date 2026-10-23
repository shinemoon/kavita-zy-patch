#!/usr/bin/env bash
# 补丁后端 API 生命周期
#
# 两种跑法共存时要分清所有权:
#   - systemd (kavita-zy-api.service, root 装的) —— 首选, 挂了自动重启
#   - ctl.sh start (手工 nohup, 免 root 时用) —— 写 run/api.pid
# 所以这里判断"活着"以端口 + /zy/api/health 为准, pid 文件只用于"我起的那份"。
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/patch.conf"

RUN="$HERE/run"
mkdir -p "$RUN" "$HERE/state/logs"
PID="$RUN/api.pid"
UNIT="kavita-zy-api"

# /zy/api/health 要 token (无 token 返 403), 所以带 token 判活
health_ok() {
  local code
  code="$(curl -s -m 3 -o /dev/null -w '%{http_code}' \
    -H "X-ZY-Token: $(cat "$TOKEN_FILE" 2>/dev/null || true)" \
    "http://127.0.0.1:$API_PORT/zy/api/health" 2>/dev/null || echo 000)"
  [ "$code" = "200" ]
}
unit_active() { systemctl is-active --quiet "$UNIT" 2>/dev/null; }
pid_alive()   { [ -s "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; }
who() {
  if unit_active; then echo "systemd:$UNIT"
  elif pid_alive; then echo "nohup:pid=$(cat "$PID")"
  else echo "unknown"
  fi
}

start_api() {
  if health_ok; then
    echo "已在跑 ($(who))  http://0.0.0.0:$API_PORT"
    return 0
  fi
  if unit_active; then
    echo "unit $UNIT 是 active 但端口无响应, 尝试重启 unit"
    if systemctl restart "$UNIT" 2>/dev/null; then
      sleep 1; health_ok && { echo "已由 systemd 重启"; return 0; }
    fi
    echo "重启 unit 需要 root: sudo systemctl restart $UNIT"
    return 1
  fi
  nohup python3 "$HERE/src/api_server.py" >> "$HERE/state/logs/api.out" 2>&1 &
  echo $! > "$PID"
  sleep 1.2
  if health_ok; then
    echo "已启动 pid=$(cat "$PID")  http://$API_BIND:$API_PORT"
    return 0
  fi
  echo "启动失败, 看 state/logs/api.out"
  tail -20 "$HERE/state/logs/api.out" || true
  return 1
}

stop_api() {
  if unit_active; then
    echo "unit $UNIT 在跑, 不在这里停 (sudo systemctl stop $UNIT)"
    return 0
  fi
  if pid_alive; then
    local p; p="$(cat "$PID")"
    kill "$p" 2>/dev/null || true
    sleep 0.6
    pid_alive && kill -9 "$p" 2>/dev/null || true
    echo "已停 (pid $p)"
  elif health_ok; then
    echo "端口上有进程但不属于本脚本 (可能是别人的), 不动"
  else
    echo "没在跑"
  fi
  rm -f "$PID"
}

status_api() {
  if health_ok; then
    echo "api UP   ($(who))  http://0.0.0.0:$API_PORT"
    curl -s -m 4 -H "X-ZY-Token: $(cat "$TOKEN_FILE" 2>/dev/null || true)" \
      "http://127.0.0.1:$API_PORT/zy/api/health" && echo
  else
    echo "api DOWN (port $API_PORT 无响应)"
    return 1
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
