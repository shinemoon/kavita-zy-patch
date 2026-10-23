#!/usr/bin/env bash
# 掌阅 ↔ Kavita 补丁: 安装 / 升级 / 卸载
# 幂等。重复跑 ./install.sh 即"重新打补丁"。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$HERE/patch.conf" ] || { echo "缺 patch.conf"; exit 1; }
# shellcheck disable=SC1090
source "$HERE/patch.conf"

BACKUP_DIR="$HERE/backups"
BUILD_DIR="$HERE/build"

die() { echo "ERR: $*" >&2; exit 1; }

# node 不在默认 PATH (走 nvm), 语法闸要用它
NODE_BIN="$(command -v node 2>/dev/null || true)"
if [ -z "$NODE_BIN" ]; then
  for c in /vol2/1001/.nvm/versions/node/*/bin/node "$HOME"/.nvm/versions/node/*/bin/node; do
    [ -x "$c" ] && { NODE_BIN="$c"; break; }
  done
fi

container_updir() { docker inspect "$CONTAINER" --format '{{.GraphDriver.Data.UpperDir}}'; }
wwwroot_dir()      { echo "$(container_updir)/kavita/wwwroot"; }
http_code()        { curl -s -o /dev/null -m 5 -w '%{http_code}' "$KAVITA_URL/"; }

# 镜像换了 / 目录结构变了就找不准 wwwroot。这里扫一遍容器可写层。
locate_wwwroot() {
  local ud; ud="$(container_updir)"
  [ -n "$ud" ] || return 1
  local c
  for c in "$ud/kavita/wwwroot" "$ud/wwwroot" "$ud/app/wwwroot" "$ud/kavita"; do
    [ -f "$c/index.html" ] && { echo "$c"; return 0; }
  done
  c="$(find "$ud" -maxdepth 4 -name index.html 2>/dev/null | head -1)"
  [ -n "$c" ] && { echo "$(dirname "$c")"; return 0; }
  return 1
}

ensure_token() {
  if [ ! -s "$TOKEN_FILE" ]; then
    mkdir -p "$(dirname "$TOKEN_FILE")"
    head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$TOKEN_FILE"
    chmod 600 "$TOKEN_FILE"
    echo "生成新 token -> $TOKEN_FILE"
  fi
}

render_asset() {
  local tok; tok="$(cat "$TOKEN_FILE")"
  mkdir -p "$BUILD_DIR"
  sed -e "s|__ZY_TOKEN__|$tok|g" \
      -e "s|__ZY_PORT__|$API_PORT|g" \
      -e "s|__ZY_WEB_PORT__|$WEB_PORT|g" \
      -e "s|__ZY_VER__|$PATCH_VERSION|g" \
      "$HERE/src/zy-kavita-patch.js.tmpl" > "$BUILD_DIR/zy-kavita-patch.js"
  # 渲染完必须没有残留占位符, 否则前端拿到的是 'http://...:__ZY_WEB_PORT__/'
  if grep -q '__ZY_[A-Z_]*__' "$BUILD_DIR/zy-kavita-patch.js"; then
    echo "ERR: 资产里还有未替换的占位符" >&2
    grep -o '__ZY_[A-Z_]*__' "$BUILD_DIR/zy-kavita-patch.js" | sort -u >&2
    return 1
  fi
  # 渲染完必须语法合法。v0.2.0 就是因为模板末尾漏了 `})();` 整个脚本
  # 没执行, 浏览器报 "Unexpected end of input", 而 install 全程显示成功。
  # 这道闸把那种"装上了但没生效"挡在 install 阶段。
  if ! "$NODE_BIN" --check "$BUILD_DIR/zy-kavita-patch.js" 2>&1; then
    echo "ERR: 渲染出来的 JS 语法不合法, 拒绝安装" >&2
    return 1
  fi
  echo "渲染 -> $BUILD_DIR/zy-kavita-patch.js (console port $WEB_PORT, 语法 OK)"
}

render_index() {
  python3 - "$BACKUP_DIR/index.html.orig" "$MARKER" "$PATCH_VERSION" > "$BUILD_DIR/index.html.patched" <<'PY'
import re, sys
orig, marker, ver = sys.argv[1], sys.argv[2], sys.argv[3]
src = open(orig, encoding="utf-8-sig").read()
# 幂等: 摘掉任何历史注入 (marker 变了也摘干净)
src = re.sub(r"[ \t]*<script[^>]*zy-kavita-patch\.js[^>]*>\s*</script>\s*", "\n", src)
src = re.sub(r"[ \t]*<!--\s*ZY-KAVITA-PATCH.*?-->\s*", "\n", src, flags=re.S)
tag = (f"<!-- {marker} v{ver} -->\n"
       f'<script src="zy-kavita-patch.js?v={ver}" defer></script>\n')
sys.stdout.write(src.rstrip() + "\n" + tag)
PY
  echo "渲染 -> $BUILD_DIR/index.html.patched"
}

do_install() {
  docker inspect "$CONTAINER" >/dev/null 2>&1 || die "找不到容器 $CONTAINER (改 patch.conf)"

  local wr; wr="$(locate_wwwroot)" || die "定位不到容器可写层里的 wwwroot (docker inspect 看 GraphDriver.Data.UpperDir)"
  echo "wwwroot = $wr"

  # 资产推送路径要用容器内视角
  local inner="$CONTAINER_WWWROOT"
  docker exec "$CONTAINER" sh -c "test -d '$wr'" >/dev/null 2>&1 \
    && inner="$(echo "$wr" | sed "s|$(container_updir)||")"

  # 校验: 原版 index.html 得能从容器内拿到
  docker exec "$CONTAINER" sh -c "test -f '${inner}/index.html'" >/dev/null 2>&1 \
    || die "容器内找不到 ${inner}/index.html (CONTAINER_WWWROOT 改 patch.conf)"

  ensure_token
  mkdir -p "$BACKUP_DIR" "$BUILD_DIR"

  if [ ! -f "$BACKUP_DIR/index.html.orig" ]; then
    docker cp "$CONTAINER:${inner}/index.html" "$BACKUP_DIR/index.html.orig"
    echo "已备份原版 index.html"
  fi

  render_asset
  render_index
  docker cp "$BUILD_DIR/zy-kavita-patch.js" "$CONTAINER:${inner}/zy-kavita-patch.js"
  docker cp "$BUILD_DIR/index.html.patched"  "$CONTAINER:${inner}/index.html"

  "$HERE/ctl.sh" restart-api >/dev/null 2>&1 || "$HERE/ctl.sh" start-api || true

  cat <<EOF

已装 v$PATCH_VERSION
  前端   $CONTAINER:${inner}/zy-kavita-patch.js
  注入   index.html 末尾 <script src="zy-kavita-patch.js">
  后端   $API_BIND:$API_PORT   (./ctl.sh status)
  验     curl -s $KAVITA_URL/zy-kavita-patch.js | head -3
  撤     ./uninstall.sh
EOF
}

do_uninstall() {
  docker inspect "$CONTAINER" >/dev/null 2>&1 || die "找不到容器 $CONTAINER"
  [ -f "$BACKUP_DIR/index.html.orig" ] || die "没有原始备份, 拒绝乱改"
  local wr; wr="$(locate_wwwroot)" || die "定位不到 wwwroot"
  local inner; inner="$(echo "$wr" | sed "s|$(container_updir)||")"
  docker cp "$BACKUP_DIR/index.html.orig" "$CONTAINER:${inner}/index.html"
  "$HERE/ctl.sh" stop-api >/dev/null 2>&1 || true
  echo "已卸载 (index.html 还原, 后端停)"
  echo "容器内 zy-kavita-patch.js 留着无害, 下次装会覆盖"
}

case "${1:-install}" in
  install)   do_install ;;
  uninstall) do_uninstall ;;
  render)    ensure_token; render_asset; render_index ;;
  status)
    echo "kavita=$(http_code)"
    curl -s -m 5 -o /dev/null -w 'asset=%{http_code}\n' "$KAVITA_URL/zy-kavita-patch.js"
    "$HERE/ctl.sh" status || true
    ;;
  *) echo "usage: $0 {install|uninstall|render|status}"; exit 2 ;;
esac