#!/usr/bin/env python3
"""自测: 逐个打后端 API, 打印结果。"""
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
tok = (ROOT / "state" / "token").read_text().strip()
port = 5111


def call(path, body=None):
    url = f"http://127.0.0.1:{port}{path}?token={tok}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]
    except Exception as e:
        return -1, str(e)


for p in ("/zy/api/health", "/zy/api/selfcheck", "/zy/api/links", "/zy/api/status"):
    code, out = call(p)
    print(f"=== {p}  -> {code}")
    if isinstance(out, dict):
        if p.endswith("selfcheck"):
            for w in out.get("warnings", []):
                print(f"   [{w['level']}] {w['code']}: {w['msg']}")
                if w.get("fix"):
                    print(f"        fix: {w['fix']}")
        elif p.endswith("status"):
            print(f"   auth={out.get('auth_present')} kv_db={out.get('kavita_db_present')}")
            print(f"   last_push={out.get('last_push')}")
            for r in out.get("rows", []):
                zy = r.get("zy") or {}
                print(f"   - sid={r['kavita_series_id']} {r['kavita_name'] or r['zy_name']} "
                      f"kv={r['kv_percent']}% zy={zy.get('pct')}")
            if out.get("zy_error"):
                print(f"   zy_error: {out['zy_error']}")
            if out.get("kv_error"):
                print(f"   kv_error: {out['kv_error']}")
        else:
            print("   " + json.dumps(out, ensure_ascii=False)[:400])
    else:
        print("   " + str(out)[:400])