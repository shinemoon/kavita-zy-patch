#!/usr/bin/env python3
"""一次请求里带多个日期 key, 看服务端怎么归日 (探测用, 只写几分钟)。"""
import gzip
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sync"))

from sync import zy_time  # noqa: E402
from sync.config import load_zy_auth, zy_query_params  # noqa: E402


def main() -> int:
    bid = sys.argv[1]
    # argv: 日期=HH:MM ...  例: 2026-10-23=08:02 2026-10-21=08:32
    specs = sys.argv[2:]
    key = zy_time.book_key(bid)
    before = zy_time.book_days(key)
    data: dict[str, list] = {}
    for spec in specs:
        day_s, hhmm = spec.split("=")
        hh, mm = (int(x) for x in hhmm.split(":"))
        data.setdefault(day_s.replace("-", ""), []).append({
            "bid": 0, "local_bid": key, "read_percent": 0.3, "res": "R",
            "d": zy_time.bitmap_from_minutes([zy_time.bit_index(hh, mm, 0)]),
        })
    print("payload dates:", list(data))
    payload = {"usr": load_zy_auth()["usr"], "data": json.dumps(data, ensure_ascii=False),
               "timestamp": str(int(time.time() * 1000)), "sign": ""}
    auth = load_zy_auth()
    qs = urllib.parse.urlencode(zy_query_params(auth, {}))
    body = gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    req = urllib.request.Request(f"{zy_time.UPLOAD_URL}?{qs}", data=body, method="POST")
    req.add_header("Content-Type", "application/octet-stream")
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    print("resp:", raw.decode("utf-8", "replace")[:300])
    for i in range(5):
        time.sleep(9)
        after = zy_time.book_days(key)
        diff = {k: (before.get(k, 0), v) for k, v in after.items() if v != before.get(k, 0)}
        print(f"poll {i}: total {sum(before.values())}->{sum(after.values())} diff={diff or 'NONE'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
