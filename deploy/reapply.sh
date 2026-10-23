#!/usr/bin/env bash
# Kavita 升级 / 容器重建后重打补丁。
#
# 两种模式:
#   mount  前端文件由宿主目录挂进容器 (compose 的 bind mount)。
#          重建不会丢; 这里只负责"镜像升级后用新 index.html 重建注入版"。
#   copy   旧做法, 补丁在容器可写层。重建即丢, 这里重新 docker cp 打进去。
#
# 全程幂等。heal.sh 每 3 分钟调一次。
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$HERE/patch.conf"

CONTAINER="${CONTAINER:-kavita}"
KAVITA_URL="${KAVITA_URL:-http://127.0.0.1:5000}"
MOUNT_DIR="${WWWROOT_MOUNT_DIR:-$HERE/state/wwwroot}"

say() { echo "[$(date '+%F %T')] $*"; }

echo "== 1/4 检查容器"
if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
  CONTAINER="$(docker ps --format '{{.Names}}' | grep -i kavita | head -1 || true)"
fi
[ -n "$CONTAINER" ] || { echo "找不到 kavita 容器"; exit 1; }
echo "容器: $CONTAINER"

echo "== 2/4 检测模式"
mode=copy
if docker inspect "$CONTAINER" --format '{{range .Mounts}}{{.Destination}}{{"\n"}}{{end}}' 2>/dev/null \
   | grep -qx "$CONTAINER_WWWROOT/index.html"; then
  mode=mount
fi
echo "模式: $mode"

asset_of() { curl -s -o /dev/null -m 5 -w '%{http_code}' "$KAVITA_URL/zy-kavita-patch.js" || echo 000; }
injected() { curl -s -m 5 "$KAVITA_URL/" | grep -q 'zy-kavita-patch.js' && echo yes || echo no; }

echo "== 3/4 检测补丁状态"
code="$(asset_of)"
in_html="$(injected)"
echo "asset=$code index=$in_html"

if [ "$mode" = "mount" ]; then
  # 镜像升级 → index.html 里的 asset 哈希过期 → 用新镜像的 index 重建
  img_md5="$(docker run --rm --entrypoint cat "$(docker inspect -f '{{.Config.Image}}' "$CONTAINER")" \
             "$CONTAINER_WWWROOT/index.html" 2>/dev/null | md5sum | cut -d' ' -f1 || true)"
  cur_md5="$(cat "$MOUNT_DIR/.image-index.md5" 2>/dev/null || true)"
  stale=no
  [ -z "$img_md5" ] || { [ "$img_md5" = "$cur_md5" ] || stale=yes; }
  [ -s "$MOUNT_DIR/index.html" ] || stale=yes
  [ -s "$MOUNT_DIR/zy-kavita-patch.js" ] || stale=yes

  # inode 漂移: 单文件 bind mount 钉的是启动时的 inode。宿主用 install/cp+rename
  # 换过文件 → 容器里还是旧内容, 但 HTTP 一切正常, 只有版本/内容对不上。
  # 这里比对 md5, 不一致就重启容器重新绑定 (restart 会重新解析挂载源)。
  h_md5="$(md5sum "$MOUNT_DIR/index.html" 2>/dev/null | cut -d' ' -f1 || true)"
  c_md5="$(docker exec "$CONTAINER" md5sum "$CONTAINER_WWWROOT/index.html" 2>/dev/null | cut -d' ' -f1 || true)"
  drift=no
  if [ -n "$h_md5" ] && [ -n "$c_md5" ] && [ "$h_md5" != "$c_md5" ]; then
    drift=yes
    say "检测到 inode 漂移 (宿主 $h_md5 != 容器 $c_md5), 重启容器重新绑定挂载"
    docker restart "$CONTAINER" >/dev/null 2>&1 || true
    for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do
      case "$(http_code "$KAVITA_URL/")" in 200|302|304) break ;; esac
      sleep 5
    done
    code="$(asset_of)"; in_html="$(injected)"
    say "重启后 asset=$code index=$in_html"
  fi

  if [ "$stale" = no ] && [ "$drift" = no ] && [ "$code" = "200" ] && [ "$in_html" = yes ]; then
    echo "补丁健康 (挂载模式, index 与当前镜像一致), 无需重打"
    exit 0
  fi

  say "需要重建注入版 (stale=$stale asset=$code index=$in_html)"
  if ! bash "$HERE/install.sh" publish; then
    say "ERR publish 失败"
    exit 1
  fi
  bash "$HERE/ctl.sh" start >/dev/null 2>&1 || true
  # 容器若把缺失的挂载点当目录创建了, 这里会一直是 404 —— 提示重建
  code="$(asset_of)"; in_html="$(injected)"
  echo "重建后 asset=$code index=$in_html"
  if [ "$code" != "200" ] || [ "$in_html" != yes ]; then
    say "ERR 挂载生效但内容不可用。多半是重建时文件不存在, docker 把挂载点建成了目录。"
    say "     修法: docker compose -f /vol1/1001/APPS/kavita/docker-compose.yml up -d --force-recreate"
    exit 1
  fi
  say "OK 补丁已重建 (挂载模式)"
  exit 0
fi

if [ "$code" = "200" ] && [ "$in_html" = yes ]; then
  echo "补丁还在 (asset=200 index=注入), 无需重打"
  echo "强制重打: ./install.sh install"
  exit 0
fi
echo "补丁已失效 (asset=$code index=$in_html), 重新安装"

echo "== 4/4 重装"
bash "$HERE/install.sh" install
bash "$HERE/ctl.sh" start || true
