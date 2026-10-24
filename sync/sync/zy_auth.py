"""掌阅扫码登录 — 纯 HTTP, 不依赖 Playwright.

流程 (2026-10-21 用 Playwright 抓包验证, 端点常量已固化):
    1. GET /bookstore/open/user/qrcode/get      -> body = 随机 code
    2. GET /bookstore/open/user/qrcode/result?code=<code>
       轮询, 未扫码返 30059 "随机码未授权"
       扫完返 body {usr, zysid, ...} -> 落 .auth.json
    3. 后续请求带 usr/zysid/p29/p2/p3/p4/p33

指纹参数 (p29/p2/p3/p4) 是 App 启动时上报的固定值, 全局通用, 跟账号无关。
不需要预置 cookie —— 抓包显示 usr/zysid 留空即可。

二维码内容格式 (从 pc.ireader.com 的 chunk 7dfd733 反查):
    r.a.toDataURL("web_login:".concat(code))
即 App 要扫的是 `web_login:<code>`, **不是**裸 code。

轮询上限 160 次 (前端 hardcoded, 约 3s 一次 = 8 分钟)。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .config import BRIDGE_ROOT

QR_GET = "/bookstore/open/user/qrcode/get"
QR_RESULT = "/bookstore/open/user/qrcode/result"
API_BASE = "https://api-pc.zhangyue.com"

# App 设备指纹 — 缺任一个就 403 code:2
FINGERPRINT = {
    "p29": "zye5b814",
    "p2": "104583",
    "p3": "101010010",
    "p4": "501603",
    "p33": "com.chaozh.iReaderFree",
}

CODE_WAITING = 30059   # 随机码未授权 (还没扫)
CODE_INVALID = 30017   # 随机码无效 (code 过期)
AUTH_PATH = BRIDGE_ROOT / ".auth.json"

# 二维码内容前缀 — pc 站前端用 qrcode 库编的, App 认这个前缀
QR_PREFIX = "web_login:"
MAX_POLLS = 160        # 前端 hardcoded 上限


class ZYError(RuntimeError):
    def __init__(self, code: int | str, msg: str):
        super().__init__(f"[{code}] {msg}")
        self.code = code
        self.msg = msg


def _get(path: str, extra: dict | None = None, *, timeout: int = 20) -> dict:
    """打一个掌阅 API, 返回已解析的 JSON."""
    q = dict(FINGERPRINT)
    q.update(extra or {})
    url = f"{API_BASE}{path}?" + urllib.parse.urlencode(q)
    req = urllib.request.Request(url, headers={
        "User-Agent": "okhttp/4.9",
        "Origin": "https://pc.ireader.com",
        "Referer": "https://pc.ireader.com/",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise ZYError(e.code, f"HTTP {e.code}") from e
    except Exception as e:  # noqa: BLE001
        raise ZYError("net", f"{type(e).__name__}: {e}") from e
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ZYError("json", raw[:200]) from e


def qr_payload(code: str) -> str:
    """二维码里该编的字符串."""
    return QR_PREFIX + code


def new_qr() -> str:
    """领一个二维码会话, 返回随机 code (裸 code, 编二维码请用 qr_payload)."""
    r = _get(QR_GET)
    if r.get("code") != 0:
        raise ZYError(r.get("code"), r.get("msg", "?"))
    body = r.get("body")
    code = body.strip('"') if isinstance(body, str) else body
    if not code:
        raise ZYError("empty", "qrcode/get 没返回 code")
    return code


def poll_once(code: str) -> dict:
    """查一次扫码状态. 返回 {'status': 'waiting'|'ok'|'invalid', 'auth': {...} or None}"""
    r = _get(QR_RESULT, {"code": code})
    rc = r.get("code")
    if rc == 0 and r.get("body"):
        return {"status": "ok", "auth": _norm_auth(r["body"]), "msg": r.get("msg", "")}
    msg = r.get("msg", "")
    if rc == CODE_WAITING:
        return {"status": "waiting", "auth": None, "msg": msg}
    if rc == CODE_INVALID:
        return {"status": "invalid", "auth": None, "msg": msg}
    return {"status": "error", "auth": None, "msg": f"[{rc}] {msg}"}


def _norm_auth(body) -> dict:
    """掌阅返回的 body 可能是 dict 也可能是 JSON 字符串, 统一成 dict."""
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            return {}
    if not isinstance(body, dict):
        return {}
    return body


def save_auth(auth: dict) -> dict:
    """合并落到 .auth.json, 保留指纹字段."""
    auth = {**_norm_auth(auth), **FINGERPRINT}
    if not auth.get("usr") or not auth.get("zysid"):
        raise ZYError("auth", "返回里没有 usr/zysid, 不能用")
    AUTH_PATH.write_text(json.dumps(auth, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        AUTH_PATH.chmod(0o600)
    except OSError:
        pass
    return auth


def load_auth() -> dict | None:
    if not AUTH_PATH.exists():
        return None
    try:
        return json.loads(AUTH_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def auth_summary() -> dict:
    """给前端看的脱敏状态."""
    a = load_auth()
    if not a:
        return {"authenticated": False}
    usr = str(a.get("usr", ""))
    return {
        "authenticated": bool(usr and a.get("zysid")),
        "usr": usr,
        "usr_masked": (usr[:3] + "***" + usr[-2:]) if len(usr) > 6 else usr,
        "zye5b814": bool(a.get("p29")),
        "file_mtime": time.strftime("%F %T", time.localtime(AUTH_PATH.stat().st_mtime))
        if AUTH_PATH.exists() else None,
    }


def wait_scan(code: str, *, timeout: int = 180, interval: int = 3) -> dict:
    """阻塞轮询直到扫完. 主要给 CLI 用; Web 端走 poll_once 增量式。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = poll_once(code)
        if r["status"] == "ok":
            save_auth(r["auth"])
            return r
        if r["status"] == "invalid":
            return r
        time.sleep(interval)
    return {"status": "timeout", "auth": None, "msg": f"{timeout}s 没扫"}


if __name__ == "__main__":
    import sys
    c = new_qr()
    print("code =", c)
    if "--wait" in sys.argv:
        print(wait_scan(c))
