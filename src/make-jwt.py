#!/usr/bin/env python3
"""签一个 Kavita JWT, 供 e2e 测试注入 localStorage。

Kavita 用 appsettings.json 的 TokenKey 签 HS512 JWT,
claim 结构: {sub: username, email, name, roles, exp, ...}
"""
import base64
import hashlib
import hmac
import json
import sqlite3
import sys
import time
from pathlib import Path

APPSETTINGS = Path("/vol1/1001/APPS/kavita/appsettings.json")
DB = Path("/vol1/1001/APPS/kavita/kavita.db")


def b64(data: bytes) -> bytes:
    return base64.urlsafe_b64encode(data).rstrip(b"=")


def make_jwt(key_b64: str, payload: dict) -> str:
    key = base64.b64decode(key_b64)
    header = {"alg": "HS512", "typ": "JWT"}
    h = b64(json.dumps(header, separators=(",", ":")).encode())
    p = b64(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode())
    sig = hmac.new(key, h + b"." + p, hashlib.sha512).digest()
    return (h + b"." + p + b"." + b64(sig)).decode()


def first_user() -> dict:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    r = con.execute("""
        SELECT u.UserName, u.Email
        FROM AspNetUsers u
        JOIN AspNetUserRoles ur ON ur.UserId = u.Id
        ORDER BY u.Id LIMIT 1
    """).fetchone()
    con.close()
    return dict(r) if r else {}


def main() -> None:
    key = json.loads(APPSETTINGS.read_text())["TokenKey"]
    u = first_user()
    if not u:
        print("NO_USER", file=sys.stderr)
        sys.exit(1)
    now = int(time.time())
    payload = {
        "name": u["UserName"],
        "sub": u["UserName"],
        "email": u["Email"],
        "roles": ["Admin", "Download", "ChangeRestriction"],
        "authTime": now,
        "exp": now + 36000,
        "iat": now,
        "nbf": now - 60,
        "iss": "Kavita",
        "aud": ["Kavita"],
    }
    tok = make_jwt(key, payload)
    print(json.dumps({"user": u, "token": tok}, ensure_ascii=False))


if __name__ == "__main__":
    main()