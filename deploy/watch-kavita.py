#!/usr/bin/env python3
"""Kavita 容器消失取证: 记录 docker events + 状态轮询 + 消失瞬间的进程快照。

背景: 2026-10-23 23:29 / 10-24 01:41 / 10-24 13:xx 容器多次"凭空消失"
(heal 报 No such container), 不是崩溃而是被删。docker 不保留事件历史,
只能从现在起现场目击。

输出: state/logs/kavita-watch.log + state/logs/kavita-events.log
用法: python3 deploy/watch-kavita.py   (建议 background 跑)
"""
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGDIR = os.path.join(ROOT, "state", "logs")
os.makedirs(LOGDIR, exist_ok=True)
WATCH = os.path.join(LOGDIR, "kavita-watch.log")
EVENTS = os.path.join(LOGDIR, "kavita-events.log")
CONTAINER = os.environ.get("ZY_WATCH_CONTAINER", "kavita")
COMPOSE_DIR = os.environ.get("ZY_COMPOSE_DIR", "/vol1/1001/APPS/kavita")


def log(path, msg):
    line = f"{datetime.now().strftime('%F %T')} {msg}"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


def sh(cmd, timeout=15):
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return (p.stdout or "") + (p.stderr or "")
    except Exception as e:
        return f"<{e}>"


def proc_scan():
    """1 秒扫一次 /proc, 抓 'docker rm / compose down / prune' 这类命令行。
    CLI 删容器只活几十毫秒, 5 秒轮询会漏, 必须逐秒扫。"""
    seen = set()
    bad = (" rm ", " rm\t", "down", "prune", "kill", "stop", "rmi")
    while True:
        try:
            for pid in os.listdir("/proc"):
                if not pid.isdigit() or pid in seen:
                    continue
                try:
                    with open(f"/proc/{pid}/cmdline", "rb") as f:
                        raw = f.read().replace(b"\x00", b" ").decode("utf-8", "replace").strip()
                except Exception:
                    continue
                if "docker" not in raw and "compose" not in raw:
                    continue
                low = f" {raw} "
                if any(k in low for k in bad):
                    info = sh(f"ps -o pid,ppid,user,etimes,args= -p {pid} 2>/dev/null; "
                              f"cat /proc/{pid}/environ 2>/dev/null | tr '\\0' '\\n' | grep -E '^(PWD|DOCKER_HOST)=' ")
                    log(WATCH, f"命中可疑 docker 命令 pid={pid}: {raw[:200]}\n{info.strip()}")
                seen.add(pid)
        except Exception as e:
            log(WATCH, f"proc_scan 出错: {e}")
        time.sleep(1)


def events_loop():
    """把 docker events 原样落盘 (含 die/destroy/kill 的 exitCode)。"""
    while True:
        try:
            p = subprocess.Popen(
                ["docker", "events", "--format",
                 "{{.Time}} {{.Type}}/{{.Action}} name={{.Actor.Attributes.name}} "
                 "exit={{.Actor.Attributes.exitCode}} sig={{.Actor.Attributes.signal}} "
                 "image={{.Actor.Attributes.image}}"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for line in p.stdout:
                if CONTAINER in line or "kavita" in line:
                    log(EVENTS, line.strip())
            p.wait()
            log(WATCH, "docker events 退出, 10s 后重连")
            time.sleep(10)
        except Exception as e:
            log(WATCH, f"docker events 启动失败: {e}")
            time.sleep(10)


def state():
    out = sh(f"docker inspect -f '{{{{.State.Status}}}}|{{{{.State.StartedAt}}}}|"
             f"{{{{.RestartCount}}}}|{{{{.State.ExitCode}}}}' {CONTAINER} 2>&1")
    return out.strip()


def snapshot(reason):
    """容器消失/状态突变时, 把可能相关的现场全抓下来。"""
    log(WATCH, f"---- 现场快照 ({reason}) ----")
    log(WATCH, "docker ps -a:\n" + sh("docker ps -a --format '{{.Names}} {{.Status}} {{.Image}}'"))
    log(WATCH, "docker compose ps:\n" + sh(f"cd {COMPOSE_DIR} && docker compose ps 2>&1"))
    log(WATCH, "docker 相关进程:\n" + sh(
        "ps -eo pid,ppid,user,etimes,args | grep -Ei 'docker|compose|kavita' | grep -v grep | head -40"))
    log(WATCH, "最近登录/提权痕迹:\n" + sh("who 2>/dev/null; last -n 5 2>/dev/null"))
    log(WATCH, f"---- 快照结束 ({reason}) ----")


def main():
    log(WATCH, f"开始监视 container={CONTAINER} compose={COMPOSE_DIR}")
    threading.Thread(target=events_loop, daemon=True).start()
    threading.Thread(target=proc_scan, daemon=True).start()
    prev = None
    while True:
        cur = state()
        if cur != prev:
            if prev is not None:
                log(WATCH, f"状态变化 {prev!r} -> {cur!r}")
                if "No such object" in cur or "No such" in cur:
                    snapshot("容器不存在")
                elif "exited" in cur or "created" in cur:
                    snapshot("容器停止")
            prev = cur
        time.sleep(5)


if __name__ == "__main__":
    main()
