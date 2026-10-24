from pathlib import Path

p = Path("/vol2/1001/kavita-zy-patch/install.sh")
s = p.read_text(encoding="utf-8")

old = '''render_asset() {
  local tok; tok="$(cat "$TOKEN_FILE")"
  mkdir -p "$BUILD_DIR"
  sed -e "s|__ZY_TOKEN__|$tok|g" \\
      -e "s|__ZY_PORT__|$API_PORT|g" \\
      -e "s|__ZY_VER__|$PATCH_VERSION|g" \\
      "$HERE/src/zy-kavita-patch.js.tmpl" > "$BUILD_DIR/zy-kavita-patch.js"
  echo "渲染 -> $BUILD_DIR/zy-kavita-patch.js"
}'''

new = '''render_asset() {
  local tok; tok="$(cat "$TOKEN_FILE")"
  mkdir -p "$BUILD_DIR"
  sed -e "s|__ZY_TOKEN__|$tok|g" \\
      -e "s|__ZY_PORT__|$API_PORT|g" \\
      -e "s|__ZY_WEB_PORT__|$WEB_PORT|g" \\
      -e "s|__ZY_VER__|$PATCH_VERSION|g" \\
      "$HERE/src/zy-kavita-patch.js.tmpl" > "$BUILD_DIR/zy-kavita-patch.js"
  # 渲染完必须没有残留占位符, 否则前端拿到的是 'http://...:__ZY_WEB_PORT__/'
  if grep -q '__ZY_[A-Z_]*__' "$BUILD_DIR/zy-kavita-patch.js"; then
    echo "ERR: 资产里还有未替换的占位符" >&2
    grep -o '__ZY_[A-Z_]*__' "$BUILD_DIR/zy-kavita-patch.js" | sort -u >&2
    return 1
  fi
  echo "渲染 -> $BUILD_DIR/zy-kavita-patch.js (console port $WEB_PORT)"
}'''

assert old in s, "render_asset block not found"
p.write_text(s.replace(old, new), encoding="utf-8")
print("patched install.sh")