#!/usr/bin/env bash
# 装自启动 (需要 root): 补丁后端 5111 / 控制台 5012 / 自愈 timer
#
#   sudo bash deploy/install-autostart.sh            # 装 + 立即启动
#   sudo bash deploy/install-autostart.sh --no-start # 只装不自启
#
# 幂等: 重复跑只会重写 unit 文件 + daemon-reload + 重启服务。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1090
source "$ROOT/patch.conf"

UNIT_DIR="${UNIT_DIR:-/etc/systemd/system}"
START=1
[ "${1:-}" = "--no-start" ] && START=0

if [ "$(id -u)" != "0" ]; then
  echo "需要 root: sudo bash $0" >&2
  exit 2
fi

UNITS=(kavita-zy-api.service kavita-zy-web.service kavita-zy-heal.service kavita-zy-heal.timer)

echo "== 1/4 停掉手工 nohup 起的进程 (端口让给 systemd)"
# 手工 pid 文件: 补丁后端 run/api.pid, 控制台 state/web.pid
for f in "$ROOT/run/api.pid" "$ROOT/state/web.pid"; do
  [ -s "$f" ] || continue
  pid="$(cat "$f" 2>/dev/null || true)"
  if [ -n "${pid:-}" ] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    sleep 1
    kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null || true
    echo "   停 pid=$pid ($f)"
  fi
  rm -f "$f"
done

echo "== 2/4 写 unit 文件到 $UNIT_DIR"
mkdir -p "$UNIT_DIR"
for u in "${UNITS[@]}"; do
  install -m 644 "$ROOT/deploy/$u" "$UNIT_DIR/$u"
  echo "   $u"
done
systemctl daemon-reload

echo "== 3/4 enable"
systemctl enable "${UNITS[@]}" >/dev/null

echo "== 4/4 启动"
if [ "$START" = "1" ]; then
  systemctl restart kavita-zy-api.service kavita-zy-web.service
  systemctl start kavita-zy-heal.timer
  sleep 2
  systemctl restart kavita-zy-heal.service || true
fi

echo
echo "== 状态"
for u in "${UNITS[@]}"; do
  printf '%-32s %s / %s\n' "$u" \
    "$(systemctl is-enabled "$u" 2>/dev/null || echo -)" \
    "$(systemctl is-active "$u" 2>/dev/null || echo -)"
done
echo
echo "自愈日志: $ROOT/state/logs/heal.log"
echo "关掉自检: touch $ROOT/state/heal.disabled   (或控制台按钮)"
echo "卸载:     sudo bash $ROOT/deploy/uninstall-autostart.sh"
