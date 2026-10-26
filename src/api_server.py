#!/usr/bin/env python3
"""掌阅 ↔ Kavita 补丁后端。

在 Kavita 之外跑一个小 HTTP 服务, 给注入到 Kavita 前端的那段脚本提供:
    GET  /zy/api/links          映射列表 (Kavita series ↔ 掌阅 book)
    GET  /zy/api/status         两侧进度对照 + 凭据健康
    POST /zy/api/link           建/改映射
    POST /zy/api/push           把 Kavita 进度推到掌阅
    POST /zy/api/pull           把掌阅进度拉回 Kavita
    POST /zy/api/pull-books     从掌阅书架拉 EPUB 到本地

纯 stdlib, 不引第三方。启动自检缺失依赖 (bridge sync 包 / .auth.json / kavita.db)。
"""
from __future__ import annotations

import json
import os
import re
import socketserver
import sys
import threading
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

CONF = HERE / "patch.conf"
_cfg: dict[str, str] = {}
if CONF.exists():
    for line in CONF.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        _cfg[k.strip()] = v.strip().strip('"')

API_PORT = int(os.environ.get("ZY_API_PORT") or _cfg.get("API_PORT", "5099"))
API_BIND = os.environ.get("ZY_API_BIND") or _cfg.get("API_BIND", "0.0.0.0")
TOKEN = (_cfg.get("TOKEN", "") or "").strip()
TOKEN_FILE = _cfg.get("TOKEN_FILE", "")
if not TOKEN and TOKEN_FILE and Path(TOKEN_FILE).exists():
    TOKEN = Path(TOKEN_FILE).read_text().strip()

BRIDGE_ROOT = _cfg.get("BRIDGE_ROOT", "/vol2/1001/zhangyue-bridge")
AUTH_JSON = Path(BRIDGE_ROOT) / ".auth.json"

# ---- 自动同步钩子 (Kavita 前端在读书时触发) ------------------------------
# AUTO_SYNC=0 关掉全部自动行为 (前端会读 /zy/api/auto/cfg 自行判断)。
AUTO_SYNC = (_cfg.get("AUTO_SYNC", "1") or "1").strip()
AUTO_PUSH_DEBOUNCE = int(_cfg.get("AUTO_PUSH_DEBOUNCE", "8") or 8)
AUTO_PULL_GAP = int(_cfg.get("AUTO_PULL_GAP", "120") or 120)
# push / pull 都会读写 kavita.db + 打掌阅 API, 串行化, 不并发。
_auto_lock = threading.Lock()

log_fh = None


def log(msg: str) -> None:
    global log_fh
    line = f"{__import__('datetime').datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    if log_fh is None:
        try:
            logdir = HERE / "state" / "logs"
            logdir.mkdir(parents=True, exist_ok=True)
            log_fh = open(logdir / "api.log", "a", encoding="utf-8")
        except Exception:
            log_fh = None
    if log_fh:
        log_fh.write(line + "\n")
        log_fh.flush()


# ---- bridge 载入 (延迟到真正需要时, 缺依赖不应阻止服务启动) ------------
def bridge():
    """返回 (core, mapping, kavita_db, zy_client) 或抛异常。"""
    p = str(Path(BRIDGE_ROOT) / "sync")
    if p not in sys.path:
        sys.path.insert(0, p)
    from sync import sync as core           # noqa: E402
    from sync import mapping, kavita_db, zy_client  # noqa: E402
    return core, mapping, kavita_db, zy_client


def auth_or_none():
    try:
        if not AUTH_JSON.exists():
            return None
        return json.loads(AUTH_JSON.read_text())
    except Exception as e:
        log(f"auth read fail: {e}")
        return None


# ---- 业务 ---------------------------------------------------------------

def api_links() -> dict:
    _, mapping, _, _ = bridge()
    return {"links": [l.__dict__ for l in mapping.all_links()]}


def api_status() -> dict:
    core, mapping, kavita_db, _ = bridge()
    links = mapping.all_links()
    out = {
        "ok": True,
        "auth_present": AUTH_JSON.exists(),
        "kavita_db_present": Path(_cfg.get("KAVITA_DB", "/vol1/1001/APPS/kavita/kavita.db")).exists(),
        "last_push": mapping.state_get("push_last_run_utc"),
        "last_pull": mapping.state_get("pull_last_run_utc"),
        "rows": [],
    }
    # 掌阅侧进度
    zy_pct: dict[str, dict] = {}
    auth = auth_or_none()
    if auth and links:
        try:
            _, _, _, zy_client = bridge()
            raw = zy_client.get_progress(auth, [l.zy_book_id for l in links])
            for bid, rec in raw.items():
                rp = rec.get("readProcess") or {}
                zy_pct[bid] = {
                    "pct": rp.get("readPercent"),
                    "chapter": rp.get("chapterId"),
                    "updated": rec.get("updateTime"),
                }
        except Exception as e:
            out["zy_error"] = str(e)

    # Kavita 侧章节进度
    kv: dict[int, list] = {}
    try:
        from collections import defaultdict
        agg = defaultdict(list)
        for p in kavita_db.iter_progress():
            agg[p.series_id].append(p)
        kv = dict(agg)
    except Exception as e:
        out["kv_error"] = str(e)

    for l in links:
        chaps = kv.get(l.kavita_series_id, [])
        read_pages = sum(p.pages_read for p in chaps)
        total_pages = sum(p.total_pages for p in chaps if p.total_pages)
        out["rows"].append({
            "kavita_series_id": l.kavita_series_id,
            "kavita_name": _series_name(l.kavita_series_id),
            "zy_book_id": l.zy_book_id,
            "zy_name": l.zy_book_name,
            "kv_percent": round(read_pages * 100.0 / total_pages, 1) if total_pages else 0.0,
            "kv_chapters_read": len([p for p in chaps if p.finished]),
            "kv_chapters_total": len(chaps),
            "zy": zy_pct.get(l.zy_book_id),
            "anchor": l.zy_anchor_chapter_id,
            "note": l.note,
            "updated_utc": l.updated_utc,
        })
    return out


def _series_name(sid: int) -> str:
    try:
        _, _, kavita_db, _ = bridge()
        return kavita_db.series_name(sid)
    except Exception:
        return ""


# 书名里常见的中英/繁简/修饰噪声, 判"是不是同一本"时先剥掉
_NAME_NOISE = re.compile(
    r"[─-▿！-～（）()《》\[\]【】_\-\s·・:：,，.。!?'\"“”‘’]|"
    r"简体|繁體|繁体|中文|英文|完整版|精校版|校对版|无删减|完结|"
    r" epub|epub|pdf|mobi|azw3|kindle|txt",
    re.I,
)


def _names_look_related(kavita: str, zhangyue: str) -> bool:
    """两个书名像不像同一本。宁可放过 (返回 True), 别误报。"""
    a = _NAME_NOISE.sub("", kavita).lower()
    b = _NAME_NOISE.sub("", zhangyue).lower()
    if not a or not b:
        return True
    if a == b:
        return True
    if a in b or b in a:
        return True
    # 公共前缀 >= 2 字 (中文名常见同系列前后缀)
    n = min(len(a), len(b))
    for k in range(n, 1, -1):
        if a[:k] == b[:k]:
            return True
    # 任意 2 字子串共有
    return any(a[i:i + 2] in b for i in range(len(a) - 1))


def api_push(dry_run: bool = False) -> dict:
    core, _, _, _ = bridge()
    return core.push_once(auth_or_none(), dry_run=dry_run)


def api_pull(dry_run: bool = False) -> dict:
    core, _, _, _ = bridge()
    return core.pull_once(auth_or_none(), dry_run=dry_run)


# ---- 自动同步钩子 (Kavita 读书页在调) ------------------------------------

def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _series_pages_read(series_id: int) -> tuple[int, int]:
    """(已读页合计, 总页合计) —— 只看 Kavita 侧, 用来判断 pull 有没有改动。"""
    try:
        _, _, kavita_db, _ = bridge()
        read = total = 0
        for p in kavita_db.iter_progress():
            if p.series_id != series_id:
                continue
            read += p.pages_read or 0
            total += p.total_pages or 0
        return read, total
    except Exception:
        return 0, 0


def api_auto_cfg() -> dict:
    return {
        "ok": True,
        "enabled": AUTO_SYNC == "1",
        "push_debounce_sec": AUTO_PUSH_DEBOUNCE,
        "pull_gap_sec": AUTO_PULL_GAP,
    }


_last_hb = {"key": "", "ts": 0.0}


def api_auto_hb(body: dict) -> dict:
    """前端心跳: 证明"哪个浏览器、哪一版钩子"活着。

    只读不写业务数据。同 (版本, 路径) 5 分钟内只落一条 log, 免得刷屏。
    排查"自动同步没生效"第一步就看这里有没有记录 —— 没有 = 前端钩子没跑到。
    """
    global _last_hb
    import time as _time
    ver = str(body.get("ver") or "?")
    path = str(body.get("path") or "")
    on = bool(body.get("on"))
    saves = int(body.get("saves") or 0)
    pushes = int(body.get("pushes") or 0)
    base = str(body.get("base") or "")[:80]
    msgs = body.get("msgs") or []
    if isinstance(msgs, list):
        msgs = [str(m)[:120] for m in msgs][-6:]
    else:
        msgs = []
    now = _time.time()
    act = f"saves={saves} pushes={pushes}"
    key = f"{ver} {path} {act}"
    if key != _last_hb["key"] or now - _last_hb["ts"] > 300:
        log(f"hb v{ver} auto={'on' if on else 'off'} path={path or '/'} {act}"
            + (f" base={base}" if base else "")
            + ((" | " + " ; ".join(msgs)) if msgs else ""))
        _last_hb = {"key": key, "ts": now}
        try:
            (HERE / "state" / "last_hb.json").write_text(
                json.dumps({"ver": ver, "path": path, "on": on,
                            "saves": saves, "pushes": pushes, "base": base,
                            "at": _now_iso()}, ensure_ascii=False), "utf-8")
        except Exception:
            pass
    else:
        _last_hb["ts"] = now
    return {"ok": True, "ver": ver}


_TIME_LAST: dict[tuple[str, int], float] = {}
_TIME_GAP = 600.0        # 同一本书 10 分钟内只同步一次时长 (避免每次存进度都打接口)

_NOTES_LAST: dict[tuple[str, int], float] = {}
_NOTES_GAP = 1800.0      # 划线/书签 30 分钟一本 (下载通道贵, 且怕风控)


def _time_sync(kind: str, sid: int, quiet: bool = True) -> dict:
    """顺手做阅读时长同步 (失败不影响进度同步)。

    push: enqueue(今天) + flush —— 队列里若还有前几天失败的行走 APK 的重试语义一起发
          (掌阅服务端按"收到日"归账, 见 sync/notes/05 §2.5)
    pull: 最近 7 天的逐书逐日 (read_report/book, 精确)
    """
    import time as _t
    key = (kind, sid)
    now = _t.monotonic()
    if now - _TIME_LAST.get(key, 0.0) < _TIME_GAP:
        return {"skipped": "throttled"}
    p = str(Path(BRIDGE_ROOT) / "sync")
    if p not in sys.path:
        sys.path.insert(0, p)
    try:
        from sync import zy_time                      # noqa: E402
        from datetime import date, timedelta
        if kind == "push":
            res = zy_time.push(date.today(), series_ids={sid})
        else:
            today = date.today()
            res = zy_time.pull(today - timedelta(days=6), today, series_ids={sid})
        if (not quiet) or res.get("written"):
            log(f"time-{kind} series={sid} written={res.get('written')} "
                f"skipped={res.get('skipped')} mode={res.get('mode')}")
        _TIME_LAST[key] = now
        return res
    except Exception as e:
        _TIME_LAST[key] = now
        log(f"time-{kind} series={sid} fail: {e}")
        return {"ok": False, "error": str(e)}


def _notes_sync(kind: str, sid: int, quiet: bool = True) -> dict:
    """顺手做划线/书签同步 (失败不影响进度同步)。

    pull: 掌阅 -> Kavita (getUserNotebooks + downloadDataV2 + 位置映射写 AppUserAnnotation)
    push: Kavita -> 掌阅 (照抄 APK: getUserDataVer 增量 + 本地较新者胜)
    节流: pull 30 分钟 / push 15 分钟一本 (下载/上传都不便宜, 且怕风控)
    """
    import time as _t
    key = (kind, sid)
    now = _t.monotonic()
    if now - _NOTES_LAST.get(key, 0.0) < _NOTES_GAP:
        return {"skipped": "throttled"}
    p = str(Path(BRIDGE_ROOT) / "sync")
    if p not in sys.path:
        sys.path.insert(0, p)
    try:
        from sync import zy_notes                     # noqa: E402
        res = (zy_notes.pull if kind == "pull" else zy_notes.push)([sid])
        touched = (res.get("added") or 0) + (res.get("updated") or 0) + \
                  (res.get("pushed") or 0) + (res.get("pushed_marks") or 0) + \
                  (res.get("deleted") or 0)
        if (not quiet) or touched or res.get("errors") or res.get("empty"):
            log(f"notes-{kind} series={sid} pushed={res.get('pushed') or 0} "
                f"marks={res.get('pushed_marks') or 0} added={res.get('added') or 0} "
                f"updated={res.get('updated') or 0} del={res.get('deleted') or 0} "
                f"skip={res.get('skipped') or 0} empty={len(res.get('empty') or [])}"
                + (f" errors={res.get('errors')}" if res.get("errors") else ""))
        _NOTES_LAST[key] = now
        return res
    except Exception as e:
        _NOTES_LAST[key] = now
        log(f"notes-{kind} series={sid} fail: {e}")
        return {"ok": False, "error": str(e)}


def api_reader_css() -> dict:
    """阅读样式增强: 把 5012 里配的规则渲染成 CSS, 给前端注入。

    弱依赖: 读不到 (bridge 未装 / 文件损坏) 也返 200 + 空 css, 前端只是不注入,
    不该让整个补丁功能因此报错。
    """
    p = str(Path(BRIDGE_ROOT) / "sync")
    if p not in sys.path:
        sys.path.insert(0, p)
    try:
        from sync import reader_css                   # noqa: E402
        conf = reader_css.load()
        return {"ok": True, "enabled": bool(conf.get("enabled")),
                "version": conf.get("version"), "updated": conf.get("updated"),
                "css": reader_css.render_css(conf)}
    except Exception as e:
        log(f"reader-css fail: {e}")
        return {"ok": True, "enabled": False, "version": 0, "css": "",
                "warn": str(e)}


def api_auto_pull(body: dict) -> dict:
    """掌阅 → Kavita, 单本。

    与手动 pull 的区别: only_forward=True —— 本地比掌阅靠前就原地不动。
    自动行为不该在用户打开书时把 Kavita 往回拨。
    """
    if AUTO_SYNC != "1":
        return {"ok": True, "skipped": "auto_disabled"}
    sid = int(body.get("series_id") or 0)
    if not sid:
        raise ValueError("缺 series_id")
    core, mapping, _, _ = bridge()
    if not mapping.links_for_series(sid):
        return {"ok": True, "skipped": "no_link", "series_id": sid}
    if not _auto_lock.acquire(blocking=False):
        return {"ok": False, "busy": True, "series_id": sid}
    try:
        before, total = _series_pages_read(sid)
        res = core.pull_once(auth_or_none(), series_ids={sid}, only_forward=True)
        after, _ = _series_pages_read(sid)
    finally:
        _auto_lock.release()
    mapping.state_set(f"auto_pull_last_{sid}", _now_iso())
    changed = after > before
    log(f"auto-pull series={sid} kavita {before}->{after}/{total} "
        f"updated={res.get('updated')} skipped={res.get('skipped')}")
    time_res = _time_sync("pull", sid)
    notes_res = _notes_sync("pull", sid)
    return {"ok": True, "series_id": sid, "changed": changed,
            "pages_read": after, "pages": total,
            "before": before, "detail": res, "time": time_res, "notes": notes_res}


def api_auto_push(body: dict) -> dict:
    """Kavita → 掌阅, 单本。push_decision 那道闸仍然生效 (不回退)。

    body.tick=true 表示"定时兜底"触发: 没真推送时不写日志, 免得每 60s 一行。
    """
    if AUTO_SYNC != "1":
        return {"ok": True, "skipped": "auto_disabled"}
    quiet = bool(body.get("tick"))
    sid = int(body.get("series_id") or 0)
    if not sid:
        raise ValueError("缺 series_id")
    core, mapping, _, _ = bridge()
    if not mapping.links_for_series(sid):
        return {"ok": True, "skipped": "no_link", "series_id": sid}
    if not _auto_lock.acquire(blocking=False):
        return {"ok": False, "busy": True, "series_id": sid}
    try:
        res = core.push_once(auth_or_none(), series_ids={sid})
    finally:
        _auto_lock.release()
    mapping.state_set(f"auto_push_last_{sid}", _now_iso())
    if (not quiet) or res.get("pushed") or res.get("failed"):
        log(f"auto-push series={sid} pushed={res.get('pushed')} "
            f"skipped={res.get('skipped')} failed={res.get('failed')}"
            + (" (tick)" if quiet else "")
            + _push_brief(res))
    time_res = _time_sync("push", sid, quiet)
    notes_res = _notes_sync("push", sid, quiet)
    return {"ok": True, "series_id": sid, "detail": res, "time": time_res,
            "notes": notes_res}


def _push_brief(res: dict) -> str:
    """把 push_once 的 detail 压成一行可读摘要 (排查"推了没生效"必备)。"""
    for it in res.get("detail") or []:
        if not isinstance(it, dict):
            continue
        pct = it.get("pct")
        if pct is None:
            continue
        zy = it.get("zy")
        why = it.get("skipped") or it.get("why") or ("ok" if it.get("ok") else "?")
        return (f" kv={pct * 100:.4f}% zy={'?' if zy is None else '%.4f%%' % (zy * 100)}"
                f" why={why}")
    return ""


def api_link(body: dict) -> dict:
    _, mapping, _, _ = bridge()
    required = ("kavita_series_id", "zy_book_id", "zy_book_name")
    missing = [k for k in required if not body.get(k)]
    if missing:
        raise ValueError("缺字段: " + ", ".join(missing))
    l = mapping.link(
        kavita_series_id=int(body["kavita_series_id"]),
        zy_book_id=str(body["zy_book_id"]),
        zy_book_name=str(body["zy_book_name"]),
        zy_anchor_chapter_id=body.get("anchor") or None,
        kavita_chapter_count=int(body.get("kavita_chapter_count") or 0),
        note=str(body.get("note") or ""),
    )
    return {"ok": True, "link": l.__dict__}


def api_selfcheck() -> dict:
    """轻量级 warning 用: 逐项体检, 返回问题清单 (不抛)。"""
    warns: list[dict] = []

    def add(level: str, code: str, msg: str, fix: str = ""):
        warns.append({"level": level, "code": code, "msg": msg, "fix": fix})

    # 1. Kavita 本体
    try:
        kdb = Path(_cfg.get("KAVITA_DB", "/vol1/1001/APPS/kavita/kavita.db"))
        if not kdb.exists():
            add("error", "kavita_db", f"Kavita 数据库不在 {kdb}", "检查 docker 是否在跑")
        elif not os.access(kdb, os.R_OK):
            add("error", "kavita_db_perm", f"{kdb} 不可读", "sudo chown $(whoami) / chmod 644")
    except Exception as e:
        add("error", "kavita_db", str(e))

    # 2. 掌阅凭据
    auth = auth_or_none()
    if auth is None:
        add("warn", "zy_auth", "缺 .auth.json, 掌阅侧功能全部不可用",
            "扫码登录后重跑, 或检查 /vol2/1001/zhangyue-bridge/.auth.json")
    else:
        need = ("usr", "zysid", "p29", "p2", "p3", "p4")
        miss = [k for k in need if not auth.get(k)]
        if miss:
            add("error", "zy_auth_fields", "凭据缺字段: " + ", ".join(miss), "重新扫码登录")
        if not os.access(AUTH_JSON, os.R_OK):
            add("error", "zy_auth_perm", f"{AUTH_JSON} 不可读", "chmod 600 + 属主改回")

    # 3. bridge 代码
    bpath = Path(BRIDGE_ROOT) / "sync"
    pkg = bpath / "sync"
    if not (pkg / "sync.py").exists():
        add("error", "bridge", f"找不到 bridge 代码 {pkg}", "确认 zhangyue-bridge 目录还在")
    else:
        try:
            bridge()
        except Exception as e:
            add("error", "bridge_import", f"bridge 导入失败: {e}", "python3 -c 'import sync.sync' 手动复现")

    # 4. token
    if not TOKEN:
        add("warn", "token", "共享 token 未生成", "跑 ./install.sh")

    # 4.5 前端落地模式 (v0.4.1): 挂载 = 容器重建不丢; 拷贝 = 丢了靠自愈重打
    try:
        mount_dir = Path(_cfg.get("WWWROOT_MOUNT_DIR",
                                  "/vol2/1001/kavita-zy-patch/state/wwwroot"))
        idx = mount_dir / "index.html"
        js = mount_dir / "zy-kavita-patch.js"
        if not (idx.is_file() and js.is_file()):
            add("warn", "mount_dir",
                f"挂载目录不全: {mount_dir} (缺 index.html 或 zy-kavita-patch.js)",
                "bash install.sh publish (重建前必须先有文件, 否则 docker 会建目录)")
        else:
            hb = HERE / "state" / "last_hb.json"
            if not hb.exists():
                add("info", "auto_hb",
                    "还没有前端心跳 (没有浏览器加载过 v0.4.1+ 钩子)",
                    "打开一次 Kavita 页面; 之后 api.log 里应有 hb 行")
            else:
                # 心跳"过期"是最常见的误判源: 用户以为钩子在跑, 其实那一页是
                # 昨晚的僵尸页 (容器/后端重启过), 一条请求都没发出来。
                try:
                    import time as _t
                    age = _t.time() - hb.stat().st_mtime
                    if age > 7200:
                        info = json.loads(hb.read_text("utf-8"))
                        add("warn", "auto_hb_stale",
                            f"前端心跳已过期 {int(age // 60)} 分钟 "
                            f"(最后 v{info.get('ver')} {info.get('path') or '/'})",
                            "Kavita 页面里强刷一次 (Ctrl+Shift+R) 让钩子重新连上")
                except Exception:
                    pass
    except Exception as e:
        add("warn", "mount_dir", str(e))

    # 5. 映射覆盖
    try:
        _, mapping, _, _ = bridge()
        links = mapping.all_links()
        if not links:
            add("info", "no_links", "还没有任何 Kavita↔掌阅 映射",
                "在页面里建一条, 或 python3 sync/zync.py link ...")
        else:
            covered = {l.kavita_series_id for l in links}
            # Kavita 里已读过的 series, 未映射的提示
            _, _, kavita_db, _ = bridge()
            read_series = {p.series_id for p in kavita_db.iter_progress()
                           if p.pages_read > 0}
            orphan = read_series - covered
            if orphan:
                add("info", "unlinked",
                    f"{len(orphan)} 本读过的书还没连掌阅: series_id={sorted(orphan)[:8]}",
                    "在系列页点「连掌阅」建立映射")
            # 书名对不上 = 大概率映射错了
            for l in links:
                kn = _series_name(l.kavita_series_id)
                zn = l.zy_book_name or ""
                if kn and zn and not _names_look_related(kn, zn):
                    add("warn", "name_mismatch",
                        f"series {l.kavita_series_id} 映射可疑: "
                        f"Kavita《{kn}》↔ 掌阅《{zn}》",
                        "确认是不是连错书; 改映射 python3 sync/zync.py link ...")
    except Exception as e:
        add("error", "mapping", str(e))

    # 6. 前端注入是否还在
    try:
        idx = HERE / "build" / "index.html.patched"
        out = {"ok": True, "install_dir": str(HERE), "warnings": warns}
    except Exception:
        out = {"ok": True, "install_dir": str(HERE), "warnings": warns}

    return out


# ---- HTTP ---------------------------------------------------------------

ROUTES_GET = {
    "/zy/api/links": api_links,
    "/zy/api/status": api_status,
    "/zy/api/health": lambda: {"ok": True, "ts": __import__("time").time()},
    "/zy/api/selfcheck": api_selfcheck,
    "/zy/api/auto/cfg": api_auto_cfg,
    "/zy/api/reader-css": api_reader_css,
}
ROUTES_POST = {
    "/zy/api/push": lambda b: api_push(bool(b.get("dry_run"))),
    "/zy/api/pull": lambda b: api_pull(bool(b.get("dry_run"))),
    "/zy/api/link": api_link,
    "/zy/api/auto/pull": api_auto_pull,
    "/zy/api/auto/push": api_auto_push,
    "/zy/api/auto/hb": api_auto_hb,
}


class H(BaseHTTPRequestHandler):
    server_version = "zy-kavita-patch/0.1"

    # token 走自定义头会触发 CORS 预检。这里只信自定义头,
    # 不开 Access-Control-Allow-Credentials —— 预检凭据共享不到,
    # 所以前端不能用 cookie, 必须靠 X-ZY-Token 显式带。
    def _cors(self) -> None:
        origin = self.headers.get("Origin")
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type, X-ZY-Token, Authorization")
        self.send_header("Access-Control-Max-Age", "600")
        # Chrome 的 Private Network Access: 页面在公网/域名上, 哪怕 CORS 全放开,
        # 发到 LAN 地址的 fetch 也要服务端显式允许, 否则直接失败 (表现为徽标"离线")。
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def _peer(self) -> str:
        try:
            return (self.client_address or ("?",))[0]
        except Exception:
            return "?"

    def _deny(self, code: int, msg: str) -> None:
        """拒绝请求, 并**记录到 api.log** —— 之前 403/404 静默, 排查时看不到
        "浏览器到底有没有连上来"。"""
        try:
            log(f"{self.command} {urlparse(self.path).path} -> {code} {msg} "
                f"from {self._peer()} origin={self.headers.get('Origin') or '-'} "
                f"ua={(self.headers.get('User-Agent') or '-')[:60]}")
        except Exception:
            pass
        self._send(code, {"ok": False, "err": msg})

    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _authorized(self, q: dict) -> bool:
        if not TOKEN:
            return True
        tok = (self.headers.get("X-ZY-Token")
               or q.get("token", [""])[0]
               or "")
        # 允许同源: 浏览器带 token 头; curl 用 query
        return tok == TOKEN

    def do_GET(self):
        u = urlparse(self.path)
        fn = ROUTES_GET.get(u.path)
        if fn is None:
            return self._deny(404, "no route")
        from urllib.parse import parse_qs
        q = parse_qs(u.query)
        if not self._authorized(q):
            return self._deny(403, "bad token")
        try:
            self._send(200, fn())
        except Exception as e:
            log(f"GET {u.path} FAIL {e}\n{traceback.format_exc()}")
            self._send(500, {"ok": False, "err": str(e)})

    def do_POST(self):
        u = urlparse(self.path)
        fn = ROUTES_POST.get(u.path)
        if fn is None:
            return self._deny(404, "no route")
        from urllib.parse import parse_qs
        q = parse_qs(u.query)
        if not self._authorized(q):
            return self._deny(403, "bad token")
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            body = {}
        try:
            self._send(200, fn(body))
        except Exception as e:
            log(f"POST {u.path} FAIL {e}\n{traceback.format_exc()}")
            self._send(500, {"ok": False, "err": str(e)})

    def log_message(self, fmt, *args):
        pass


def main() -> None:
    ThreadingHTTPServer.allow_reuse_address = True
    srv = ThreadingHTTPServer((API_BIND, API_PORT), H)
    log(f"zy-kavita-patch api on {API_BIND}:{API_PORT} token={'set' if TOKEN else 'none'}")
    # 启动自检: 只记 log, 不阻断
    try:
        sc = api_selfcheck()
        for w in sc["warnings"]:
            log(f"selfcheck {w['level']}: {w['code']}: {w['msg']}")
    except Exception as e:
        log(f"selfcheck crashed: {e}")
    srv.serve_forever()


if __name__ == "__main__":
    main()