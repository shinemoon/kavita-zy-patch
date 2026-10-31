"""Web 登录保护 —— 复用 Kavita (:5000) 的账号密码.

用户 2026-10-31: 「新制的端口 WebUI 都没有密码保护, 和 5000 端口用一样的即可」.
所以 5012 / 5013 不另设密码: 用户输的用户名/密码直接拿去问 Kavita
`POST /api/Account/login`, 200 才放行 —— 凭据就是 :5000 那套, 改密码也自动跟着变.

设计要点:
  * 会话 cookie ``zy_webauth`` = ``<b64user>.<exp>.<nonce>.<hmac>``
    HMAC-SHA256, 密钥落在共享目录. cookie 只按 host 生效(不看端口),
    所以 5012 与 5013 用同一份密钥 -> 登录一次两边都进得去.
  * Kavita 挂了怎么办: 首次在线验证成功后缓存一份 pbkdf2 verifier,
    在线验证 *不可达* 时用它兜底 (还是同一把密码), 不会把自己锁在门外.
    密码输错(401)时不走兜底, 免得缓存变成另一套口令.
  * 失败限流: 同一 IP 5 分钟 8 次, 超了返回 429 并倒计时.

环境变量:
  ZY_WEBAUTH_DIR      共享状态目录 (默认 /vol2/1001/webauth)
  ZY_WEBAUTH_KAVITA   Kavita 地址 (默认 $ZY_KAVITA_URL 或 http://127.0.0.1:5000)
  ZY_WEBAUTH_TTL      会话有效期秒 (默认 604800 = 7 天)
  ZY_WEBAUTH_DISABLE  1 = 关闭保护 (仅排障用)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

COOKIE_NAME = "zy_webauth"
STATE_NAME = "webauth.json"
_LOCK = threading.RLock()
_FAILS: dict[str, list[float]] = {}
_RATE_N = 8
_RATE_WIN = 300


# ---------------------------------------------------------------- env

def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default) or ""


def enabled() -> bool:
    return _env("ZY_WEBAUTH_DISABLE").strip().lower() not in ("1", "true", "yes", "on")


def ttl() -> int:
    try:
        return max(300, int(_env("ZY_WEBAUTH_TTL", "604800")))
    except ValueError:
        return 604800


def kavita_url() -> str:
    base = _env("ZY_WEBAUTH_KAVITA") or _env("ZY_KAVITA_URL") or "http://127.0.0.1:5000"
    return base.rstrip("/")


def state_dir() -> Path:
    p = Path(_env("ZY_WEBAUTH_DIR", "/vol2/1001/webauth"))
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return p


def _own(path: Path) -> None:
    """root 写的文件交回目录属主 (目录常由非 root 的 5013 进程持有)."""
    try:
        if os.geteuid() != 0:
            return
        st = path.parent.stat()
        os.chown(path, st.st_uid, st.st_gid)
    except Exception:  # noqa: BLE001 — 拿不到就算了, 只是会挡住非 root 的写
        pass


# ---------------------------------------------------------------- state

def _load() -> dict:
    p = state_dir() / STATE_NAME
    try:
        d = json.loads(p.read_text("utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001 — 缺/坏都当空
        return {}


def _save(d: dict) -> None:
    p = state_dir() / STATE_NAME
    tmp = p.with_name(f".{STATE_NAME}.{os.getpid()}.tmp")
    with _LOCK:
        try:
            tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), "utf-8")
            try:
                os.chmod(tmp, 0o660)
            except OSError:
                pass
            _own(tmp)
            os.replace(tmp, p)
            _own(p)
        except Exception:  # noqa: BLE001 — 写不了不该让页面 500
            pass


def _secret() -> bytes:
    d = _load()
    s = d.get("secret")
    if isinstance(s, str) and len(s) >= 32:
        try:
            return bytes.fromhex(s)
        except ValueError:
            return s.encode()
    d["secret"] = secrets.token_hex(32)
    d.setdefault("verifier", {})
    _save(d)
    # 两个进程可能同时首启, 以落盘那份为准
    again = _load().get("secret")
    s = again if isinstance(again, str) and len(again) >= 32 else d["secret"]
    try:
        return bytes.fromhex(s)
    except ValueError:
        return s.encode()


# ---------------------------------------------------------------- session

def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode().rstrip("=")


def _unb64(text: str) -> str:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)).decode("utf-8")


def _sign(body: str) -> str:
    return hmac.new(_secret(), body.encode("utf-8"), hashlib.sha256).hexdigest()[:40]


def make_token(user: str, now: float | None = None) -> str:
    exp = int((now if now is not None else time.time()) + ttl())
    body = f"{_b64(user)}.{exp}.{secrets.token_hex(6)}"
    return f"{body}.{_sign(body)}"


def parse_token(value: str | None) -> str | None:
    parts = (value or "").split(".")
    if len(parts) != 4:
        return None
    u, exp, nonce, sig = parts
    body = f"{u}.{exp}.{nonce}"
    if not hmac.compare_digest(_sign(body), sig):
        return None
    try:
        if int(exp) < time.time():
            return None
        return _unb64(u)
    except Exception:  # noqa: BLE001
        return None


def cookie_from_header(header: str | None) -> str | None:
    for part in (header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k.strip() == COOKIE_NAME:
            return v.strip()
    return None


def current_user(cookie_header: str | None) -> str | None:
    """已登录用户名; 未登录 None. 关闭保护时给占位 '*'."""
    if not enabled():
        return "*"
    return parse_token(cookie_from_header(cookie_header))


def set_cookie(user: str) -> str:
    return (f"{COOKIE_NAME}={make_token(user)}; Path=/; Max-Age={ttl()}; "
            f"HttpOnly; SameSite=Lax")


def clear_cookie() -> str:
    return f"{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"


# ---------------------------------------------------------------- auth

def _hash_pw(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), 120000).hex()


def remember(user: str, password: str) -> None:
    d = _load()
    verifier = dict(d.get("verifier") or {})
    salt = secrets.token_hex(16)
    verifier[user] = {"salt": salt, "hash": _hash_pw(password, salt),
                      "at": int(time.time())}
    d["verifier"] = verifier
    _save(d)


def verify_cached(user: str, password: str) -> bool:
    entry = (_load().get("verifier") or {}).get(user)
    if not isinstance(entry, dict):
        return False
    try:
        return hmac.compare_digest(entry["hash"], _hash_pw(password, entry["salt"]))
    except Exception:  # noqa: BLE001
        return False


def verify_online(user: str, password: str) -> tuple[bool, str]:
    """问 Kavita: 这套账号密码对不对. 返回 (ok, 错误文案)."""
    payload = json.dumps({"username": user, "password": password,
                          "apiKey": ""}).encode("utf-8")
    req = urllib.request.Request(
        f"{kavita_url()}/api/Account/login", data=payload, method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return 200 <= r.status < 300, ""
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return False, "用户名或密码不对"
        return False, f"unreachable: Kavita 返回 HTTP {e.code}"
    except Exception as e:  # noqa: BLE001 — 连不上 / 超时 / DNS
        return False, f"unreachable: Kavita 不可达 ({type(e).__name__})"


def verify(user: str, password: str) -> tuple[bool, str, str]:
    """→ (ok, 错误文案, 途径 'kavita'|'cache')"""
    user = (user or "").strip()
    if not user or not password:
        return False, "用户名和密码都要填", ""
    ok, msg = verify_online(user, password)
    if ok:
        remember(user, password)
        return True, "", "kavita"
    if msg.startswith("unreachable:"):
        if verify_cached(user, password):
            return True, "", "cache"
        return False, (msg.split(":", 1)[1] +
                       " —— 离线口令也对不上 (先让 Kavita 起来, 或用上次验证过的密码)"), ""
    return False, msg, ""


# ---------------------------------------------------------------- ratelimit

def rate_blocked(ip: str) -> int:
    """被限流则返回还需等待的秒数 (>0), 否则 0."""
    now = time.time()
    with _LOCK:
        arr = [t for t in _FAILS.get(ip, []) if now - t < _RATE_WIN]
        _FAILS[ip] = arr
        if len(arr) >= _RATE_N:
            return max(1, int(_RATE_WIN - (now - arr[0])) + 1)
        return 0


def rate_fail(ip: str) -> None:
    with _LOCK:
        _FAILS.setdefault(ip, []).append(time.time())


def rate_clear(ip: str) -> None:
    with _LOCK:
        _FAILS.pop(ip, None)


# ---------------------------------------------------------------- 页面

_LOGIN_CSS = """
:root{--bg:#14161a;--fg:#e8e6e1;--dim:#8b8f98;--line:#2a2e35;--acc:#c9a96a;--err:#d97a6c}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;background:var(--bg);color:var(--fg);
  font:15px/1.6 -apple-system,"PingFang SC","Noto Sans SC",sans-serif;
  display:flex;align-items:center;justify-content:center;padding:20px}
.box{width:100%;max-width:360px;background:#1b1e24;border:1px solid var(--line);
  border-radius:12px;padding:22px 20px}
h1{font-size:18px;font-weight:600;margin:0 0 4px}
.sub{color:var(--dim);font-size:13px;margin-bottom:18px}
label{display:block;font-size:12px;color:var(--dim);margin:12px 0 5px}
input{width:100%;min-height:44px;background:#22262d;color:var(--fg);
  border:1px solid var(--line);border-radius:8px;padding:0 12px;font-size:15px}
input:focus{outline:none;border-color:var(--acc)}
button{width:100%;min-height:44px;margin-top:18px;background:#2b3038;color:var(--fg);
  border:1px solid var(--line);border-radius:8px;font-size:15px;cursor:pointer}
button:active{background:#333941}
.err{margin-top:14px;padding:9px 11px;border-radius:8px;font-size:13px;
  color:var(--err);background:#2a1f1d;border:1px solid #4a3330}
.tip{margin-top:16px;font-size:12px;color:var(--dim);line-height:1.7}
"""


def login_page(title: str = "登录", next_url: str = "/", err: str = "",
               user: str = "") -> str:
    err_html = f'<div class="err">{html.escape(err)}</div>' if err else ""
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>{html.escape(title)} · 登录</title>
<style>{_LOGIN_CSS}</style></head><body>
<form class="box" method="post" action="/login" autocomplete="on">
  <h1>{html.escape(title)}</h1>
  <div class="sub">用 Kavita (:5000) 的账号密码登录</div>
  <input type="hidden" name="next" value="{html.escape(next_url or '/')}">
  <label for="u">用户名</label>
  <input id="u" name="username" value="{html.escape(user or '')}"
         autocomplete="username" autocapitalize="none" autocorrect="off" autofocus>
  <label for="p">密码</label>
  <input id="p" name="password" type="password" autocomplete="current-password">
  <button type="submit">登录</button>
  {err_html}
  <div class="tip">与 Kavita 同一套凭据; 会话 {max(1, ttl() // 86400)} 天内有效。</div>
</form></body></html>
"""


def status() -> dict:
    d = _load()
    return {"enabled": enabled(), "kavita": kavita_url(), "dir": str(state_dir()),
            "ttl": ttl(), "cached_users": sorted((d.get("verifier") or {}).keys())}
