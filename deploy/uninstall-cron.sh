#!/usr/bin/env bash
# 卸掉 install-cron.sh 写的受管块 (不动用户其它 cron 任务)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BEGIN="# >>> kavita-zy (managed by install-cron.sh) >>>"
END="# <<< kavita-zy <<<"

command -v crontab >/dev/null || { echo "没有 crontab 命令" >&2; exit 1; }

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

crontab -l 2>/dev/null | awk -v b="$BEGIN" -v e="$END" '
  $0 == b {skip=1; next}
  $0 == e {skip=0; next}
  skip != 1 {print}
' > "$TMP"

crontab "$TMP"
echo "已移除 kavita-zy 的 cron 任务 (其它任务保留)"
echo "（systemd 版若装过, 仍会继续跑）"
