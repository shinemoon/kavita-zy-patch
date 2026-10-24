#!/usr/bin/env python3
"""日期归属探测 v3: 一次跑多种变体, 之后单独核对。

变体:
  A  单日期 key + timestamp 也是那天
  B  多日期 key (两天) + timestamp now
  C  单日期 key + res 用 "R" + bid 数字 0 (同 A, 但 minute 不同) —— 校验幂等/最小面

用法: python3 scripts/probe-time-hist3.py <book32hex> [--dry]
"""
import gzip
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sync"))

from sync import zy_time  # noqa: E402
from sync.config import load_zy_auth, zy_query_params  # noqa: E402


def send(key: str, data: dict, ts_ms: int) -> str:
    payload = {"usr": load_zy_auth()["usr"],
               "data": json.dumps(data, ensure_ascii=False),
               "timestamp": str(ts_ms), "sign": ""}
    auth = load_zy_auth()
    qs = urllib.parse.urlencode(zy_query_params(auth, {}))
    body = gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    req = urllib.request.Request(f"{zy_time.UPLOAD_URL}?{qs}", data=body, method="POST")
    req.add_header("Content-Type", "application/octet-stream")
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", "replace")[:200]


def entry(key: str, hh: int, mm: int, pct: float = 0.3) -> dict:
    return {"bid": 0, "local_bid": key, "read_percent": pct, "res": "R",
            "d": zy_time.bitmap_from_minutes([zy_time.bit_index(hh, mm, 0)])}


def ms(d, hh, mm) -> int:
    return int(datetime.combine(d, datetime.min.time()).replace(hour=hh, minute=mm)
               .timestamp() * 1000)


def main() -> int:
    args = [a for a in sys.argv[1:][:1]]
    if not args:
        print(__doc__)
        return 2
    key = zy_time.book_key(args[0])
    dry = "--dry" in sys.argv
    now_ms = int(time.time() * 1000)

    d_a = date(2026, 10, 18)
    d_b1, d_b2 = date(2026, 10, 16), date(2026, 10, 17)

    jobs = [
        ("A key10-18 ts10-18 09:11", {d_a.strftime("%Y%m%d"): [entry(key, 9, 11)]}, ms(d_a, 9, 11)),
        ("B key10-16+10-17 ts-now", {d_b1.strftime("%Y%m%d"): [entry(key, 9, 12)],
                                     d_b2.strftime("%Y%m%d"): [entry(key, 9, 13)]}, now_ms),
    ]
    for name, data, ts in jobs:
        print(f"--- {name}")
        print("   data:", json.dumps(data, ensure_ascii=False))
        if dry:
            continue
        t0 = time.time()
        print("   resp:", send(key, data, ts), f"({time.time() - t0:.1f}s)")
    print("key:", key)
    return 0


if __name__ == "__main__":
    sys.exit(main())
