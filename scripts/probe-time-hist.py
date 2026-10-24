#!/usr/bin/env python3
"""探测 reading_time/report 的日期归属: 历史日期 key 是否被服务端尊重。

只写 1 分钟 (指定 hour:minute), 落到指定日期, 不碰其它分钟。

用法:
    python3 scripts/probe-time-hist.py <book_id64> <YYYY-MM-DD> <HH:MM> [--dry]
"""
import gzip
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sync"))

from sync import zy_time  # noqa: E402
from sync.config import load_zy_auth, zy_query_params  # noqa: E402


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry" in sys.argv
    if len(args) < 3:
        print(__doc__)
        return 2
    bid, day_s, hhmm = args[0], args[1], args[2]
    hh, mm = (int(x) for x in hhmm.split(":"))
    key = zy_time.book_key(bid)

    before = zy_time.book_days(key)
    today = date.today().isoformat()
    print(f"key={key}")
    print(f"before: {day_s}={before.get(day_s)} today({today})={before.get(today)} total={sum(before.values())}")

    bit = zy_time.bit_index(hh, mm, 0)
    d_b64 = zy_time.bitmap_from_minutes([bit])
    data = {day_s.replace("-", ""): [{
        "bid": 0,
        "local_bid": key,
        "read_percent": 0.3,
        "res": "R",
        "d": d_b64,
    }]}
    payload = {
        "usr": load_zy_auth()["usr"],
        "data": json.dumps(data, ensure_ascii=False),
        "timestamp": str(int(time.time() * 1000)),
        "sign": "",
    }
    if dry:
        print("dry payload:", payload["data"])
        return 0

    auth = load_zy_auth()
    qs = urllib.parse.urlencode(zy_query_params(auth, {}))
    body = gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    req = urllib.request.Request(f"{zy_time.UPLOAD_URL}?{qs}", data=body, method="POST")
    req.add_header("Content-Type", "application/octet-stream")
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    print("resp:", raw.decode("utf-8", "replace")[:400])

    time.sleep(1.5)
    after = zy_time.book_days(key)
    print(f"after : {day_s}={after.get(day_s)} today({today})={after.get(today)} total={sum(after.values())}")
    changed = {k: (before.get(k, 0), v) for k, v in after.items() if v != before.get(k, 0)}
    print("changed days:", changed or "NONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
