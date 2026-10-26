"""掌阅桥接 Web 层 — stdlib http.server, 零依赖.

设计 (用户 2026-10-21 拍板):
    浏览器 <-> 本机 5012  <--subprocess--> sync/zync.py (CLI)
    浏览器 **不碰** 掌阅。所有状态从本地 state/sync.sqlite 读。

安全边界:
    - 绑 127.0.0.1; 若要公网走 nginx / 花生壳端口映射再反代
    - 写操作要求 X-ZY-Token (config 里 ZY_WEB_TOKEN, 默认空=不校验)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BRIDGE_ROOT = Path("/vol2/1001/zhangyue-bridge")
SYNC_DIR = BRIDGE_ROOT / "sync"
SYNC_ROOT = SYNC_DIR / "sync"
STATE_DB = BRIDGE_ROOT / "state" / "sync.sqlite"
LOG_DIR = BRIDGE_ROOT / "state" / "logs"
ZYNC = SYNC_DIR / "zync.py"

# Kavita 改造 (前端注入 / 重排版 / 重扫) 的宿主
PATCH_ROOT = Path(os.environ.get("ZY_PATCH_ROOT", "/vol2/1001/kavita-zy-patch"))
KAVITA_URL = os.environ.get("ZY_KAVITA_URL", "http://127.0.0.1:5000")
PATCH_API_PORT = int(os.environ.get("ZY_PATCH_API_PORT", "5111"))
# 补丁后端 (kavita-zy-api) 的本地地址。/zy/api/* 走本进程反代 —— 这样浏览器
# 只需要能访问 5012 这一个端口, 不必再给 5111 单独开端口映射 (2026-10-24)
PATCH_API = os.environ.get("ZY_PATCH_API", f"http://127.0.0.1:{PATCH_API_PORT}")
PATCH_PROXY_TIMEOUT = float(os.environ.get("ZY_PATCH_PROXY_TIMEOUT", "120"))
KAVITA_CONTAINER = os.environ.get("ZY_KAVITA_CONTAINER", "kavita")

# 让 web.py 能 import sync.* 与 qr (直接 python3 web.py 跑时 PYTHONPATH 没设)
sys.path.insert(0, str(SYNC_ROOT))
sys.path.insert(0, str(SYNC_DIR))

HOST = os.environ.get("ZY_WEB_HOST", "0.0.0.0")
PORT = int(os.environ.get("ZY_WEB_PORT", "5012"))
TOKEN = os.environ.get("ZY_WEB_TOKEN", "")
CLI_TIMEOUT = int(os.environ.get("ZY_CLI_TIMEOUT", "180"))

# 同一时刻只允许一个 CLI 跑 (push 会写 kavita.db, 并发会打架)
_run_lock = threading.Lock()
_last_run: dict = {"cmd": None, "rc": None, "at": None, "ms": None}


# ---- 状态读取 (直连 sqlite, 不跑 CLI) ------------------------------

def _read_state() -> dict:
    """从本地 sqlite 读映射 + 上次同步时间. 不触网."""
    import sqlite3
    out = {"links": [], "last_push": None, "last_pull": None, "error": None}
    try:
        c = sqlite3.connect(f"file:{STATE_DB}?mode=ro", uri=True, timeout=5)
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT kavita_series_id, kavita_chapter_id, zy_book_id, zy_book_name, "
            "zy_anchor_chapter_id, kavita_chapter_count, note, updated_utc "
            "FROM book_link ORDER BY kavita_series_id, kavita_chapter_id"
        ).fetchall()
        for r in rows:
            out["links"].append(dict(r))
        st = {r["key"]: r["value"] for r in
              c.execute("SELECT key, value FROM sync_state").fetchall()}
        out["last_push"] = st.get("push_last_run_utc")
        out["last_pull"] = st.get("pull_last_run_utc")
        c.close()
    except Exception as e:  # noqa: BLE001 — 状态页不能因 db 问题整个挂掉
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def _tail_log(n: int = 60) -> list[dict]:
    """读 sync.log 尾部若干行, 给前端展示最近一次干了什么."""
    log = LOG_DIR / "sync.log"
    if not log.exists():
        return []
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for line in lines[-n:]:
        parts = line.split(" ", 2)
        if len(parts) == 3 and parts[1] in ("INFO", "ERROR", "WARNING", "DEBUG"):
            out.append({"ts": parts[0], "level": parts[1], "msg": parts[2]})
        else:
            out.append({"ts": "", "level": "RAW", "msg": line})
    return out


# ---- Kavita 改造: 状态 / 一键动作 ------------------------------------
#
# Kavita 本体不改代码, 改造只有三处 (全在 patch 仓里):
#   1. 前端注入  容器 overlay 的 wwwroot/index.html 末尾加 <script>
#   2. 数据直写  /vol1/1001/APPS/kavita/kavita.db 的 AppUserProgresses
#   3. 库文件替换 epub 重排版成"等字节片", 再调 /api/series/scan
# 容器重建 (pull 新镜像) 会清掉 overlay → 第 1 项失效, 其余两项跟卷走。

_kv_lock = threading.Lock()


def _run_cmd(argv: list[str], *, timeout: int, cwd: Path | None = None) -> dict:
    t0 = time.time()
    try:
        p = subprocess.run(argv, cwd=str(cwd or PATCH_ROOT), capture_output=True,
                           text=True, timeout=timeout)
        rc, out, err = p.returncode, p.stdout or "", p.stderr or ""
    except subprocess.TimeoutExpired:
        rc, out, err = 124, "", f"超时 {timeout}s"
    except Exception as e:  # noqa: BLE001
        rc, out, err = 1, "", f"{type(e).__name__}: {e}"
    return {"rc": rc, "stdout": out[-8000:], "stderr": err[-4000:],
            "ms": int((time.time() - t0) * 1000)}


def _http_code(url: str, *, headers: dict | None = None, timeout: int = 5) -> int:
    import urllib.request
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:  # noqa: BLE001 — 连不上就当 000
        return 0


def _http_json(url: str, *, headers: dict | None = None, timeout: int = 8):
    import urllib.request
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _patch_token() -> str:
    f = PATCH_ROOT / "state" / "token"
    try:
        return f.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _patch_version() -> str:
    try:
        for line in (PATCH_ROOT / "patch.conf").read_text(encoding="utf-8").splitlines():
            if line.startswith("PATCH_VERSION="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return "?"


# ---- 自愈 / 自启 状态 -------------------------------------------------

HEAL_STATE = PATCH_ROOT / "state" / "heal.json"
HEAL_LOG = PATCH_ROOT / "state" / "logs" / "heal.log"
HEAL_DISABLED = PATCH_ROOT / "state" / "heal.disabled"
AUTOSTART_UNITS = [
    "kavita-zy-api.service",
    "kavita-zy-web.service",
    "kavita-zy-heal.service",
    "kavita-zy-heal.timer",
]


def _heal_state() -> dict:
    """最近一次自愈结果 + 是否被停用."""
    out: dict = {"disabled": HEAL_DISABLED.exists(), "last": None,
                 "log_tail": [], "heal_script": str(PATCH_ROOT / "deploy" / "heal.sh")}
    try:
        out["last"] = json.loads(HEAL_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    try:
        lines = HEAL_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        out["log_tail"] = lines[-60:]
    except OSError:
        pass
    return out


def _unit_state(unit: str) -> dict:
    def q(prop: str) -> str:
        try:
            p = subprocess.run(["systemctl", prop, unit], capture_output=True,
                               text=True, timeout=8)
            return (p.stdout or "").strip() or "-"
        except Exception:  # noqa: BLE001
            return "-"
    return {"unit": unit, "enabled": q("is-enabled"), "active": q("is-active")}


def _cron_state() -> dict:
    """用户级自启 (crontab 受管块) 是否装了 + cron 服务活着吗."""
    out = {"installed": False, "lines": 0, "service": "-"}
    try:
        p = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=8)
        txt = p.stdout or ""
        out["lines"] = txt.count("kavita-zy")
        out["installed"] = "# >>> kavita-zy" in txt
    except Exception:  # noqa: BLE001
        pass
    try:
        p = subprocess.run(["systemctl", "is-active", "cron"], capture_output=True,
                           text=True, timeout=8)
        out["service"] = (p.stdout or "").strip() or "-"
    except Exception:  # noqa: BLE001
        pass
    return out


def _autostart_state() -> dict:
    units = [_unit_state(u) for u in AUTOSTART_UNITS]
    installed = sum(1 for u in units if u["enabled"] not in ("-", "disabled", "not-found"))
    cron = _cron_state()
    return {"units": units, "installed": installed, "total": len(units),
            "all_installed": installed == len(units), "cron": cron}


def _kavita_status() -> dict:
    """改造清单: 每一项 [id, label, ok, detail, fix]。前端只读渲染。"""
    tok = _patch_token()
    checks: list[dict] = []

    # 1. 容器
    p = subprocess.run(["docker", "inspect", KAVITA_CONTAINER, "--format",
                        "{{.State.Status}}|{{.Config.Image}}|{{.Created}}"],
                       capture_output=True, text=True, timeout=20)
    if p.returncode == 0 and p.stdout.strip():
        status, image, created = (p.stdout.strip().split("|") + ["", "", ""])[:3]
        checks.append({"id": "container", "label": "Kavita 容器",
                       "ok": status == "running",
                       "detail": f"{KAVITA_CONTAINER} · {status} · {image} · 建于 {created[:19]}",
                       "fix": "docker start kavita"})
        created_at = created
    else:
        checks.append({"id": "container", "label": "Kavita 容器", "ok": False,
                       "detail": f"找不到容器 {KAVITA_CONTAINER}",
                       "fix": "docker compose up -d"})
        created_at = ""

    # 2. 前端资源 + 注入
    asset = _http_code(f"{KAVITA_URL}/zy-kavita-patch.js")
    injected = False
    try:
        import urllib.request
        with urllib.request.urlopen(f"{KAVITA_URL}/", timeout=6) as r:
            head = r.read(400_000).decode("utf-8", "replace")
        injected = "zy-kavita-patch.js" in head
    except Exception:  # noqa: BLE001
        pass
    checks.append({"id": "asset", "label": "前端资源 zy-kavita-patch.js",
                   "ok": asset == 200, "detail": f"HTTP {asset}",
                   "fix": "点「注入 / 重打」"})
    checks.append({"id": "inject", "label": "index.html 已注入",
                   "ok": injected, "detail": "有 <script> 引用" if injected else "没有引用",
                   "fix": "点「注入 / 重打」"})

    # 3. 补丁后端
    api_ok, api_detail = False, "连不上"
    if tok:
        try:
            j = _http_json(f"http://127.0.0.1:{PATCH_API_PORT}/zy/api/health",
                           headers={"X-ZY-Token": tok})
            api_ok, api_detail = bool(j.get("ok")), f"pid 活 · {PATCH_API_PORT}"
        except Exception as e:  # noqa: BLE001
            api_detail = f"{type(e).__name__}"
    checks.append({"id": "patch_api", "label": f"补丁后端 :{PATCH_API_PORT}",
                   "ok": api_ok, "detail": api_detail, "fix": "点「重启补丁后端」"})

    # 4. token
    checks.append({"id": "token", "label": "共享 token",
                   "ok": bool(tok), "detail": "state/token 存在" if tok else "缺失",
                   "fix": "点「注入 / 重打」(install 会生成)"})

    # 5. kavita.db 可读 (进度直写的先决条件)
    db_ok, db_detail = False, ""
    try:
        import sqlite3
        c = sqlite3.connect(f"file:{os.environ.get('ZY_KAVITA_DB', '/vol1/1001/APPS/kavita/kavita.db')}?mode=ro", uri=True)
        n = c.execute("select count(*) from Series").fetchone()[0]
        c.close()
        db_ok, db_detail = True, f"{n} series 可读"
    except Exception as e:  # noqa: BLE001
        db_detail = f"{type(e).__name__}: {e}"
    checks.append({"id": "kavita_db", "label": "kavita.db 可读写",
                   "ok": db_ok, "detail": db_detail,
                   "fix": "setfacl -m u:claud:rw /vol1/1001/APPS/kavita/kavita.db"})

    # 6. 映射 + 重排版
    st = _read_state()
    n_links = len(st.get("links") or [])
    checks.append({"id": "links", "label": "书籍映射",
                   "ok": n_links > 0, "detail": f"{n_links} 本已连掌阅",
                   "fix": "CLI: zync.py link --kavita-series-id N --zy-book-id ..."})
    try:
        sidecars = sorted((BRIDGE_ROOT / "state" / "kpages").glob("*.json"))
    except OSError:
        sidecars = []
    checks.append({"id": "edition", "label": "重排版 (页=等字节片)",
                   "ok": bool(sidecars), "detail": f"{len(sidecars)} 本已重排",
                   "fix": "点「重排版 dry-run」先自检, 再「重排版执行」"})

    zy_ok = False
    try:
        from sync import zy_auth
        zy_ok = bool(zy_auth.auth_summary().get("authenticated"))
    except Exception:  # noqa: BLE001
        pass
    checks.append({"id": "zy_auth", "label": "掌阅凭据",
                   "ok": zy_ok, "detail": "已认证" if zy_ok else "未认证/已失效",
                   "fix": "上方「刷新二维码」扫码"})

    # 7. 前端资产版本 (浏览器拿到的 JS 与 patch.conf 是否同版)
    want = _patch_version()
    have = ""
    try:
        import urllib.request
        with urllib.request.urlopen(f"{KAVITA_URL}/zy-kavita-patch.js", timeout=6) as r:
            head = r.read(200).decode("utf-8", "replace")
        for tok_ in head.replace("*", " ").replace("/", " ").split():
            if tok_.startswith("v") and tok_[1:2].isdigit():
                have = tok_[1:]
                break
    except Exception:  # noqa: BLE001
        pass
    checks.append({"id": "patch_ver", "label": "前端资产版本",
                   "ok": bool(have) and have == want,
                   "detail": f"资产 v{have or '?'} · 期望 v{want}",
                   "fix": "点「注入 / 重打」"})

    # 8. 自启动 (systemd unit 或 用户 crontab, 二者其一即可)
    auto = _autostart_state()
    cron = auto["cron"]
    auto_ok = auto["all_installed"] or cron["installed"]
    if auto["all_installed"]:
        auto_detail = f"systemd {auto['installed']}/{auto['total']} unit enabled"
    elif cron["installed"]:
        auto_detail = f"crontab 受管块已装 (cron={cron['service']}, 每 3 分钟巡检)"
    else:
        auto_detail = f"systemd {auto['installed']}/{auto['total']} · crontab 未装"
    checks.append({"id": "autostart", "label": "开机自启 + 定时巡检",
                   "ok": auto_ok, "detail": auto_detail,
                   "fix": "点「装自启 (免 root)」写 crontab; 或 sudo bash deploy/install-autostart.sh"})

    # 9. 自愈 (定时检查 + 自动补注入)
    heal = _heal_state()
    last = heal.get("last") or {}
    if heal["disabled"]:
        heal_ok, heal_detail = False, "已停用 (state/heal.disabled)"
    elif not last:
        heal_ok, heal_detail = False, "还没跑过 · 点「立即自检」"
    else:
        heal_ok = bool(last.get("ok"))
        heal_detail = f"上次 {str(last.get('ts') or '')[:19].replace('T', ' ')} · " + \
                      ("正常" if heal_ok else f"{last.get('problems')} 项异常") + \
                      (f" · 修复过 {last.get('actions')}" if last.get("actions") else "")
    checks.append({"id": "heal", "label": "自愈巡检",
                   "ok": heal_ok, "detail": heal_detail,
                   "fix": "点「立即自检」看输出; 关/开自愈见下方按钮"})

    return {"version": _patch_version(), "container": KAVITA_CONTAINER,
            "created": created_at, "kavita_url": KAVITA_URL,
            "checks": checks, "all_ok": all(c["ok"] for c in checks),
            "autostart": auto, "heal": {k: v for k, v in heal.items() if k != "log_tail"},
            "params": [
                ["patch.conf", "PATCH_VERSION", _patch_version()],
                ["patch.conf", "CONTAINER", KAVITA_CONTAINER],
                ["patch.conf", "KAVITA_URL", KAVITA_URL],
                ["patch.conf", "API_PORT", str(PATCH_API_PORT)],
                ["patch.conf", "WEB_PORT", str(PORT)],
                ["patch.conf", "TOKEN_FILE", str(PATCH_ROOT / "state" / "token")],
                ["patch.conf", "BRIDGE_ROOT", str(BRIDGE_ROOT)],
                ["crontab", "巡检间隔", "每 3 分钟 (deploy/install-cron.sh)"],
            ]}


_KV_ACTIONS: dict[str, tuple[list[str], int]] = {
    # 注入前端 + 生成 token + 启后端 (幂等, 容器重建后重跑这一条就够)
    "install": (["bash", "install.sh", "install"], 300),
    "reapply": (["bash", "deploy/reapply.sh"], 300),
    "uninstall": (["bash", "install.sh", "uninstall"], 180),
    "restart-api": (["bash", "ctl.sh", "restart-api"], 180),
    # 自愈: 一次完整巡检 (容器 / 注入 / 5111 / 5012)
    "heal": (["bash", "deploy/heal.sh"], 360),
    # 自启动 (需要 root; 用 sudo -n, 没免密就直接失败并提示手跑命令)
    "autostart-install": (["sudo", "-n", "bash", "deploy/install-autostart.sh"], 300),
    "autostart-uninstall": (["sudo", "-n", "bash", "deploy/uninstall-autostart.sh"], 180),
    # 用户级自启 (不需要 root): 写当前用户 crontab 受管块
    "cron-install": (["bash", "deploy/install-cron.sh"], 60),
    "cron-uninstall": (["bash", "deploy/uninstall-cron.sh"], 60),
    # 把已映射的书按"等字节页"重新排版并换进库里, 再重扫
    "edition-dry": (["/usr/bin/env", "python3", "tools/apply_edition.py", "--all", "--dry-run"], 600),
    "edition-all": (["/usr/bin/env", "python3", "tools/apply_edition.py", "--all"], 2400),
}


def _kavita_bootstrap() -> dict:
    """新装 Kavita 的一键改造: 注入 -> 起后端 -> 装自启 -> 自检.

    每一步都幂等, 失败就停并回显已跑部分。自启那步需要 root, 没免密会给出
    可以手抄的命令 (不影响其余步骤)。
    """
    steps: list[dict] = []

    def step(name: str, argv: list[str], timeout: int) -> bool:
        r = _run_cmd(argv, timeout=timeout, cwd=PATCH_ROOT)
        r["step"] = name
        steps.append(r)
        return r["rc"] == 0

    ok = step("注入 / 重打", ["bash", "install.sh", "install"], 300)
    if ok:
        step("自愈巡检", ["bash", "deploy/heal.sh"], 360)
    cron_ok = step("装自启 (crontab)", ["bash", "deploy/install-cron.sh"], 60)
    sysd = _run_cmd(["sudo", "-n", "bash", "deploy/install-autostart.sh"], timeout=300,
                    cwd=PATCH_ROOT)
    sysd["step"] = "装自启 (systemd, 可选)"
    steps.append(sysd)
    hint = ""
    if not cron_ok and sysd["rc"] != 0:
        hint = ("自启没装上。手动跑一次 (免 root 那条就够):\n"
                f"  bash {PATCH_ROOT}/deploy/install-cron.sh\n"
                "有 root 想更稳 (崩溃自动重启) 再加:\n"
                f"  sudo bash {PATCH_ROOT}/deploy/install-autostart.sh")
    elif sysd["rc"] != 0 and cron_ok:
        hint = ("crontab 版已生效。systemd 版需要 root, 想升级再跑一次:\n"
                f"  sudo bash {PATCH_ROOT}/deploy/install-autostart.sh")
    return {"rc": 0 if ok else steps[-1]["rc"],
            "steps": [{k: s.get(k) for k in ("step", "rc", "ms", "stdout", "stderr")}
                      for s in steps],
            "hint": hint}


def _kavita_action(action: str) -> dict:
    if action == "selfcheck":
        tok = _patch_token()
        try:
            return {"rc": 0, "stdout": "", "stderr": "",
                    "json": _http_json(f"http://127.0.0.1:{PATCH_API_PORT}/zy/api/selfcheck",
                                       headers={"X-ZY-Token": tok})}
        except Exception as e:  # noqa: BLE001
            return {"rc": 1, "stdout": "", "stderr": f"{type(e).__name__}: {e}"}
    if action == "bootstrap":
        return _kavita_bootstrap()
    if action in ("heal-off", "heal-on"):
        try:
            if action == "heal-off":
                HEAL_DISABLED.parent.mkdir(parents=True, exist_ok=True)
                HEAL_DISABLED.write_text("off\n", encoding="utf-8")
            else:
                HEAL_DISABLED.unlink(missing_ok=True)
        except OSError as e:
            return {"rc": 1, "stdout": "", "stderr": f"{type(e).__name__}: {e}"}
        return {"rc": 0, "stdout": "自愈已关闭\n" if action == "heal-off" else "自愈已开启\n",
                "stderr": ""}
    spec = _KV_ACTIONS.get(action)
    if not spec:
        return {"rc": 2, "stdout": "", "stderr": f"unknown action: {action}"}
    argv, timeout = spec
    cwd = PATCH_ROOT if action.startswith(
        ("install", "reapply", "uninstall", "restart", "heal", "autostart", "cron")) else SYNC_DIR
    out = _run_cmd(argv, timeout=timeout, cwd=cwd)
    if action.startswith("autostart") and out["rc"] != 0:
        out["stdout"] = (out.get("stdout") or "") + (
            "\n需要 root 且没有免密 sudo。手动跑一次:\n"
            f"  sudo bash {PATCH_ROOT}/deploy/"
            f"{'install' if action.endswith('install') else 'uninstall'}-autostart.sh\n")
    return out


# ---- 掌阅认证 (扫码登录) ---------------------------------------------
#
# 用户 2026-10-21: 公网面板要能直接刷二维码重新认证, 不再回 PC 站。
# 链路 (zy_auth.py 已封装端点):
#   GET qrcode/get      -> code
#   GET qrcode/result   -> 未扫 code=30059 / 扫完 body={usr, zysid}
# 二维码内容 = "web_login:" + code (App 认这个前缀)

_qr_lock = threading.Lock()
_qr_state: dict = {"code": None, "issued": None, "status": "idle", "auth": None}


def _auth_state() -> dict:
    """当前凭据状态 + 未完成的扫码会话."""
    from sync import zy_auth
    out = dict(zy_auth.auth_summary())
    out["qr"] = {k: v for k, v in _qr_state.items() if k != "auth"}
    return out


def _auth_refresh() -> dict:
    """重读 .auth.json (认证成功后前端调)。"""
    from sync import zy_auth
    a = zy_auth.auth_summary()
    with _qr_lock:
        if a.get("authenticated"):
            _qr_state["status"] = "ok"
    return a


def _qr_issue() -> dict:
    """领一个新二维码会话。旧的作废。"""
    from sync import zy_auth
    global _qr_state
    with _qr_lock:
        if _qr_state["status"] == "waiting":
            return {"ok": True, "already": True, "code": _qr_state["code"],
                    "issued": _qr_state["issued"], "svg": _qr_state["svg"]}
        code = zy_auth.new_qr()
        payload = zy_auth.qr_payload(code)
        import qr as qrgen
        _qr_state = {
            "code": code,
            "payload": payload,
            "issued": time.strftime("%F %T"),
            "status": "waiting",
            "svg": qrgen.to_svg(payload, scale=6, border=4),
        }
        return {"ok": True, "code": code, "payload": payload,
                "issued": _qr_state["issued"], "svg": _qr_state["svg"]}


def _qr_check() -> dict:
    """查一次扫码状态。前端轮询用。"""
    from sync import zy_auth
    global _qr_state
    with _qr_lock:
        code = _qr_state["code"]
        if not code:
            return {"status": "idle"}
        r = zy_auth.poll_once(code)
        if r["status"] == "ok":
            a = zy_auth.save_auth(r["auth"])
            _qr_state["status"] = "ok"
            _qr_state["code"] = None
            return {"status": "ok", "usr": a.get("usr"),
                    "summary": zy_auth.auth_summary()}
        if r["status"] == "invalid":
            _qr_state["status"] = "invalid"
            _qr_state["code"] = None
        return {"status": _qr_state["status"], "msg": r.get("msg", ""),
                "code": code, "issued": _qr_state["issued"]}


# ---- CLI 调用 --------------------------------------------------------

def _run_cli(args: list[str]) -> dict:
    """跑 zync.py, 返回 {rc, stdout, stderr, ms}."""
    t0 = time.time()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SYNC_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    try:
        p = subprocess.run(
            [sys.executable, str(ZYNC), *args],
            cwd=str(SYNC_DIR), env=env,
            capture_output=True, text=True, timeout=CLI_TIMEOUT,
        )
        rc, so, se = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        rc, so, se = -9, "", f"timeout after {CLI_TIMEOUT}s"
    except Exception as e:  # noqa: BLE001
        rc, so, se = -1, "", f"{type(e).__name__}: {e}"
    ms = int((time.time() - t0) * 1000)
    _last_run.update({"cmd": " ".join(args), "rc": rc, "at": time.strftime("%F %T"), "ms": ms})
    return {"rc": rc, "stdout": so[-8000:], "stderr": se[-8000:], "ms": ms}


def _try_json(text: str):
    """CLI stdout 里抠 JSON (dict 或 list). 失败返 None (日志会混进 stderr)."""
    i = min((p for p in (text.find("{"), text.find("[")) if p != -1), default=-1)
    if i == -1:
        return None
    j = max(text.rfind("}"), text.rfind("]"))
    if j <= i:
        return None
    try:
        return json.loads(text[i:j + 1])
    except json.JSONDecodeError:
        return None


# ---- HTTP ------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "ZYBridge/1.0"

    def _auth_ok(self) -> bool:
        if not TOKEN:
            return True
        got = self.headers.get("X-ZY-Token", "")
        return got == TOKEN

    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # 浏览器页面在 Kavita (:5000), 调本服务 (:5012) 是跨源 → 必须给 CORS 头,
        # 否则 fetch 直接报错 (X-ZY-Token 属自定义头, 还会触发 OPTIONS 预检)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "X-ZY-Token, Content-Type, Accept")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def do_OPTIONS(self):  # noqa: N802 — 跨源预检
        self._send(204, b"", "text/plain; charset=utf-8")

    # ---- /zy/api/* → 补丁后端 (kavita-zy-api, 默认 :5111) ---------------
    def _proxy_patch(self) -> bool:
        """把 /zy/api/* 原样转给补丁后端。返回 True = 本请求已处理。

        为什么需要: Kavita 的 Kestrel 没法给 SPA 加 /zy/api 反代, 而让浏览器
        再连一个 5111 端口意味着要给每个访问路径 (域名/公网) 额外做端口映射。
        5012 本来就要暴露 (控制台入口), 借它当同源入口最省事。
        """
        if not self.path.startswith("/zy/api/"):
            return False
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else None
        headers = {}
        for k in ("X-ZY-Token", "Content-Type", "Accept"):
            v = self.headers.get(k)
            if v:
                headers[k] = v
        req = urllib.request.Request(PATCH_API + self.path, data=body,
                                     headers=headers, method=self.command)
        try:
            with urllib.request.urlopen(req, timeout=PATCH_PROXY_TIMEOUT) as r:
                data, code = r.read(), r.status
                ctype = r.headers.get("Content-Type") or "application/json; charset=utf-8"
        except urllib.error.HTTPError as e:
            data, code = e.read(), e.code
            ctype = ((e.headers.get("Content-Type") if e.headers else None)
                     or "application/json; charset=utf-8")
        except Exception as e:  # noqa: BLE001 — 后端没起不该把控制台带下去
            self._json(502, {"ok": False,
                             "err": f"补丁后端不可达 ({PATCH_API}): {type(e).__name__}: {e}"})
            return True
        self._send(code, data, ctype)
        return True

    def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler 契约
        path = urllib.parse.urlparse(self.path).path
        if self._proxy_patch():
            return
        if path == "/api/state":
            self._json(200, {**_read_state(), "running": _run_lock.locked(),
                             "last_run": dict(_last_run)})
        elif path == "/api/log":
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            self._json(200, {"lines": _tail_log(int((q.get("n") or ["60"])[0]))})
        elif path == "/api/auth":
            self._json(200, _auth_state())
        elif path == "/api/cloud/books":
            from sync import cloud_books as cb
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            refresh = (q.get("refresh") or ["1"])[0] not in ("0", "false", "no")
            try:
                self._json(200, cb.list_books(refresh=refresh))
            except Exception as e:  # noqa: BLE001 — 掌阅不可达不该让面板 500
                self._json(502, {"error": f"{type(e).__name__}: {e}"})
        elif path == "/api/cloud/local":
            from sync import cloud_books as cb
            self._json(200, {"files": cb.local_files(), "dl_dir": cb.dl_dir()})
        elif path == "/api/cloud/jobs":
            from sync import cloud_books as cb
            self._json(200, {"jobs": cb.jobs_list()})
        elif path == "/api/kavita/status":
            try:
                self._json(200, _kavita_status())
            except Exception as e:  # noqa: BLE001 — 状态页不该整个 500
                self._json(500, {"error": f"{type(e).__name__}: {e}"})
        elif path == "/api/kavita/heal-log":
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            n = max(1, min(500, int((q.get("n") or ["80"])[0])))
            st = _heal_state()
            self._json(200, {"disabled": st["disabled"], "last": st.get("last"),
                             "lines": (st.get("log_tail") or [])[-n:]})
        elif path == "/api/auth/qr":
            self._send(200, (_qr_state.get("svg") or "").encode("utf-8"),
                       "image/svg+xml; charset=utf-8")
        elif path in ("/", "/index.html"):
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/healthz":
            self._json(200, {"ok": True, "port": PORT})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if self._proxy_patch():
            return
        if not self._auth_ok():
            self._json(403, {"error": "bad or missing X-ZY-Token"})
            return
        if path.startswith("/api/auth/"):
            act = path[len("/api/auth/"):]
            try:
                if act == "qr/new":
                    self._json(200, _qr_issue())
                elif act == "qr/check":
                    self._json(200, _qr_check())
                elif act == "refresh":
                    self._json(200, _auth_refresh())
                else:
                    self._json(404, {"error": f"unknown auth action: {act}"})
            except Exception as e:  # noqa: BLE001 — 面板不该整个 500
                self._json(502, {"error": f"{type(e).__name__}: {e}"})
            return
        if path.startswith("/api/cloud/"):
            from sync import cloud_books as cb
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            except Exception:  # noqa: BLE001
                body = {}
            act = path[len("/api/cloud/"):]
            try:
                key = str(body.get("key") or body.get("bookid") or "")
                if act == "download":
                    jid = cb.start_download(key, body.get("dest") or None,
                                            name=body.get("name") or None,
                                            overwrite=bool(body.get("overwrite")),
                                            target=body.get("target") or None,
                                            prep=body.get("prep", True) is not False)
                    self._json(200, {"ok": True, "job": cb.job_status(jid)})
                elif act == "job":
                    j = cb.job_status(str(body.get("id") or ""))
                    self._json(200 if j else 404, j or {"error": "job 不存在"})
                elif act == "move":
                    rec = cb.move_local(key, body.get("dest") or "",
                                        new_name=body.get("name") or None)
                    self._json(200, {"ok": True, "file": rec})
                elif act == "local_delete":
                    self._json(200, cb.delete_local(key, delete_file=bool(body.get("delete_file"))))
                elif act == "delete":
                    if str(body.get("confirm") or "").lower() not in ("yes", "1", "true", "confirm"):
                        self._json(400, {"error": "删除不可逆, 请求里要带 confirm=\"yes\""})
                    else:
                        keys = body.get("keys") or body.get("bookids") or ([key] if key else [])
                        self._json(200, cb.delete_cloud(keys, source=body.get("source") or None))
                elif act == "dir_set":
                    self._json(200, {"ok": True, "dir": cb.set_dl_dir(str(body.get("path") or ""))})
                elif act in ("dir", "dir_get"):
                    self._json(200, {"dir": cb.dl_dir()})
                else:
                    self._json(404, {"error": f"unknown cloud action: {act}"})
            except Exception as e:  # noqa: BLE001 — 面板不能因掌阅/文件错误整页挂
                self._json(502, {"error": f"{type(e).__name__}: {e}"})
            return
        if path == "/api/kavita/action":
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            except Exception:  # noqa: BLE001
                body = {}
            action = str(body.get("action") or "")
            if not _kv_lock.acquire(blocking=False):
                self._json(409, {"error": "另一项 Kavita 改造正在跑, 等它结束"})
                return
            try:
                r = _kavita_action(action)
            finally:
                _kv_lock.release()
            self._json(200 if r.get("rc") == 0 else 500, r)
            return
        if not path.startswith("/api/run/"):
            self._json(404, {"error": "not found"})
            return
        cmd = path[len("/api/run/"):]
        if cmd not in ("push", "pull", "status", "links"):
            self._json(400, {"error": f"unsupported cmd: {cmd}"})
            return
        if not _run_lock.acquire(blocking=False):
            self._json(409, {"error": "已有同步在跑, 等它结束"})
            return
        try:
            r = _run_cli([cmd])
            r["json"] = _try_json(r["stdout"])
        finally:
            _run_lock.release()
        self._json(200 if r["rc"] == 0 else 500, r)

    def log_message(self, fmt, *args):
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_DIR / "web.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%F %T')} {fmt % args}\n")


PAGE = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>掌阅桥接</title>
<style>
:root{--bg:#14161a;--fg:#e8e6e1;--dim:#8b8f98;--line:#2a2e35;--ok:#7fb069;--err:#d97a6c;--acc:#c9a96a;--warn:#d9b06c}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
  font:15px/1.6 -apple-system,"PingFang SC","Noto Sans SC",sans-serif;
  padding:20px 16px calc(20px + env(safe-area-inset-bottom));max-width:720px;margin:auto}
h1{font-size:20px;font-weight:600;margin:0 0 2px}
.sub{color:var(--dim);font-size:13px;margin-bottom:20px}
.row{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:18px}
button{flex:1 1 120px;min-height:44px;background:#22262d;color:var(--fg);
  border:1px solid var(--line);border-radius:8px;font-size:15px;cursor:pointer}
button:active{background:#2b3038}button:disabled{opacity:.45;cursor:default}
.meta{font-size:12px;color:var(--dim);margin-bottom:14px;line-height:1.9}
.card{background:#1b1e24;border:1px solid var(--line);border-radius:10px;
  padding:14px;margin-bottom:12px}
.card h2{font-size:14px;margin:0 0 8px;font-weight:600}
.k{color:var(--dim)}
pre{background:#111316;border:1px solid var(--line);border-radius:8px;padding:10px;
  font-size:12px;overflow:auto;max-height:280px;margin:8px 0 0;white-space:pre-wrap;
  word-break:break-all;color:#c3c7cd}
.lv-ERROR{color:var(--err)}.lv-INFO{color:var(--ok)}.lv-RAW{color:var(--dim)}
table{width:100%;border-collapse:collapse;font-size:13px}
td{padding:6px 4px;border-bottom:1px solid var(--line);vertical-align:top}
td:last-child{text-align:right;white-space:nowrap;color:var(--dim)}
.qrbox{background:#fff;border-radius:10px;padding:10px;display:flex;
  justify-content:center;margin:10px 0}
.qrbox svg{width:100%;max-width:260px;height:auto;display:block}
.auth-ok{color:var(--ok)}.auth-no{color:var(--dim)}.auth-wait{color:var(--acc)}
.pill{display:inline-block;font-size:12px;padding:1px 8px;border-radius:10px;
  border:1px solid var(--line);color:var(--dim)}
.pill.ok{color:var(--ok);border-color:#3c4a35}.pill.err{color:var(--err);border-color:#4a3330}
details.sec{background:#1b1e24;border:1px solid var(--line);border-radius:10px;
  margin-bottom:12px;overflow:hidden}
details.sec>summary{list-style:none;cursor:pointer;padding:12px 14px;font-size:14px;
  font-weight:600;display:flex;align-items:center;gap:8px;user-select:none}
details.sec>summary::-webkit-details-marker{display:none}
details.sec>summary::after{content:"›";margin-left:auto;color:var(--dim);font-size:16px;
  line-height:1;transform:rotate(90deg);transition:transform .15s}
details.sec[open]>summary::after{transform:rotate(-90deg)}
details.sec>summary:hover{background:#20242b}
.sec-body{padding:0 14px 14px}
.cnt{font-size:12px;color:var(--dim);font-weight:400}
.divider{display:flex;align-items:center;gap:10px;color:var(--dim);font-size:12px;
  margin:22px 0 10px}
.divider::before,.divider::after{content:"";flex:1;height:1px;background:var(--line)}
</style></head><body>
<h1>掌阅桥接</h1>
<div class="sub">Kavita ⇄ 掌阅 · 本地 CLI 驱动</div>

<div class="row" style="margin-bottom:10px">
  <button id="b-push">推到掌阅</button>
  <button id="b-pull">从掌阅拉</button>
  <button id="b-refresh">刷新状态</button>
</div>
<div class="meta" id="meta" style="margin-bottom:12px">加载中…</div>

<details class="sec" id="sec-out">
  <summary><span id="out-h">执行结果</span></summary>
  <div class="sec-body"><pre id="out">—</pre></div>
</details>

<details class="sec" id="sec-links">
  <summary>同步映射 <span class="cnt" id="link-cnt">…</span></summary>
  <div class="sec-body" id="links"></div>
</details>

<details class="sec" id="sec-cloud">
  <summary>掌阅个人云书籍 <span class="pill" id="cl-pill">…</span></summary>
  <div class="sec-body">
    <div class="meta" id="cl-meta" style="margin-bottom:8px">加载中…</div>
    <div class="row" style="margin-bottom:8px">
      <input id="cl-dest" placeholder="/vol2/1001/dav/Books/掌阅" style="flex:1;min-width:200px">
      <button id="b-cl-dir-save">保存目录</button>
      <button id="b-cl-refresh">刷新列表</button>
    </div>
    <div class="meta" id="cl-dir-hint" style="margin-bottom:8px">下载与搬移都只落在这个目录（可新建子目录）。</div>
    <div id="cl-list">加载中…</div>
    <div class="meta" id="cl-jobs" style="margin-top:8px"></div>
  </div>
</details>

<details class="sec" id="sec-local">
  <summary>已下载到本地 <span class="cnt" id="local-cnt">…</span></summary>
  <div class="sec-body"><div id="cl-local" class="meta">加载中…</div></div>
</details>

<div class="divider"><span>初始化 / 维护</span></div>

<details class="sec" id="sec-auth">
  <summary>掌阅认证 <span class="pill" id="auth-pill">…</span></summary>
  <div class="sec-body">
    <div class="meta" id="auth-meta" style="margin-bottom:6px">加载中…</div>
    <div class="row" style="margin-bottom:0">
      <button id="b-qr">刷新二维码</button>
      <button id="b-authchk">检查状态</button>
    </div>
    <div id="qr-host"></div>
  </div>
</details>

<details class="sec" id="sec-kv">
  <summary>Kavita 改造 <span class="pill" id="kv-pill">…</span></summary>
  <div class="sec-body">
    <div class="meta" id="kv-meta" style="margin-bottom:8px">加载中…</div>
    <div class="row" style="margin:0 0 6px">
      <button id="b-kv-check">重新检测</button>
      <button id="b-kv-heal">立即自检</button>
      <button id="b-kv-self">体检</button>
    </div>
    <div id="kv-checks"></div>
    <div class="meta" style="margin:12px 0 4px">一次性 / 初始化动作</div>
    <div class="row" style="margin:0 0 6px">
      <button id="b-kv-boot">一键改造 / 新装适配</button>
      <button id="b-kv-install">注入 / 重打</button>
      <button id="b-kv-api">重启补丁后端</button>
    </div>
    <div class="row" style="margin:0 0 6px">
      <button id="b-kv-cron">装自启 (免 root)</button>
      <button id="b-kv-auto">装自启 (systemd)</button>
      <button id="b-kv-heal-off">关自愈</button>
      <button id="b-kv-heal-on">开自愈</button>
    </div>
    <div class="row" style="margin:0 0 6px">
      <button id="b-kv-edry">重排版 dry-run</button>
      <button id="b-kv-edo">重排版执行</button>
    </div>
    <div class="meta" id="kv-auto" style="margin:10px 0 0">自启状态…</div>
    <div class="meta" id="kv-heal" style="margin:6px 0 0">自愈状态…</div>
    <details style="margin-top:8px">
      <summary class="meta" style="cursor:pointer">自愈日志 (最近 60 行)</summary>
      <pre id="kv-heal-log" class="out" style="margin-top:6px;max-height:220px">加载中…</pre>
    </details>
    <details style="margin-top:8px">
      <summary class="meta" style="cursor:pointer">参数 (改 <code>patch.conf</code> 后点「注入 / 重打」生效)</summary>
      <table id="kv-conf" style="margin-top:6px"></table>
    </details>
    <div class="meta" style="margin:10px 0 0">
      容器重建 (换镜像) 会清掉前端注入 —— 其余两项跟卷走。
      重排版会替换库内 epub 并重扫, 原书归档在 <code>state/backup/</code>。<br>
      <b>Kavita 里看不到「掌阅同步」?</b> 先 <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>R</kbd> 强刷
      (index.html 被浏览器缓存过就会这样); 入口现在有两个 —— 顶栏右侧图标+文字,
      和左侧抽屉里的「掌阅同步」。也可以直接开
      <a id="kv-console-link" href="/" target="_blank">本控制台</a>。
    </div>
  </div>
</details>

<details class="sec" id="sec-log">
  <summary>最近日志</summary>
  <div class="sec-body"><pre id="log"></pre></div>
</details>

<script>
const $ = id => document.getElementById(id);
let busy = false;
function setBusy(b){ busy=b; document.querySelectorAll('button').forEach(x=>x.disabled=b); }
function esc(s){ return String(s??'').replace(/[&<>]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
function openSec(id){ const el = $(id); if(el && !el.open) el.open = true; }
function closeSec(id){ const el = $(id); if(el && el.open) el.open = false; }

async function loadState(){
  try{
    const r = await fetch('/api/state'); const j = await r.json();
    if(j.error) $('meta').textContent = 'db: ' + j.error;
    const lp = j.last_push ? new Date(j.last_push).toLocaleString('zh-CN') : '—';
    const ll = j.last_pull ? new Date(j.last_pull).toLocaleString('zh-CN') : '—';
    $('meta').innerHTML =
      '上次推送 <b>'+esc(lp)+'</b> · 上次拉取 <b>'+esc(ll)+'</b>' +
      (j.running ? ' · <b style="color:var(--acc)">同步中…</b>' : '');
    $('links').innerHTML = (j.links||[]).map(l =>
      '<div class="card"><h2>'+esc(l.zy_book_name||'(无书名)')+'</h2>' +
      '<table>' +
      '<tr><td>Kavita Series</td><td>'+esc(l.kavita_series_id)+'</td></tr>' +
      '<tr><td>映射粒度</td><td>'+((Number(l.kavita_chapter_id) > 0)
        ? ('卷级 · chapter '+esc(l.kavita_chapter_id))
        : '整本（系列级）')+'</td></tr>' +
      '<tr><td>掌阅 bookId</td><td>'+esc(String(l.zy_book_id).slice(0,24))+'…</td></tr>' +
      '<tr><td>锚定 chapterId</td><td>'+esc(l.zy_anchor_chapter_id||'0')+'</td></tr>' +
      '<tr><td>映射更新</td><td>'+esc(l.updated_utc||'—')+'</td></tr>' +
      '</table></div>').join('')
      || '<div class="card k">还没有任何映射。CLI: <code>zync.py link</code></div>';
    const nLink = (j.links||[]).length;
    $('link-cnt').textContent = nLink ? (nLink + ' 本') : '还没有';
  }catch(e){ $('meta').textContent = '加载失败: ' + e.message; }
}
async function loadLog(){
  try{
    const r = await fetch('/api/log?n=40'); const j = await r.json();
    $('log').innerHTML = (j.lines||[]).map(l =>
      '<span class="lv-'+esc(l.level)+'">'+
      (l.ts?esc(l.ts.slice(11))+' ':'')+esc(l.msg)+'</span>').join('\n');
  }catch(e){}
}
async function run(cmd){
  if(busy) return; setBusy(true);
  openSec('sec-out');
  $('out-h').textContent = cmd + ' 执行中…'; $('out').textContent = '…';
  try{
    const r = await fetch('/api/run/'+cmd, {method:'POST'});
    const j = await r.json();
    $('out-h').textContent = cmd + ' — rc=' + j.rc + ' · ' + (j.ms??'?') + 'ms';
    $('out').textContent = [j.json ? JSON.stringify(j.json,null,1) : '',
                            j.stderr ? 'STDERR:\n'+j.stderr : ''].filter(Boolean).join('\n\n')
               || (j.stdout||'(空)');
    if(j.error) $('out').textContent = '错误: ' + j.error + '\n\n' + $('out').textContent;
  }catch(e){ $('out').textContent = '请求失败: ' + e.message; }
  setBusy(false); await loadState(); await loadLog();
}
$('b-push').onclick = ()=>run('push');
$('b-pull').onclick = ()=>run('pull');
$('b-refresh').onclick = ()=>{loadState();loadLog();loadAuth();loadKavita();loadCloud(false);loadCloudLocal();};

// ---- Kavita 改造 -----------------------------------------------------
async function loadKavita(){
  try{
    const r = await fetch('/api/kavita/status'); const j = await r.json();
    if(j.error){ $('kv-meta').textContent = '检测失败: ' + j.error; return; }
    const bad = (j.checks||[]).filter(c=>!c.ok).length;
    const p = $('kv-pill');
    p.textContent = bad ? bad + ' 项待处理' : '全部就绪';
    p.className = 'pill ' + (bad ? '' : 'auth-ok');
    $('kv-meta').innerHTML = '补丁 v'+esc(j.version)+' · 容器 '+esc(j.container)+
      ' · 建于 '+esc(String(j.created||'').slice(0,19).replace('T',' '));
    $('kv-checks').innerHTML = '<table>' + (j.checks||[]).map(c =>
      '<tr><td><span class="lv-'+ (c.ok?'INFO':'ERROR') +'">'+(c.ok?'✓':'✗')+'</span> '+
      esc(c.label)+'<br><span class="k">'+esc(c.detail)+'</span>'+
      (c.ok?'':'<br><span class="k">修: '+esc(c.fix)+'</span>')+'</td></tr>').join('') + '</table>';

    const a = j.autostart || {};
    const cr = a.cron || {};
    $('kv-auto').innerHTML = '<b>自启</b> systemd ' + esc(a.installed ?? '?') + '/' + esc(a.total ?? '?') +
      ' · crontab ' + (cr.installed ? '已装' : '未装') + ' (cron=' + esc(cr.service ?? '?') + ')' +
      ((a.all_installed || cr.installed) ? '' :
        ' <span class="k">(点「装自启 (免 root)」)</span>') +
      '<br><span class="k">' + (a.units||[]).map(u =>
        esc(u.unit.replace('kavita-zy-',''))+':'+esc(u.enabled)+'/'+esc(u.active)).join(' · ') + '</span>';

    const h = j.heal || {};
    const last = h.last || {};
    $('kv-heal').innerHTML = '<b>自愈</b> ' + (h.disabled ? '已停用' :
      (last.ts ? esc(String(last.ts).slice(0,19).replace('T',' ')) + ' · ' +
        (last.ok ? '正常' : (last.problems||'?')+' 项异常') +
        (last.actions ? ' · 修复: '+esc(last.actions) : '') : '还没跑过'));
    loadHealLog();
    $('kv-conf').innerHTML = (j.params||[]).map(r =>
      '<tr><td><span class="k">'+esc(r[0]+' · '+r[1])+'</span></td><td>'+esc(r[2])+'</td></tr>').join('');
  }catch(e){ $('kv-meta').textContent = '检测请求失败: ' + e.message; }
}
async function loadHealLog(){
  try{
    const r = await fetch('/api/kavita/heal-log?n=60'); const j = await r.json();
    $('kv-heal-log').textContent = (j.lines||[]).join('\n') || '(空)';
  }catch(e){ $('kv-heal-log').textContent = '读取失败: ' + e.message; }
}
async function kvAct(action, label){
  if(busy) return; setBusy(true);
  openSec('sec-out');
  $('out-h').textContent = label + ' 执行中…'; $('out').textContent = '…';
  try{
    const r = await fetch('/api/kavita/action', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({action: action})});
    const j = await r.json();
    if(j.error && j.rc === undefined){
      $('out-h').textContent = label + ' — 失败';
      $('out').textContent = j.error;
    } else if(j.steps){
      $('out-h').textContent = label + ' — rc=' + (j.rc ?? '?');
      $('out').textContent = j.steps.map(s =>
        '### ' + s.step + '  rc=' + s.rc + ' · ' + (s.ms ?? '?') + 'ms\n' +
        [s.stdout, s.stderr ? 'STDERR:\n'+s.stderr : ''].filter(Boolean).join('\n')
      ).join('\n\n') + (j.hint ? '\n\n' + j.hint : '');
    } else {
      $('out-h').textContent = label + ' — rc=' + (j.rc ?? '?') + ' · ' + (j.ms ?? '?') + 'ms';
      $('out').textContent = [j.json ? JSON.stringify(j.json, null, 1) : '',
                              j.stderr ? 'STDERR:\n' + j.stderr : ''].filter(Boolean).join('\n\n')
                              || (j.stdout || '(空)');
    }
  }catch(e){ $('out').textContent = '请求失败: ' + e.message; }
  setBusy(false); await loadKavita();
}
$('b-kv-boot').onclick = ()=>kernelBoot();
function kernelBoot(){
  if(!confirm('会对当前 Kavita 执行: 注入前端 + 重启后端 + 安装自启 + 自检。继续?')) return;
  kvAct('bootstrap', '一键改造');
}
$('b-kv-install').onclick = ()=>kvAct('install', '注入 / 重打');
$('b-kv-check').onclick = ()=>loadKavita();
$('b-kv-api').onclick = ()=>kvAct('restart-api', '重启补丁后端');
$('b-kv-heal').onclick = ()=>kvAct('heal', '立即自检');
$('b-kv-cron').onclick = ()=>kvAct('cron-install', '装自启 (crontab)');
$('b-kv-auto').onclick = ()=>kvAct('autostart-install', '装自启 (systemd)');
$('b-kv-heal-off').onclick = ()=>kvAct('heal-off', '关自愈');
$('b-kv-heal-on').onclick = ()=>kvAct('heal-on', '开自愈');
$('b-kv-edry').onclick = ()=>kvAct('edition-dry', '重排版 dry-run');
$('b-kv-edo').onclick = ()=>{ if(confirm('会替换库内 epub 并触发重扫, 继续?')) kvAct('edition-all', '重排版执行'); };
$('b-kv-self').onclick = ()=>kvAct('selfcheck', '体检');

// ---- 掌阅认证 --------------------------------------------------------
let qrTimer = null;
function stopQrPoll(){ if(qrTimer){ clearInterval(qrTimer); qrTimer=null; } }

async function loadAuth(){
  try{
    const r = await fetch('/api/auth'); const j = await r.json();
    const p = $('auth-pill'), m = $('auth-meta');
    if(j.authenticated){
      p.textContent = '已认证'; p.className='pill auth-ok';
      m.innerHTML = '账号 <b>'+esc(j.usr_masked)+'</b>' +
        (j.file_mtime ? ' · 凭据写入 '+esc(j.file_mtime) : '');
      stopQrPoll();
      if(j.qr && j.qr.status === 'waiting') showQrWaiting();
      return;
    }
    p.textContent = '未认证'; p.className='pill auth-no';
    m.textContent = 'token 缺失或已失效 · 点「刷新二维码」用掌阅 App 扫';
    if(j.qr && j.qr.status === 'waiting') showQrWaiting(j.qr.issued);
  }catch(e){ $('auth-meta').textContent = '认证状态加载失败: '+e.message; }
}

function showQrWaiting(issued){
  $('qr-host').innerHTML = '<div class="qrbox"><img alt="qr" src="/api/auth/qr?t='+
    Date.now()+'" style="width:100%;max-width:260px;height:auto;display:block"></div>'+
    '<div class="meta" style="text-align:center">等待扫码'+
    (issued ? ' · '+esc(issued) : '')+' …</div>';
  stopQrPoll();
  qrTimer = setInterval(async ()=>{
    try{
      const r = await fetch('/api/auth/qr/check', {method:'POST'});
      const j = await r.json();
      if(j.status === 'ok'){
        $('qr-host').innerHTML = '<div class="meta" style="text-align:center;padding:24px 0">'+
          '<b class="auth-ok">认证成功</b><br>'+esc(j.usr||'')+'</div>';
        stopQrPoll(); loadAuth();
      } else if(j.status === 'invalid' || j.status === 'error'){
        $('qr-host').innerHTML = '<div class="meta" style="text-align:center">'+
          '二维码失效'+esc(j.msg?(' · '+j.msg):'')+' · 点「刷新二维码」重来</div>';
        stopQrPoll();
      }
    }catch(e){}
  }, 3000);
}

$('b-qr').onclick = async ()=>{
  const b = $('b-qr'); b.disabled = true; b.textContent = '领码中…';
  try{
    const r = await fetch('/api/auth/qr/new', {method:'POST'});
    const j = await r.json();
    if(!j.ok && j.error){ $('qr-host').innerHTML = '<div class="meta auth-err">'+esc(j.error)+'</div>'; return; }
    showQrWaiting(j.issued);
  }catch(e){ $('qr-host').innerHTML = '<div class="meta">领码失败: '+esc(e.message)+'</div>'; }
  b.disabled=false; b.textContent='刷新二维码';
};
$('b-authchk').onclick = async ()=>{
  const b=$('b-authchk'); b.disabled=true;
  try{
    const r = await fetch('/api/auth/refresh', {method:'POST'});
    await r.json();
    const cr = await fetch('/api/auth/qr/check', {method:'POST'});
    const cj = await cr.json();
    if(cj.status==='ok'){ $('qr-host').innerHTML=''; }
  }catch(e){}
  b.disabled=false; loadAuth();
};
// ---- 掌阅云书籍 -------------------------------------------------------
function fmtBytes(n){
  if(n==null) return '?';
  const u=['B','KB','MB','GB']; let i=0, v=Number(n);
  while(v>=1024 && i<u.length-1){ v/=1024; i++; }
  return (i?v.toFixed(1):v)+u[i];
}
async function postJson(url, obj){
  const r = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify(obj||{})});
  let j = null; try{ j = await r.json(); }catch(e){ j = {error:'HTTP '+r.status}; }
  if(!r.ok) throw new Error(j.error || ('HTTP '+r.status));
  return j;
}
let clBooks = [];
const SRC_CN = {shelf:'个人', store:'商店'};

function renderDir(dir){
  const inp=$('cl-dest');
  if(dir && !inp.dataset.touched) inp.value = dir;
  $('cl-dir-hint').innerHTML = '当前下载目录 <code>'+esc(dir||'?')+'</code> · 下载与搬移都落在这里（可填子目录）。';
}

function bookRow(b){
  const lo = b.local;
  const cloudSize = b.size || 0;
  const localSize = lo ? (lo.actual_size ?? lo.size ?? 0) : 0;
  let st;
  if(lo){
    const diff = cloudSize && localSize &&
      Math.abs(localSize - cloudSize) > Math.max(8192, cloudSize * 0.05);
    st = '<span class="pill ok">已下载</span>'+
      '<div class="meta" style="font-size:11px;line-height:1.5">'+esc(lo.name||'')+
      '<br>'+fmtBytes(localSize)+
      (diff ? ' <span style="color:var(--warn)">≠ 云端 '+fmtBytes(cloudSize)+'（本地已改，可能是重排版版）</span>' : '')+
      '<br>'+esc(lo.path||'')+'</div>';
  }else{
    st = '<span class="meta">未下载</span>';
  }
  const sizeTxt = b.size ? fmtBytes(b.size) : '<span class="meta">—</span>';
  const dlBtn = lo
    ? '<button data-dl="'+esc(b.key)+'" data-ow="1" style="padding:2px 8px;font-size:12px">覆盖下载</button> '
    : '<button data-dl="'+esc(b.key)+'" style="padding:2px 8px;font-size:12px">下载</button> ';
  return '<tr><td>'+esc(b.name)+
    '<div class="meta" style="font-size:11px">'+esc(String(b.id).slice(0,20))+'…'+
    (b.file_id?' · file '+esc(String(b.file_id).slice(0,12))+'…':'')+'</div></td>'+
    '<td>'+esc(b.author||'')+'</td>'+
    '<td>'+sizeTxt+'</td>'+
    '<td>'+esc(b.ctime||'')+'</td>'+
    '<td>'+st+'</td>'+
    '<td>'+dlBtn+
    '<button data-del="'+esc(b.key)+'" style="padding:2px 8px;font-size:12px">删云端</button></td></tr>';
}

async function loadCloud(refresh){
  const pill=$('cl-pill'), meta=$('cl-meta'), list=$('cl-list');
  pill.textContent='…';
  try{
    const r = await fetch('/api/cloud/books'+(refresh===false?'?refresh=0':''));
    const j = await r.json();
    if(j.error) throw new Error(j.error);
    clBooks = j.books || [];
    pill.textContent = j.count + ' 本';
    pill.className = 'pill ok';
    meta.innerHTML = '自己上传的书 <b>'+j.personal_count+'</b> 本（无 DRM）'+
      ' · 版本 '+esc(String(j.version ?? '?'))+
      ' · 拉取于 '+esc((j.fetched_at||'?').replace('T',' ')) +
      (j.errors && j.errors.length ? ' · <span style="color:var(--err)">'+j.errors.map(esc).join('; ')+'</span>' : '');
    renderDir(j.dl_dir);
    if(!clBooks.length){
      list.innerHTML = '<div class="meta">云书架里没有自己上传的书（is_local_book=true 的条目为 0）</div>';
      return;
    }
    list.innerHTML = '<table><thead><tr><th>书名</th><th>作者</th><th>大小</th><th>上传</th><th>本地</th><th>操作</th></tr></thead><tbody>'+
      clBooks.map(bookRow).join('') + '</tbody></table>';
  }catch(e){
    pill.textContent='离线'; pill.className='pill err';
    meta.textContent = '取列表失败: ' + e.message;
    list.innerHTML = '<div class="meta">离线时只能看本地已下载清单（见下）</div>';
  }
}

async function loadCloudLocal(){
  const host=$('cl-local');
  try{
    const r = await fetch('/api/cloud/local');
    const j = await r.json();
    const fs = j.files || [];
    if(!fs.length){ host.innerHTML='<div class="meta">还没有下载过</div>'; $('local-cnt').textContent='还没有'; return; }
    $('local-cnt').textContent = fs.length + ' 个文件';
    host.innerHTML = '<table><thead><tr><th>文件</th><th>大小</th><th>操作</th></tr></thead><tbody>'+
      fs.map(f=>'<tr><td>'+esc(f.name||f.path)+
        '<div class="meta" style="font-size:11px">'+esc(f.source||'')+' · '+esc(f.path)+
        (f.exists?'':' <span style="color:var(--err)">文件缺失</span>')+'</div></td>'+
        '<td>'+fmtBytes(f.actual_size ?? f.size)+'</td>'+
        '<td>'+(f.unmanaged
          ? '<span class="meta">未登记 · </span><button data-lfile="'+esc(f.key)+'" style="padding:2px 8px;font-size:12px">删文件</button>'
          : '<button data-mv="'+esc(f.key)+'" style="padding:2px 8px;font-size:12px">搬移</button> '+
            '<button data-lrm="'+esc(f.key)+'" style="padding:2px 8px;font-size:12px">移出清单</button> '+
            '<button data-lfile="'+esc(f.key)+'" style="padding:2px 8px;font-size:12px">删文件</button>')+
        '</td></tr>').join('')+
      '</tbody></table>';
  }catch(e){ host.innerHTML = '<div class="meta">读取失败: '+esc(e.message)+'</div>'; }
}

async function pollJob(id){
  for(let i=0;i<600;i++){
    await new Promise(r=>setTimeout(r,700));
    let j=null;
    try{ j = await postJson('/api/cloud/job', {id}); }catch(e){ $('cl-jobs').textContent='任务丢失: '+e.message; return; }
    const s = j.state;
    $('cl-jobs').textContent = '任务 '+id+' · '+s+' · '+fmtBytes(j.bytes)+(j.total?(' / '+fmtBytes(j.total)):'')+
      (j.error?(' · '+j.error):'');
    if(s==='preparing'){ $('cl-jobs').textContent += ' · 重排中: '+(j.note||''); }
    if(s==='done'||s==='error'){
      if(s==='done'){
        $('cl-jobs').textContent += ' · 落盘 '+j.path;
        const pp = j.prep || {};
        if(pp.state==='ok'){ $('cl-jobs').textContent += ' · 已重排 '+pp.pages+' 页'+
          (pp.series_id?(' (Kavita 系列 '+pp.series_id+')'):''); }
        else if(pp.state==='edition'){ $('cl-jobs').textContent += ' · 已是切片版'; }
        else if(pp.state==='skipped'||pp.state==='no_series'){
          $('cl-jobs').textContent += ' · 未重排: '+(pp.why||pp.state); }
        else if(pp.state==='error'){ $('cl-jobs').textContent += ' · 重排失败: '+pp.error; }
      }
      await loadCloud(false); await loadCloudLocal();
      return;
    }
  }
}

document.addEventListener('click', async ev=>{
  const t = ev.target;
  if(!t || t.tagName!=='BUTTON') return;
  if(t.dataset.dl){
    const key = t.dataset.dl;
    const b = clBooks.find(x=>x.key===key) || {};
    const lo = b.local;
    const ow = t.dataset.ow === '1' && lo;
    if(ow){
      const cloudSize = b.size || 0, localSize = lo.actual_size ?? lo.size ?? 0;
      const diff = cloudSize && localSize &&
      Math.abs(localSize - cloudSize) > Math.max(8192, cloudSize * 0.05);
      if(!confirm('覆盖本地文件?\n\n'+lo.path+
        (diff ? '\n\n注意: 本地文件 '+fmtBytes(localSize)+' 与云端 '+fmtBytes(cloudSize)+
                ' 大小不同，本地可能是重排版/Kavita 适配版，覆盖后进度映射会失效。' : '')+
        '\n\n云端那份不会动。')) return;
    }
    const label = t.textContent;
    t.disabled = true; t.textContent='…';
    try{
      const payload = ow ? {key, overwrite:true, target: lo.path} : {key};
      const j = await postJson('/api/cloud/download', payload);
      pollJob(j.job.id);
    }catch(e){ $('cl-jobs').textContent = '下载启动失败: '+e.message; }
    t.disabled = false; t.textContent = label;
  }
  if(t.dataset.del){
    const key = t.dataset.del;
    const b = clBooks.find(x=>x.key===key) || {};
    if(!confirm('从掌阅云端删除《'+(b.name||key)+'》?\n\n这是删云端书架条目，不可撤销（本地已下载的文件不受影响）。')) return;
    t.disabled=true;
    try{
      const j = await postJson('/api/cloud/delete', {keys:[key], confirm:'yes'});
      $('cl-jobs').textContent = j.ok ? ('已从云端删除 '+key) : ('删除失败: '+(j.error||'?'));
      await loadCloud(true);
    }catch(e){ $('cl-jobs').textContent = '删除失败: '+e.message; }
    t.disabled=false;
  }
  if(t.dataset.mv){
    const key = t.dataset.mv;
    const dest = prompt('搬到下载目录下的哪个位置？（留空=根目录，可填子目录名）', '');
    if(dest===null) return;
    try{
      const j = await postJson('/api/cloud/move', {key, dest: dest || null});
      $('cl-jobs').textContent = '已搬移到 '+j.file.path;
      await loadCloudLocal(); await loadCloud(false);
    }catch(e){ $('cl-jobs').textContent = '搬移失败: '+e.message; }
  }
  if(t.dataset.lrm){
    const key = t.dataset.lrm;
    if(!confirm('只从清单里移除（文件留在磁盘）？')) return;
    try{
      await postJson('/api/cloud/local_delete', {key, delete_file:false});
      await loadCloudLocal(); await loadCloud(false);
    }catch(e){ $('cl-jobs').textContent = '移出失败: '+e.message; }
  }
  if(t.dataset.lfile){
    const key = t.dataset.lfile;
    if(!confirm('删除本地文件（不可恢复，云端那份还在）？')) return;
    try{
      const j = await postJson('/api/cloud/local_delete', {key, delete_file:true});
      $('cl-jobs').textContent = '已删除本地文件 '+(j.path||'');
      await loadCloudLocal(); await loadCloud(false);
    }catch(e){ $('cl-jobs').textContent = '删除失败: '+e.message; }
  }
  if(t.dataset.rootDel){
    const p = t.dataset.rootDel;
    if(!confirm('从白名单移除目录 '+p+' ?')) return;
    try{
      const j = await postJson('/api/cloud/roots_remove', {path:p});
      renderRoots(j.roots);
    }catch(e){ $('cl-jobs').textContent = '移除失败: '+e.message; }
  }
});
$('b-cl-refresh').onclick = ()=>loadCloud(true);
$('cl-dest').oninput = ()=>{ $('cl-dest').dataset.touched = '1'; };
$('b-cl-dir-save').onclick = async ()=>{
  const p = ($('cl-dest').value||'').trim();
  if(!p){ $('cl-jobs').textContent='目录不能为空'; return; }
  try{
    const j = await postJson('/api/cloud/dir_set', {path:p});
    delete $('cl-dest').dataset.touched;
    renderDir(j.dir);
    $('cl-jobs').textContent = '下载目录已设为 '+j.dir;
  }catch(e){ $('cl-jobs').textContent = '设置目录失败: '+e.message; }
};

loadState(); loadLog(); loadAuth(); loadKavita();
loadCloud(false); loadCloudLocal();
</script></body></html>"""


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    srv.daemon_threads = True
    print(f"掌阅桥接 web  {HOST}:{PORT}  token={'on' if TOKEN else 'off'}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()