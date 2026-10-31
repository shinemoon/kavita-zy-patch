"""5012 控制台的登录门集成测试 (真起一个 ThreadingHTTPServer).

跑法:  cd /vol2/1001/zhangyue-bridge/sync && python3 tests/test_web_gate.py

验的是 web.Handler 里的分流, 不是 webauth 本身 (那是 test_webauth.py):
  * 未登录 GET /            -> 303 /login?next=...
  * 未登录 GET /api/*       -> 401 JSON
  * /healthz                -> 200 (heal 靠它判活)
  * /zy/api/*               -> 不被登录门拦 (转给补丁后端, 那边 401/403 都算"过了门")
  * POST /login 密码对      -> 303 + Set-Cookie, 之后能进首页
  * POST /login 密码错      -> 401
  * /logout                 -> 清 cookie
"""
from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
import threading
import urllib.parse
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TMP = Path(tempfile.mkdtemp())
os.environ["ZY_WEBAUTH_DIR"] = str(TMP)
os.environ.pop("ZY_WEBAUTH_DISABLE", None)


# ---- 假 Kavita (只要 /api/Account/login) --------------------------------

class _MockKavita(ThreadingHTTPServer):
    daemon_threads = True


def _start_mock():
    from http.server import BaseHTTPRequestHandler

    class H(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            try:
                d = json.loads(self.rfile.read(n) or b"{}")
            except Exception:  # noqa: BLE001
                d = {}
            ok = (d.get("username") == "demo" and d.get("password") == "demo123")
            body = b'{"token":"jwt"}' if ok else b"nope"
            self.send_response(200 if ok else 401)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = _MockKavita(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


MOCK = _start_mock()
os.environ["ZY_WEBAUTH_KAVITA"] = f"http://127.0.0.1:{MOCK.server_address[1]}"

import web  # noqa: E402 — 必须在 env 设好之后导入 (webauth 是运行时读 env, 这里只为保险)

SRV = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
SRV.daemon_threads = True
threading.Thread(target=SRV.serve_forever, daemon=True).start()
PORT = SRV.server_address[1]


def _req(path, method="GET", form=None, cookie=None):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=15)
    h, body = {}, None
    if form is not None:
        body = urllib.parse.urlencode(form)
        h["Content-Type"] = "application/x-www-form-urlencoded"
    if cookie:
        h["Cookie"] = cookie
    c.request(method, path, body=body, headers=h)
    r = c.getresponse()
    data = r.read()
    hd = dict(r.getheaders())
    c.close()
    return r.status, hd, data


def _login(user="demo", password="demo123", nxt="/"):
    st, hd, _ = _req("/login", "POST", {"username": user, "password": password, "next": nxt})
    return st, (hd.get("Set-Cookie") or "").split(";")[0]


# ---- tests -------------------------------------------------------------

def test_index_redirects_to_login(tmp=None):
    st, hd, _ = _req("/")
    assert st == 303, st
    assert hd.get("Location") == "/login?next=/", hd.get("Location")


def test_api_returns_401_json(tmp=None):
    st, _, d = _req("/api/state")
    assert st == 401 and json.loads(d)["login"] == "/login", (st, d[:60])


def test_healthz_open(tmp=None):
    st, _, d = _req("/healthz")
    assert st == 200 and json.loads(d)["ok"] is True


def test_login_page_open(tmp=None):
    st, _, d = _req("/login")
    assert st == 200 and b'name="password"' in d and "Kavita".encode() in d


def test_zy_proxy_not_gated(tmp=None):
    # 后端没起/没 token 时会是 403 或 502, 但**不是**登录门的 401
    st, _, d = _req("/zy/api/health")
    assert st != 401 or b"unauthorized" not in d, (st, d[:60])


def test_login_wrong_password(tmp=None):
    st, ck = _login(password="bad")
    assert st == 401 and not ck, (st, ck)


def test_login_ok_then_browse(tmp=None):
    st, ck = _login()
    assert st == 303 and ck.startswith("zy_webauth="), (st, ck)
    st, _, d = _req("/", cookie=ck)
    assert st == 200 and b"<!doctype html>" in d[:40].lower(), st
    st, _, d = _req("/api/state", cookie=ck)
    assert st == 200 and b"links" in d, (st, d[:60])


def test_post_action_needs_cookie(tmp=None):
    st, _, _ = _req("/api/run/pull", "POST", {})
    assert st == 401, st


def test_logout_clears_cookie(tmp=None):
    st, ck = _login()
    st, hd, _ = _req("/logout", cookie=ck)
    assert st == 303 and "Max-Age=0" in (hd.get("Set-Cookie") or ""), (st, hd)


def test_next_kept_in_login_form(tmp=None):
    st, _, d = _req("/login?next=" + urllib.parse.quote("/api/state"))
    assert st == 200 and b'/api/state' in d


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except AssertionError as e:
                fails += 1
                print("FAIL", name, e)
    SRV.shutdown()
    MOCK.shutdown()
    raise SystemExit(1 if fails else 0)
