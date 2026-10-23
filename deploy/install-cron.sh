#!/usr/bin/env bash
# 用户级自启动 (不需要 root): 往当前用户 crontab 写一个受管块
#
#   bash deploy/install-cron.sh         # 装 (幂等)
#   bash deploy/uninstall-cron.sh       # 卸
#
# 两条任务:
#   @reboot    开机 30 秒后跑一次 heal.sh  (它会把 5111/5012 拉起来)
#   */3 * * *  每 3 分钟跑一次 heal.sh     (自愈: 补注入 / 起进程 / 起容器)
#
# 有 root 的话优先用 systemd 版 (install-autostart.sh), 那个还能自动重启崩溃的
# 进程 (Restart=always)。cron 版只管"起得来", 进程崩了要等下一轮巡检。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HEAL="$ROOT/deploy/heal.sh"
BEGIN="# >>> kavita-zy (managed by install-cron.sh) >>>"
END="# <<< kavita-zy <<<"

command -v crontab >/dev/null || { echo "没有 crontab 命令" >&2; exit 1; }
[ -f "$HEAL" ] || { echo "缺 $HEAL" >&2; exit 1; }

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

# 先摘掉旧块 (若存在), 保留用户其它任务
crontab -l 2>/dev/null | awk -v b="$BEGIN" -v e="$END" '
  $0 == b {skip=1; next}
  $0 == e {skip=0; next}
  skip != 1 {print}
' > "$TMP"

cat >> "$TMP" <<EOF
$BEGIN
@reboot /bin/sleep 30; /bin/bash $HEAL >/dev/null 2>&1
*/3 * * * * /bin/bash $HEAL >/dev/null 2>&1
$END
EOF

crontab "$TMP"
echo "已写入当前用户 crontab:"
crontab -l | sed -n "/kavita-zy (managed/,/kavita-zy <<</p"
echo
echo "生效: 每 3 分钟巡检一次; 开机 30s 后首跑"
echo "下次不一定立刻跑, 想马上看效果: bash $HEAL"
echo "卸载: bash $ROOT/deploy/uninstall-cron.sh"
