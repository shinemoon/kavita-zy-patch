#!/usr/bin/env bash
# 一掌阅桥接自愈: 容器 / 前端注入 / 补丁后端 5111 / 控制台 5012
#
# 为什么需要: 前端补丁打在容器 overlay 可写层, 换镜像 (pull + recreate)
# 就没了; 5111 和 5012 是手工 nohup 起的, 重启机器就没了。这个脚本把
# 「该在的东西都在」当成一个幂等目标, 每次跑一遍, 缺什么补什么。
#
# 调用方:
#   - systemd timer (kavita-zy-heal.timer, 默认 3 分钟)
#   - 控制台按钮「立即自检」/ 命令行 deploy/heal.sh
#
# 退出码: 0 = 一切 OK (含"修好了"), 1 = 有修不动的项 (看日志)
# 关掉自愈: touch state/heal.disabled   (或控制台点「关自愈」)
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1090
source "$ROOT/patch.conf"

LOG="$ROOT/state/logs/heal.log"
STATE="$ROOT/state/heal.json"
DISABLE_FLAG="$ROOT/state/heal.disabled"
BRIDGE_ROOT="${BRIDGE_ROOT:-/vol2/1001/zhangyue-bridge}"
WEB_DIR="$BRIDGE_ROOT/sync"
WEB_PID="$BRIDGE_ROOT/state/web.pid"

mkdir -p "$ROOT/state/logs"

# 日志上限 512KB, 超了留一份 .1
if [ -f "$LOG" ] && [ "$(stat -c %s "$LOG" 2>/dev/null || echo 0)" -gt 524288 ]; then
  mv -f "$LOG" "$LOG.1"
fi

log() { printf '%s %s\n' "$(date '+%F %T')" "$*" >> "$LOG"; }

# 心跳: 每小时写一条 "still ok", 其余成功巡检只更新 heal.json (避免刷屏)
BEAT="$ROOT/state/heal.last_beat"
heartbeat() {
  local now prev=0
  now="$(date +%s)"
  [ -f "$BEAT" ] && prev="$(cat "$BEAT" 2>/dev/null || echo 0)"
  if [ "$((now - prev))" -ge 3600 ]; then
    log "heartbeat ok (kavita=$1 api=$2 web=$3)"
    echo "$now" > "$BEAT"
  fi
}

problems=0
actions=""

note_action() { actions="${actions:+$actions, }$1"; }

if [ -f "$DISABLE_FLAG" ]; then
  log "self-heal disabled ($DISABLE_FLAG), skip"
  printf '{"ts":"%s","ok":true,"disabled":true,"actions":""}\n' "$(date -Iseconds)" > "$STATE"
  exit 0
fi

http_code() { curl -s -o /dev/null -m 6 -w '%{http_code}' "$1" 2>/dev/null || echo 000; }

# systemd unit 存在时, 服务归 systemd 管 —— 不自己再拉一份进程, 否则两个写者
# 抢同一个端口: systemd 启失败 → Restart=always 无限重试, 端口被孤儿占着,
# `systemctl restart` 永远起不来。unit 在就用 systemctl, 起不动就让 root 的
# heal.timer 接管 (它以 root 跑, 能 systemctl start)。
unit_exists() {
  local all
  all="$(systemctl list-unit-files 2>/dev/null)" || return 1
  case "$all" in *"$1.service"*) return 0 ;; esac
  return 1
}
AM_ROOT=no; [ "$(id -u)" = "0" ] && AM_ROOT=yes

# 端口上的 pid (ss -p 需要 root 才给 pid; 非 root 时返回空 → 不做孤儿清理)
pids_on_port() {
  local raw tok pid
  raw="$(ss -ltnpH "sport = :$1" 2>/dev/null)" || return 0
  # ss 输出里 pid 藏在 users:(("python3",pid=1234,fd=3)) 中间, 不是独立字段
  while [ -n "$raw" ]; do
    case "$raw" in
      *pid=*) ;;
      *) break ;;
    esac
    raw="${raw#*pid=}"
    pid="${raw%%[!0-9]*}"
    [ -n "$pid" ] && echo "$pid"
    raw="${raw#*pid=$pid}"
  done | sort -u
}

# unit 存在时端口可能被"上一代手工/定时脚本拉起的孤儿"占着 → systemd 永远起不来
# (EADDRINUSE → Restart=always → 无限重试)。只在 root 下做, 且只杀 cmdline 匹配
# 指定脚本名、又不是 systemd MainPID 的进程。
reap_orphan() {     # $1=unit $2=端口 $3=cmdline 关键字
  local unit="$1" port="$2" needle="$3" mainpid pid cmd
  [ "$AM_ROOT" = "yes" ] || return 0
  mainpid="$(systemctl show -p MainPID --value "$unit" 2>/dev/null || echo 0)"
  for pid in $(pids_on_port "$port"); do
    [ "$pid" = "$mainpid" ] && continue
    cmd="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null)" || continue
    case "$cmd" in
      *"$needle"*)
        if kill "$pid" 2>/dev/null; then log "OK 清掉占 $port 的孤儿 pid=$pid ($cmd)"; sleep 1; fi ;;
    esac
  done
}

start_service() {   # $1=unit $2=额外兜底命令 (空 = 无兜底)
  local unit="$1" fb="${2:-}" started=no
  if unit_exists "$unit"; then
    systemctl start "$unit" >/dev/null 2>&1 && started=yes
    if [ "$started" != "yes" ] && [ "$AM_ROOT" != "yes" ]; then
      log "WARN $unit 存在但不归我拉 (非 root), 留给 systemd / heal.timer"
      return 2      # 2 = 交给 systemd, 不算故障
    fi
    return $([ "$started" = "yes" ] && echo 0 || echo 1)
  fi
  if [ -n "$fb" ]; then
    bash -c "$fb" >>"$LOG" 2>&1 && started=yes
  fi
  [ "$started" = "yes" ] && return 0 || return 1
}


# ---- 1. 容器 ---------------------------------------------------------
# 之前这里只在日志里留一句 "FAIL 容器启动失败", 拿不到 docker 的原始错误,
# 结果 2026-10-23 23:29 起它每 30s 报一次"未运行", 谁也看不出原因。
# 现在:
#   - docker start 的 stderr 一起记下来
#   - 容器**在跑但 Kavita 打不开**连续 3 次 → docker restart (进程活着但僵死)
#   - 告警去重: 同一个问题 30 分钟内只记一次, 别再刷 600 行
touch "$ROOT/state/heal.notice"
notice_once() {   # $1=key $2=message   ($1 作为去重键; 30 分钟窗口)
  local key="$1" msg="$2" now prev_ts prev_key
  now="$(date +%s)"
  read -r prev_ts prev_key < "$ROOT/state/heal.notice" 2>/dev/null || { prev_ts=0; prev_key=""; }
  if [ "$prev_key" = "$key" ] && [ "$((now - prev_ts))" -lt 1800 ]; then return 0; fi
  printf '%s %s\n' "$now" "$key" > "$ROOT/state/heal.notice"
  log "$msg"
}

recreate_container() {   # 容器被删时唯一的复原手段
  local out
  out="$(cd "${COMPOSE_DIR:-/vol1/1001/APPS/kavita}" && docker compose up -d 2>&1)"
  if [ $? -eq 0 ]; then
    log "OK 容器已按 compose 重建"
    note_action "recreate-container"
    return 0
  fi
  notice_once "recreatefail-$CONTAINER" \
    "FAIL 容器重建失败: $(printf '%s' "$out" | tr '\n' ' ' | cut -c1-300)"
  return 1
}

# 容器状态: missing / true / false
# 注意别写 inspect ... || echo missing —— 对象不存在时 inspect 会先吐一个空行,
# 拼出来是 "\nmissing", 跟 "missing" 永远不相等 (2026-10-24 踩过, 导致
# "容器被删" 一直被当成 "容器停止", 只会 docker start 然后失败)。
ctr_state() {
  local names out
  names="$(docker ps -a --filter "name=^${CONTAINER}$" --format '{{.Names}}' 2>/dev/null)"
  [ -z "$names" ] && { echo missing; return; }
  out="$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null | tr -d '[:space:]')"
  echo "${out:-false}"
}

# kavita.service 被人为 stop 时 (inactive), 不要自作主张把容器拉回来。
# 故障态下 unit 是 active(exited), 所以这道闸不会影响真正的自愈。
ctr_allowed=yes
if unit_exists kavita.service; then
  case "$(systemctl is-active kavita.service 2>/dev/null)" in
    inactive)
      ctr_allowed=no
      notice_once "ctr-skip" "kavita.service 处于 inactive (人为停止), 跳过容器恢复" ;;
  esac
fi

running="$(ctr_state)"
if [ "$ctr_allowed" = "no" ]; then
  :   # 容器这块不动, 也不计入故障
elif [ "$running" = "missing" ]; then
  # 2026-10-24: 容器多次被**删掉** (12:40 那次持续 2.5 小时), 而这里以前只记一句
  # "不存在" 就返回 → 用户只能手动 systemctl restart。
  # docker start 救不了已删容器, 必须 compose up 重建。
  log "WARN 容器 $CONTAINER 不存在, 按 compose 重建"
  if recreate_container; then
    for _ in 1 2 3 4 5 6; do
      [ "$(ctr_state)" = "true" ] && break
      sleep 5
    done
    running="$(ctr_state)"
  fi
  [ "$running" = "true" ] || problems=$((problems + 1))
elif [ "$running" != "true" ]; then
  # 只在容器确实停止时才开始尝试 (启动最多等 30s); 失败原因原样落日志
  notice_once "stopped-$CONTAINER" "容器 $CONTAINER 未运行, 尝试启动"
  out="$(docker start "$CONTAINER" 2>&1)"
  if [ $? -eq 0 ]; then
    note_action "start-container"; log "OK 容器已启动"
  else
    # 极端情况: 容器停在 "marked for removal" 之类状态, start 也失败 → 重建一次
    notice_once "startfail-$CONTAINER" "FAIL 容器启动失败: $(printf '%s' "$out" | tr '\n' ' ' | cut -c1-300)"
    if recreate_container; then
      running="$(ctr_state)"
      [ "$running" = "true" ] || problems=$((problems + 1))
    else
      problems=$((problems + 1))
    fi
  fi
fi

# ---- 2. Kavita 可访问 (等最多 60s) -----------------------------------
kv=""
for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do
  kv="$(http_code "$KAVITA_URL/")"
  case "$kv" in 200|302|304) break ;; esac
  sleep 5
done
case "$kv" in
  200|302|304) echo 0 > "$ROOT/state/heal.kv_fail" ;;
  *)
    notice_once "kvdown-$kv" "FAIL Kavita $KAVITA_URL 不可访问 (http=$kv)"
    fails=$(( $(cat "$ROOT/state/heal.kv_fail" 2>/dev/null || echo 0) + 1 ))
    echo "$fails" > "$ROOT/state/heal.kv_fail"
    # 容器在跑但应用三层连续打不开 = 进程僵死 (2026-10-24: Kavita 挂着但没人看,
    # 早上起来整个服务 000000)。docker restart 是唯一能救的手段。
    if [ "$running" = "true" ] && [ "$fails" -ge 3 ]; then
      out="$(docker restart "$CONTAINER" 2>&1)"
      if [ $? -eq 0 ]; then
        note_action "restart-container"
        log "WARN Kavita 连续 $fails 次打不开, 容器已重启"
        echo 0 > "$ROOT/state/heal.kv_fail"
      else
        log "FAIL 容器重启失败: $(printf '%s' "$out" | tr '\n' ' ' | cut -c1-300)"
      fi
      for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do
        kv="$(http_code "$KAVITA_URL/")"
        case "$kv" in 200|302|304) break ;; esac
        sleep 5
      done
    fi
    case "$kv" in
      200|302|304) : ;;   # 重启后活了, 不算故障
      *) problems=$((problems + 1)) ;;
    esac ;;
esac


# ---- 3. 前端注入 -----------------------------------------------------
if [ "$kv" = "200" ] || [ "$kv" = "302" ]; then
  asset="$(http_code "$KAVITA_URL/zy-kavita-patch.js")"
  injected=no
  kv_index="$(curl -s -m 6 "$KAVITA_URL/")" || kv_index=""
  case "$kv_index" in *zy-kavita-patch.js*) injected=yes ;; esac

  # 挂载模式下镜像升级 → 挂着的 index.html 会引用旧镜像的 chunk 哈希 → 页面白屏,
  # 但 asset 和注入都还是"好的"。所以额外验一个 index 里引用的 main-*.js 能不能拿到。
  if [ "$injected" = yes ]; then
    main_js="$(printf '%s' "$kv_index" | grep -oE 'main-[A-Z0-9]+\.js' | head -1)"
    if [ -n "$main_js" ]; then
      mc="$(http_code "$KAVITA_URL/$main_js")"
      if [ "$mc" != "200" ]; then
        log "挂载的 index.html 引用了不存在的 $main_js (http=$mc), 镜像升级了? 重建注入版"
        injected=stale
      fi
    fi
  fi

  if [ "$asset" != "200" ] || [ "$injected" != "yes" ]; then
    log "注入失效 (asset=$asset index=$injected), 重打"
    if bash "$ROOT/deploy/reapply.sh" >>"$LOG" 2>&1; then
      note_action "reapply"; log "OK 补丁已重打"
    else
      problems=$((problems + 1)); log "FAIL 重打失败, 看上面输出"
    fi
  fi
fi

# ---- 4. 补丁后端 5111 ------------------------------------------------
tok="$(cat "$TOKEN_FILE" 2>/dev/null || true)"
api_ok=no
if [ -n "$tok" ]; then
  api_body="$(curl -s -m 6 -H "X-ZY-Token: $tok" "http://127.0.0.1:$API_PORT/zy/api/health")" || api_body=""
  case "$api_body" in *'"ok"'*) api_ok=yes ;; esac
fi
if [ "$api_ok" != "yes" ]; then
  log "补丁后端 :$API_PORT 没响应, 拉起"
  reap_orphan kavita-zy-api.service "$API_PORT" api_server.py
  start_service kavita-zy-api "bash \"$ROOT/ctl.sh\" start"; rc=$?
  sleep 2
  if [ "$rc" = "2" ]; then
    log "WARN 补丁后端交给 systemd 重启 (本次不重复拉进程)"
  elif [ "$rc" = "0" ]; then
    api_body="$(curl -s -m 6 -H "X-ZY-Token: $tok" "http://127.0.0.1:$API_PORT/zy/api/health")" || api_body=""
    case "$api_body" in
      *'"ok"'*) api_ok=yes; note_action "start-api"; log "OK 补丁后端已启动" ;;
    esac
  fi
  if [ "$api_ok" != "yes" ] && [ "$rc" != "2" ]; then
    problems=$((problems + 1)); log "FAIL 补丁后端起不来, 看 state/logs/api.out"
  fi
fi

# ---- 5. 控制台 5012 --------------------------------------------------
web="$(http_code "http://127.0.0.1:$WEB_PORT/healthz")"
if [ "$web" != "200" ]; then
  log "控制台 :$WEB_PORT 没响应, 拉起"
  reap_orphan kavita-zy-web.service "$WEB_PORT" web.py
  if [ -f "$WEB_DIR/zyweb.sh" ]; then
    start_service kavita-zy-web "sh \"$WEB_DIR/zyweb.sh\" start"; rc=$?
  else
    start_service kavita-zy-web ""; rc=$?
  fi
  sleep 2
  web="$(http_code "http://127.0.0.1:$WEB_PORT/healthz")"
  if [ "$web" = "200" ]; then
    note_action "start-web"; log "OK 控制台已启动"
  elif [ "$rc" = "2" ]; then
    log "WARN 控制台交给 systemd 重启 (本次不重复拉进程)"
  else
    problems=$((problems + 1)); log "FAIL 控制台起不来 (http=$web), 看 $BRIDGE_ROOT/state/logs/web.out"
  fi
fi

# ---- 5b. 代码比进程新 → 重启后端 (只有 root 能 systemctl restart) ------
# 改了 src/*.py 或 sync 包之后, 不用等人手动 sudo systemctl restart:
# heal 每次巡检顺带比一次 mtime, 代码新就重启。非 root (crontab 那条) 跳过。
reload_if_stale() {   # $1=unit, 其余=代码文件
  local unit="$1"; shift
  [ "$AM_ROOT" = "yes" ] || return 0
  [ "$(systemctl is-active "$unit" 2>/dev/null || true)" = "active" ] || return 0
  local started_epoch newest=0 f mt
  started_epoch="$(date -d "$(systemctl show -p ActiveEnterTimestamp --value "$unit" 2>/dev/null)" +%s 2>/dev/null || echo 0)"
  [ "$started_epoch" -gt 0 ] || return 0
  for f in "$@"; do
    [ -f "$f" ] || continue
    mt="$(stat -c %Y "$f" 2>/dev/null || echo 0)"
    [ "$mt" -gt "$newest" ] && newest="$mt"
  done
  [ "$newest" -gt "$started_epoch" ] || return 0
  log "WARN $unit 代码比进程新, 重启加载"
  if systemctl restart "$unit" >/dev/null 2>&1; then
    note_action "reload-$unit"
    log "OK $unit 已重启 (新代码生效)"
  else
    log "FAIL 重启 $unit 失败"
  fi
}

reload_if_stale kavita-zy-api.service \
  "$ROOT/src/api_server.py" "$ROOT/patch.conf" \
  "$BRIDGE_ROOT/sync/sync/sync.py" "$BRIDGE_ROOT/sync/sync/config.py" \
  "$BRIDGE_ROOT/sync/sync/kpages.py" "$BRIDGE_ROOT/sync/sync/mapping.py" \
  "$BRIDGE_ROOT/sync/sync/kavita_db.py" "$BRIDGE_ROOT/sync/sync/epubprep.py" \
  "$BRIDGE_ROOT/sync/sync/zpct.py"
reload_if_stale kavita-zy-web.service \
  "$BRIDGE_ROOT/sync/web.py" "$BRIDGE_ROOT/sync/sync/sync.py" \
  "$BRIDGE_ROOT/sync/sync/kpages.py" "$BRIDGE_ROOT/sync/sync/cloud_books.py" \
  "$BRIDGE_ROOT/sync/sync/kavita_db.py" "$BRIDGE_ROOT/sync/sync/epubprep.py" \
  "$BRIDGE_ROOT/sync/sync/zpct.py"

# ---- 5c. 双写者纠偏 (只有 root 能做) ---------------------------------
# 端口活着但服务不是 systemd 起的那份 = 上一代脚本留下的孤儿。此时 unit 会
# 一直 EADDRINUSE 重试 (Restart=always), `systemctl restart` 也永远失败。
# 条件收紧: unit 必须 enabled (说明这台机器就是要 systemd 管), 且它当前没跑。
bind_takeover() {   # $1=unit $2=端口 $3=cmdline 关键字 $4=健康检查 URL
  local unit="$1" port="$2" needle="$3" url="$4" mainpid
  [ "$AM_ROOT" = "yes" ] || return 0
  [ "$(systemctl is-enabled "$unit" 2>/dev/null || echo disabled)" = "enabled" ] || return 0
  # 只看 unit 是否 active。重启循环里 is-active = activating/auto-restart,
  # MainPID 会瞬间非 0, 用它判断会漏掉"永远起不来"这种状态。
  case "$(systemctl is-active "$unit" 2>/dev/null || true)" in
    active) return 0 ;;
  esac
  log "WARN $unit 已启用但没在跑, :$port 被非 systemd 进程占着, 交给 systemd 接管"
  reap_orphan "$unit" "$port" "$needle"
  if [ "$(http_code "$url")" != "200" ]; then
    systemctl reset-failed "$unit" >/dev/null 2>&1 || true
    systemctl start "$unit" >/dev/null 2>&1 || true
    sleep 2
    [ "$(http_code "$url")" = "200" ] && { log "OK $unit 已由 systemd 接管"; note_action "takeover-$port"; }
  fi
}

bind_takeover kavita-zy-api.service "$API_PORT" api_server.py \
  "http://127.0.0.1:$API_PORT/zy/api/health"
bind_takeover kavita-zy-web.service "$WEB_PORT" web.py \
  "http://127.0.0.1:$WEB_PORT/healthz"

# ---- 5d. 取证脚本 (可选, 只由 cron 的普通用户拉起, 避免 root/claud 双写者) ----
# 想知道"容器到底被谁删了": touch state/watch-kavita.on, 之后 heal 保证它常驻。
# 它逐秒扫 /proc + 录 docker events, 谁执行 docker rm/down/prune 会连同
# pid/ppid/user/PWD 一起落到 state/logs/kavita-watch.log。
if [ -f "$ROOT/state/watch-kavita.on" ] && [ "$AM_ROOT" = "no" ]; then
  if ! pgrep -f "deploy/watch-kavita.py" >/dev/null 2>&1; then
    setsid python3 "$ROOT/deploy/watch-kavita.py" >> "$ROOT/state/logs/kavita-watch.out" 2>&1 < /dev/null &
    log "OK 取证脚本 watch-kavita 已启动"
  fi
fi

# ---- 6. 落状态 -------------------------------------------------------
ok=true; [ "$problems" -gt 0 ] && ok=false
printf '{"ts":"%s","ok":%s,"problems":%d,"actions":"%s","kavita_http":"%s"}\n' \
  "$(date -Iseconds)" "$ok" "$problems" "$actions" "$kv" > "$STATE"

[ "$problems" -eq 0 ] && heartbeat "$kv" "$api_ok" "$web"

if [ "$problems" -gt 0 ]; then
  exit 1
fi
[ -n "$actions" ] && echo "已修复: $actions"
echo "ok"
