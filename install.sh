#!/usr/bin/env bash
# 掌阅 ↔ Kavita 补丁: 安装 / 升级 / 卸载
# 幂等。重复跑 ./install.sh 即"重新打补丁"。
#
# 两种落地方式:
#   A. 挂载模式 (推荐, patch.conf 的 WWWROOT_MOUNT_DIR 被挂进容器)
#      宿主 state/wwwroot/{index.html,zy-kavita-patch.js} → 容器 wwwroot, 只读
#      容器重建 / 换镜像都不丢; 升级镜像时重跑 install (或 heal 自动) 会用新镜像的
#      index.html 重建, 不会残留旧 asset 哈希。
#   B. 拷贝模式 (旧做法)
#      docker cp 打进容器可写层。容器一重建就丢, 靠 heal 每 3 分钟重打。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$HERE/patch.conf" ] || { echo "缺 patch.conf"; exit 1; }
# shellcheck disable=SC1090
source "$HERE/patch.conf"

BACKUP_DIR="$HERE/backups"
BUILD_DIR="$HERE/build"
WWWROOT_MOUNT_DIR="${WWWROOT_MOUNT_DIR:-$HERE/state/wwwroot}"

die() { echo "ERR: $*" >&2; exit 1; }

# node 不在默认 PATH (走 nvm), 语法闸要用它
# 注: cron / systemd 环境可能没有 HOME, set -u 下直接展开会 unbound
HOME_DIR="${HOME:-}"
if [ -z "$HOME_DIR" ]; then HOME_DIR="$(getent passwd "$(id -u)" | cut -d: -f6)"; fi
HOME_DIR="${HOME_DIR:-/root}"
NODE_BIN="$(command -v node 2>/dev/null || true)"
if [ -z "$NODE_BIN" ]; then
  for c in /vol2/1001/.nvm/versions/node/*/bin/node "$HOME_DIR"/.nvm/versions/node/*/bin/node; do
    [ -x "$c" ] && { NODE_BIN="$c"; break; }
  done
fi

container_updir() { docker inspect "$CONTAINER" --format '{{.GraphDriver.Data.UpperDir}}'; }
container_image() { docker inspect "$CONTAINER" --format '{{.Config.Image}}'; }
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

# 容器里 wwwroot 有没有被宿主目录盖住 (compose 的 bind mount)
mount_mode() {
  local dests
  dests="$(docker inspect "$CONTAINER" \
           --format '{{range .Mounts}}{{.Destination}}{{"\n"}}{{end}}' 2>/dev/null || true)"
  [ -z "$dests" ] && return 1
  printf '%s\n' "$dests" | grep -qx "$CONTAINER_WWWROOT/index.html" && return 0
  return 1
}

# 直接从镜像读原版 index.html —— 挂载模式下容器里的那份已被盖住, 读不到原始的
image_index() {
  local img; img="$(container_image)"
  [ -n "$img" ] || return 1
  docker run --rm --entrypoint cat "$img" "$CONTAINER_WWWROOT/index.html" 2>/dev/null
}

# 镜像 wwwroot 里有哪些文件 (用来校验 index.html 引用的 asset 在当前镜像里存在)
image_wwwroot_list() {
  local img; img="$(container_image)"
  [ -n "$img" ] || return 1
  docker run --rm --entrypoint ls "$img" "$CONTAINER_WWWROOT" 2>/dev/null
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

# $1 = 原版 index.html 路径
render_index() {
  local orig="${1:-$BACKUP_DIR/index.html.orig}"
  [ -s "$orig" ] || die "缺原版 index.html ($orig)"
  python3 - "$orig" "$MARKER" "$PATCH_VERSION" > "$BUILD_DIR/index.html.patched" <<'PY'
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

# index.html 里引用到的本地 asset 必须存在于当前镜像 (防镜像升级后残留旧哈希)
verify_index_assets() {
  local idx="${1:-$BUILD_DIR/index.html.patched}"
  local listing; listing="$(image_wwwroot_list)" || return 0   # 读不到就跳过
  [ -n "$listing" ] || return 0
  local bad=0 ref name top
  while IFS= read -r ref; do
    name="$(printf '%s' "$ref" | sed -e 's/[?#].*$//' -e 's#^\./##')"
    case "$name" in
      ""|/*|http*|data:*|\#*) continue ;;
      zy-kavita-patch.js) continue ;;          # 我们自己注入的, 不在镜像里
      # assets/ 子目录不逐个比 (ls 只看顶层), 只比顶层第一段存在
      */*) top="${name%%/*}" ;;
      *)   top="$name" ;;
    esac
    printf '%s\n' "$listing" | grep -qxF -- "$top" || { echo "缺 asset: $name" >&2; bad=1; }
  done < <(grep -oE '(src|href)="[^"]+"' "$idx" | sed -E 's/^(src|href)="//; s/"$//')
  [ "$bad" = 0 ] || return 1
}

# 把渲染产物落到挂载目录 (宿主侧)。挂载模式下容器立刻能看到。
publish_host() {
  ensure_token
  mkdir -p "$WWWROOT_MOUNT_DIR" "$BACKUP_DIR" "$BUILD_DIR"

  # 原版 index.html: 优先取当前镜像里的 (镜像升级后自动跟上), 取不到才用旧备份
  local orig="$BACKUP_DIR/index.html.orig"
  local fresh="$BACKUP_DIR/index.html.from-image"
  if image_index > "$fresh" 2>/dev/null && [ -s "$fresh" ]; then
    mv "$fresh" "$orig"
    echo "已从当前镜像提取原版 index.html"
  else
    rm -f "$fresh" 2>/dev/null || true
    echo "读不到镜像里的 index.html, 用备份 ($orig)"
  fi
  [ -s "$orig" ] || die "没有原版 index.html, 无法生成注入版"

  render_asset
  render_index "$orig"
  verify_index_assets || die "注入版 index.html 引用了当前镜像里不存在的 asset (镜像升级了? 先 docker compose pull 再重跑)"

  # 关键: 单文件 bind mount 钉的是 inode。install/cp+rename 会换 inode,
  # 容器里看到的还是旧文件 (补丁"更新了但没生效"的经典坑)。
  # 必须先写同 inode: cat > 目标 (O_TRUNC), 不行再退回 install 并提示需重建容器。
  put_in_place() {
    local src="$1" dst="$2"
    if [ -e "$dst" ]; then
      if ! cat "$src" > "$dst" 2>/dev/null; then
        install -m 0644 "$src" "$dst" || return 1
        echo "WARN $dst 换了 inode —— 容器可能还挂着旧文件, 需要 docker compose up -d --force-recreate"
      fi
    else
      install -m 0644 "$src" "$dst" || return 1
    fi
    chmod 0644 "$dst" 2>/dev/null || true
  }

  put_in_place "$BUILD_DIR/zy-kavita-patch.js" "$WWWROOT_MOUNT_DIR/zy-kavita-patch.js" \
    || die "写 $WWWROOT_MOUNT_DIR/zy-kavita-patch.js 失败"
  put_in_place "$BUILD_DIR/index.html.patched" "$WWWROOT_MOUNT_DIR/index.html" \
    || die "写 $WWWROOT_MOUNT_DIR/index.html 失败"
  md5sum "$orig" | awk '{print $1}' > "$WWWROOT_MOUNT_DIR/.image-index.md5"
  echo "已发布到挂载目录 $WWWROOT_MOUNT_DIR"
  echo "  index.html         $(wc -c < "$WWWROOT_MOUNT_DIR/index.html") bytes"
  echo "  zy-kavita-patch.js $(wc -c < "$WWWROOT_MOUNT_DIR/zy-kavita-patch.js") bytes"
}

do_install() {
  docker inspect "$CONTAINER" >/dev/null 2>&1 || die "找不到容器 $CONTAINER (改 patch.conf)"

  ensure_token
  mkdir -p "$BACKUP_DIR" "$BUILD_DIR"

  if mount_mode; then
    echo "模式: 挂载 (wwwroot 由 $WWWROOT_MOUNT_DIR 提供)"
    publish_host
    bash "$HERE/ctl.sh" restart-api >/dev/null 2>&1 || bash "$HERE/ctl.sh" start-api || true
    cat <<EOF

已装 v$PATCH_VERSION (挂载模式)
  前端   $WWWROOT_MOUNT_DIR/zy-kavita-patch.js -> $CONTAINER:$CONTAINER_WWWROOT/
  注入   index.html 末尾 <script src="zy-kavita-patch.js">
  后端   $API_BIND:$API_PORT   (bash ./ctl.sh status)
  验     curl -s $KAVITA_URL/zy-kavita-patch.js | head -3
  撤     ./install.sh uninstall
EOF
    return 0
  fi

  echo "模式: 拷贝 (docker cp 进容器可写层, 容器重建后会丢)"
  local wr; wr="$(locate_wwwroot)" || die "定位不到容器可写层里的 wwwroot (docker inspect 看 GraphDriver.Data.UpperDir)"
  echo "wwwroot = $wr"

  # 资产推送路径要用容器内视角
  local inner="$CONTAINER_WWWROOT"
  docker exec "$CONTAINER" sh -c "test -d '$wr'" >/dev/null 2>&1 \
    && inner="$(echo "$wr" | sed "s|$(container_updir)||")"

  # 校验: 原版 index.html 得能从容器内拿到
  docker exec "$CONTAINER" sh -c "test -f '${inner}/index.html'" >/dev/null 2>&1 \
    || die "容器内找不到 ${inner}/index.html (CONTAINER_WWWROOT 改 patch.conf)"

  if [ ! -f "$BACKUP_DIR/index.html.orig" ]; then
    docker cp "$CONTAINER:${inner}/index.html" "$BACKUP_DIR/index.html.orig"
    echo "已备份原版 index.html"
  fi

  render_asset
  render_index
  docker cp "$BUILD_DIR/zy-kavita-patch.js" "$CONTAINER:${inner}/zy-kavita-patch.js"
  docker cp "$BUILD_DIR/index.html.patched"  "$CONTAINER:${inner}/index.html"

  bash "$HERE/ctl.sh" restart-api >/dev/null 2>&1 || bash "$HERE/ctl.sh" start-api || true

  cat <<EOF

已装 v$PATCH_VERSION
  前端   $CONTAINER:${inner}/zy-kavita-patch.js
  注入   index.html 末尾 <script src="zy-kavita-patch.js">
  后端   $API_BIND:$API_PORT   (bash ./ctl.sh status)
  验     curl -s $KAVITA_URL/zy-kavita-patch.js | head -3
  撤     ./install.sh uninstall
EOF
}

do_uninstall() {
  docker inspect "$CONTAINER" >/dev/null 2>&1 || die "找不到容器 $CONTAINER"
  if mount_mode; then
    cat <<EOF
当前是挂载模式 —— 容器里的 index.html 是只读挂载, 没法从容器侧还原。
请二选一:
  1) 从 docker-compose.yml 删掉这两行, 再 docker compose up -d 重建:
       $WWWROOT_MOUNT_DIR/index.html:$CONTAINER_WWWROOT/index.html:ro
       $WWWROOT_MOUNT_DIR/zy-kavita-patch.js:$CONTAINER_WWWROOT/zy-kavita-patch.js:ro
  2) 只关后端不动前端: bash ./ctl.sh stop
EOF
    bash "$HERE/ctl.sh" stop-api >/dev/null 2>&1 || true
    return 1
  fi
  [ -f "$BACKUP_DIR/index.html.orig" ] || die "没有原始备份, 拒绝乱改"
  local wr; wr="$(locate_wwwroot)" || die "定位不到 wwwroot"
  local inner; inner="$(echo "$wr" | sed "s|$(container_updir)||")"
  docker cp "$BACKUP_DIR/index.html.orig" "$CONTAINER:${inner}/index.html"
  bash "$HERE/ctl.sh" stop-api >/dev/null 2>&1 || true
  echo "已卸载 (index.html 还原, 后端停)"
  echo "容器内 zy-kavita-patch.js 留着无害, 下次装会覆盖"
}

case "${1:-install}" in
  install)   do_install ;;
  publish)   ensure_token; publish_host ;;
  uninstall) do_uninstall ;;
  render)    ensure_token; render_asset; render_index ;;
  status)
    echo "kavita=$(http_code)"
    curl -s -m 5 -o /dev/null -w 'asset=%{http_code}\n' "$KAVITA_URL/zy-kavita-patch.js"
    curl -s -m 5 "$KAVITA_URL/" | grep -q 'zy-kavita-patch.js' \
      && echo 'index=注入' || echo 'index=未注入'
    mount_mode && echo 'mode=mount' || echo 'mode=copy'
    bash "$HERE/ctl.sh" status || true
    ;;
  *) echo "usage: $0 {install|publish|uninstall|render|status}"; exit 2 ;;
esac
