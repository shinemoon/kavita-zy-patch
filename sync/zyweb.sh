#!/bin/sh
# 掌阅桥接 Web 控制脚本
#   ./zyweb.sh start | stop | status | log
set -e

DIR="/vol2/1001/zhangyue-bridge/sync"
PIDF="/vol2/1001/zhangyue-bridge/state/web.pid"
LOGF="/vol2/1001/zhangyue-bridge/state/logs/web.out"
PORT="${ZY_WEB_PORT:-5012}"

mkdir -p "$(dirname "$PIDF")" "$(dirname "$LOGF")"

alive() {
  [ -f "$PIDF" ] || return 1
  kill -0 "$(cat "$PIDF")" 2>/dev/null
}

case "$1" in
  start)
    if alive; then echo "already running pid=$(cat "$PIDF")"; exit 0; fi
    cd "$DIR"
    nohup python3 web.py >>"$LOGF" 2>&1 &
    echo $! > "$PIDF"
    sleep 1.5
    if alive; then echo "started pid=$(cat "$PIDF") port=$PORT"; else
      echo "failed to start; see $LOGF"; exit 1; fi
    ;;
  stop)
    if alive; then kill "$(cat "$PIDF")" 2>/dev/null || true; sleep 1; fi
    rm -f "$PIDF"
    echo stopped
    ;;
  restart) sh "$DIR/zyweb.sh" stop; sh "$DIR/zyweb.sh" start ;;
  status)
    if alive; then echo "running pid=$(cat "$PIDF") port=$PORT"; else echo "not running"; fi
    curl -s --max-time 3 -H "X-ZY-Token: $(cat "$TOKEN_FILE" 2>/dev/null || true)" "http://127.0.0.1:$PORT/healthz" || true; echo
    ;;
  log) tail -n "${2:-40}" "$LOGF" ;;
  *) echo "usage: $0 {start|stop|restart|status|log}"; exit 1 ;;
esac