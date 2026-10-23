#!/usr/bin/env bash
# Kavita 升级 / 容器重建后重打补丁。
#
# 为什么需要: 前端补丁打进容器 overlay 的可写层 (wwwroot)。docker 换镜像
# (pull latest + recreate) 之后 overlay 上层被清掉, 补丁就没了。
# 这个脚本重新定位 wwwroot 并重打, 全程幂等。
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$HERE/patch.conf"

CONTAINER="${CONTAINER:-kavita}"
KAVITA_URL="${KAVITA_URL:-http://127.0.0.1:5000}"

echo "== 1/3 检查容器"
if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
  CONTAINER="$(docker ps --format '{{.Names}}' | grep -i kavita | head -1 || true)"
fi
[ -n "$CONTAINER" ] || { echo "找不到 kavita 容器"; exit 1; }
echo "容器: $CONTAINER"

echo "== 2/3 检测补丁是否还在"
code="$(curl -s -o /dev/null -m 5 -w '%{http_code}' "$KAVITA_URL/zy-kavita-patch.js" || echo 000)"
in_html=no
if curl -s -m 5 "$KAVITA_URL/" | grep -q 'zy-kavita-patch.js'; then
  in_html=yes
fi
if [ "$code" = "200" ] && [ "$in_html" = yes ]; then
  echo "补丁还在 (asset=200 index=注入), 无需重打"
  echo "强制重打: ./install.sh install"
  exit 0
fi
echo "补丁已失效 (asset=$code index=$in_html), 重新安装"

echo "== 3/3 重装"
bash "$HERE/install.sh" install
bash "$HERE/ctl.sh" start || true