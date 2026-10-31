"""登录保护 (webauth) 测试 —— 复用 Kavita (:5000) 账号密码.

跑法:  cd /vol2/1001/zhangyue-bridge/sync && python3 tests/test_webauth.py

覆盖:
  * cookie 签名 / 过期 / 被篡改 / 换密钥后失效
  * Cookie 头解析 (多 cookie, 前缀干扰)
  * 在线验证: 200 放行, 401 拒绝, 连不上走缓存兜底
  * 缓存 verifier: 密码对不上不放行; 不同用户互不影响
  * 限流: 8 次失败后 nth 次被挡, 清空后恢复
  * 关闭开关 (ZY_WEBAUTH_DISABLE)
  * 登录页转义 (next / 错误文案里的 HTML)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import webauth  # noqa: E402


# ---------------------------------------------------------------- helpers

class _MockKavita(BaseHTTPRequestHandler):
    """最小 Kavita: 只认 /api/Account/login, 用户名 demo / 密码 demo123."""
    calls: list = []

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        try:
            d = json.loads(self.rfile.read(n) or b"{}")
        except Exception:  # noqa: BLE001
            d = {}
        self.calls.append((self.path, d.get("username")))
        ok = (self.path == "/api/Account/login"
              and d.get("username") == "demo" and d.get("password") == "demo123")
        body = b'{"token":"jwt"}' if ok else b"Your credentials are not correct"
        self.send_response(200 if ok else 401)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # noqa: D401 — 静音
        pass


def _serve_mock():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _MockKavita)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _env(tmp: Path, kavita: str = "") -> None:
    os.environ["ZY_WEBAUTH_DIR"] = str(tmp)
    os.environ["ZY_WEBAUTH_KAVITA"] = kavita
    os.environ.pop("ZY_WEBAUTH_DISABLE", None)
    webauth._FAILS.clear()


# ---------------------------------------------------------------- tests

def test_secret_persisted(tmp):
    _env(tmp)
    s1 = webauth._secret()
    s2 = webauth._secret()
    assert s1 == s2 and len(s1) >= 32
    state = json.loads((tmp / webauth.STATE_NAME).read_text("utf-8"))
    assert len(state["secret"]) == 64


def test_token_roundtrip(tmp):
    _env(tmp)
    t = webauth.make_token("claud")
    assert len(t.split(".")) == 4
    assert webauth.parse_token(t) == "claud"


def test_token_unicode_user(tmp):
    _env(tmp)
    t = webauth.make_token("小明·kavita")
    assert webauth.parse_token(t) == "小明·kavita"


def test_token_tampered(tmp):
    _env(tmp)
    t = webauth.make_token("claud")
    assert webauth.parse_token(t[:-1] + ("0" if not t.endswith("0") else "1")) is None
    # 把用户名换成 admin, 签名不变 -> 必须失效
    parts = t.split(".")
    parts[0] = webauth._b64("admin")
    assert webauth.parse_token(".".join(parts)) is None


def test_token_expired(tmp):
    _env(tmp)
    t = webauth.make_token("claud", now=time.time() - webauth.ttl() - 10)
    assert webauth.parse_token(t) is None


def test_token_wrong_secret(tmp):
    _env(tmp)
    t = webauth.make_token("claud")
    other = Path(tempfile.mkdtemp())
    _env(other)                      # 换一份密钥
    assert webauth.parse_token(t) is None


def test_cookie_header_parsing(tmp):
    _env(tmp)
    a = webauth.make_token("a")
    b = webauth.make_token("b")
    assert webauth.current_user(f"x=1; {webauth.COOKIE_NAME}={b}; y=2") == "b"
    assert webauth.current_user(f"{webauth.COOKIE_NAME}={a}") == "a"
    # 长得像但不是 (前缀): 不该被认
    assert webauth.current_user("zy_webauth_x=bad") is None
    assert webauth.current_user("") is None
    assert webauth.current_user(None) is None


def test_disable_switch(tmp):
    _env(tmp)
    os.environ["ZY_WEBAUTH_DISABLE"] = "1"
    try:
        assert webauth.enabled() is False
        assert webauth.current_user(None) == "*"
    finally:
        os.environ.pop("ZY_WEBAUTH_DISABLE", None)
    assert webauth.enabled() is True


def test_verify_online_ok_and_cache(tmp):
    srv, url = _serve_mock()
    try:
        _env(tmp, url)
        ok, msg, via = webauth.verify("demo", "demo123")
        assert ok and via == "kavita", (ok, msg, via)
        # 缓存里应有一条
        assert "demo" in webauth.status()["cached_users"]
        assert webauth.verify_cached("demo", "demo123") is True
        # 关掉 Kavita -> 走缓存
        srv.shutdown()
        ok, msg, via = webauth.verify("demo", "demo123")
        assert ok and via == "cache", (ok, msg, via)
    finally:
        srv.server_close()


def test_verify_wrong_password(tmp):
    srv, url = _serve_mock()
    try:
        _env(tmp, url)
        ok, msg, via = webauth.verify("demo", "nope")
        assert not ok and via == "" and "不对" in msg
    finally:
        srv.shutdown()
        srv.server_close()


def test_offline_without_cache_fails(tmp):
    _env(tmp, "http://127.0.0.1:9")     # 没人监听
    ok, msg, via = webauth.verify("nobody", "whatever")
    assert not ok and "不可达" in msg


def test_cache_is_per_user(tmp):
    srv, url = _serve_mock()
    try:
        _env(tmp, url)
        assert webauth.verify("demo", "demo123")[0]
        srv.shutdown()
        # 另一个用户名不能借 demo 的缓存
        assert webauth.verify("other", "demo123")[0] is False
    finally:
        srv.server_close()


def test_rate_limit(tmp):
    _env(tmp)
    ip = "10.0.0.9"
    for _ in range(webauth._RATE_N - 1):
        webauth.rate_fail(ip)
        assert webauth.rate_blocked(ip) == 0
    webauth.rate_fail(ip)
    assert webauth.rate_blocked(ip) > 0
    # 别的 IP 不受影响
    assert webauth.rate_blocked("10.0.0.10") == 0
    webauth.rate_clear(ip)
    assert webauth.rate_blocked(ip) == 0


def test_cookie_attrs(tmp):
    _env(tmp)
    c = webauth.set_cookie("claud")
    assert c.startswith(f"{webauth.COOKIE_NAME}=")
    for part in ("Path=/", "HttpOnly", "SameSite=Lax", "Max-Age="):
        assert part in c, part
    assert "Secure" not in c          # 内网 http, 加 Secure 就永远发不出去
    assert "Max-Age=0" in webauth.clear_cookie()


def test_login_page_escapes(tmp):
    _env(tmp)
    page = webauth.login_page("掌阅桥接", "/x?a=1&b=<2>", '<script>x</script>', 'a"b')
    assert "&lt;script&gt;" in page and "<script>x</script>" not in page
    assert "Kavita" in page


def test_status_shape(tmp):
    _env(tmp)
    st = webauth.status()
    for k in ("enabled", "kavita", "dir", "ttl", "cached_users"):
        assert k in st, k


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            tmp = Path(tempfile.mkdtemp())
            try:
                fn(tmp)
                print("PASS", name)
            except AssertionError as e:
                fails += 1
                print("FAIL", name, e)
    raise SystemExit(1 if fails else 0)
