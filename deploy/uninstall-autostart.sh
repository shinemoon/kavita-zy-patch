#!/usr/bin/env bash
# 卸自启动 (需要 root)。不删数据, 只停/禁用 unit。
#
#   sudo bash deploy/uninstall-autostart.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="${UNIT_DIR:-/etc/systemd/system}"
UNITS=(kavita-zy-api.service kavita-zy-web.service kavita-zy-heal.service kavita-zy-heal.timer)

if [ "$(id -u)" != "0" ]; then
  echo "需要 root: sudo bash $0" >&2
  exit 2
fi

systemctl stop kavita-zy-heal.timer 2>/dev/null || true
systemctl disable "${UNITS[@]}" >/dev/null 2>&1 || true
systemctl stop kavita-zy-api.service kavita-zy-web.service 2>/dev/null || true

for u in "${UNITS[@]}"; do
  rm -f "$UNIT_DIR/$u"
  echo "   删 $u"
done
systemctl daemon-reload
echo "已卸载自启动 (unit 删掉, 数据保留)"
