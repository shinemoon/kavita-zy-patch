#!/usr/bin/env python3
"""日期归属决定性探测 (第二版): 按 APK 形态推一个历史日期, 用两个独立视图看落账。

视图 1: read_report/book?book_id=<MD5>_local  (逐日每书分钟)
视图 2: read_report/statistics?date_type=week (账号级逐日分钟, reading_distribution)

用法:
    python3 scripts/probe-time-hist2.py <book32hex> <YYYY-MM-DD> <HH:MM> [--wait N] [--dry]
"""
import gzip
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sync"))

from sync import zy_time  # noqa: E402
from sync.config import load_zy_auth, zy_query_params  # noqa: E402


def snapshot(key: str, around: date) -> dict:
    days = zy_time.book_days(key)
    acc = zy_time.account_days(around - timedelta(days=3), around + timedelta(days=1))
    return {"book": days, "account": acc}


def show(tag: str, snap: dict, day_s: str) -> None:
    today = date.today().isoformat()
    print(f"[{tag}] book {day_s}={snap['book'].get(day_s)}  today={snap['book'].get(today)}"
          f"  total={sum(snap['book'].values())}")
    window = sorted(snap["account"].items())
    print(f"[{tag}] account {window}")


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry" in sys.argv
    wait = 120
    if "--wait" in sys.argv:
        wait = int(sys.argv[sys.argv.index("--wait") + 1])
    if len(args) < 3:
        print(__doc__)
        return 2
    bid, day_s, hhmm = args[0], args[1], args[2]
    hh, mm = (int(x) for x in hhmm.split(":"))
    key = zy_time.book_key(bid)
    target = date.fromisoformat(day_s)

    before = snapshot(key, target)
    show("before", before, day_s)

    bit = zy_time.bit_index(hh, mm, 0)
    data = {day_s.replace("-", ""): [{
        "bid": 0,
        "local_bid": key,
        "read_percent": 0.3,
        "res": "R",
        "d": zy_time.bitmap_from_minutes([bit]),
    }]}
    payload = {
        "usr": load_zy_auth()["usr"],
        "data": json.dumps(data, ensure_ascii=False),
        "timestamp": str(int(time.time() * 1000)),
        "sign": "",
    }
    print("minute-bit:", bit, "key:", key)
    print("data:", payload["data"])
    if dry:
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
    print("resp:", raw.decode("utf-8", "replace")[:300])

    print(f"sleep {wait}s ...")
    time.sleep(wait)
    after = snapshot(key, target)
    show("after", after, day_s)

    chg = {k: (before["book"].get(k, 0), v) for k, v in after["book"].items()
           if v != before["book"].get(k, 0)}
    print("book changed:", chg or "NONE")
    chg2 = {k: (before["account"].get(k, 0), v) for k, v in after["account"].items()
            if v != before["account"].get(k, 0)}
    print("account changed:", chg2 or "NONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
