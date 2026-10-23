#!/usr/bin/env python3
"""验证自签 JWT 能不能过 Kavita 的 /api/Account。"""
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("mj", str(Path(__file__).resolve().parent / "make-jwt.py"))
mj = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(mj)
first_user = mj.first_user  # noqa: E402

APP = Path("/vol1/1001/APPS/kavita/appsettings.json")


def variants(u: dict) -> list[tuple[str, dict]]:
    now = 1792575617
    roles = ["Admin", "Download", "ChangeRestriction"]
    base = {"authTime": now, "exp": now + 36000, "iat": now, "nbf": now - 60,
            "iss": "Kavita", "aud": ["Kavita"]}
    v = []
    v.append(("sub+email+roles", {**base, "name": u["UserName"], "sub": u["UserName"],
                                  "email": u["Email"], "roles": roles}))
    v.append(("ClaimTypes 展开", {**base,
                                 "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name": u["UserName"],
                                 "email": u["Email"],
                                 "http://schemas.microsoft.com/ws/2008/06/identity/claims/role": roles}))
    v.append(("username+role 单数", {**base, "username": u["UserName"], "role": roles[0],
                                     "email": u["Email"], "name": u["UserName"]}))
    v.append(("sub only", {**base, "sub": u["UserName"]}))
    return v


def main():
    import base64, hashlib, hmac

    def b64(d: bytes) -> bytes:
        return base64.urlsafe_b64encode(d).rstrip(b"=")

    key = base64.b64decode(json.loads(APP.read_text())["TokenKey"])
    u = first_user()

    for label, payload in variants(u):
        h = b64(json.dumps({"alg": "HS512", "typ": "JWT"},
                           separators=(",", ":")).encode())
        p = b64(json.dumps(payload, separators=(",", ":"),
                           ensure_ascii=False).encode())
        sig = hmac.new(key, h + b"." + p, hashlib.sha512).digest()
        tok = (h + b"." + p + b"." + b64(sig)).decode()

        req = urllib.request.Request(
            "http://127.0.0.1:5000/api/Account",
            headers={"Authorization": f"Bearer {tok}", "X-Kavita-Client": "e2e"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                body = r.read().decode()
                print(f"[OK  ] {label}  -> {body[:120]}")
                print(f"       TOKEN_OK={tok}")
                Path("/tmp/kavita-jwt.txt").write_text(tok)
                return 0
        except urllib.error.HTTPError as e:
            print(f"[{e.code:3d}] {label}  {e.read().decode()[:100]}")
        except Exception as e:
            print(f"[ERR ] {label}  {e}")
    print("NO_VARIANT_WORKED")
    return 1


if __name__ == "__main__":
    sys.exit(main())