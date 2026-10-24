#!/usr/bin/env python3
"""探测 reading report 的书 key 形态: 哪个 key 才落到 app 自己的数据桶。

用途: 确认 local_bid 的规范形式 (32hex_local / 64hex / 64hex_local / 大小写),
不写任何数据, 只读 read_report/book。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sync"))

from sync import zy_time  # noqa: E402

CASES = {
    "狐的故事": "6818BFD9C92C8576A0E21AD13CD28CC28B4CAD7E27E236AA2B64CDE9D137FBCE",
    "剑来": "70776A64C32C1C065DAB241F3E9F133A55C7D8B38C5C6FBC79271E87E00E9526",
}


def variants(bid: str) -> dict[str, str]:
    b32 = bid[:32]
    return {
        "32up_local": f"{b32.upper()}_local",
        "32low_local": f"{b32.lower()}_local",
        "64up": bid.upper(),
        "64low": bid.lower(),
        "64up_local": f"{bid.upper()}_local",
    }


for name, bid in CASES.items():
    print(f"===== {name} ({bid[:32]})")
    for label, key in variants(bid).items():
        try:
            body = zy_time.book_report(key)
            days = zy_time.book_days(key)
            total = sum(days.values())
            print(f"  {label:12s} read={body.get('read')!r:>8} days_cnt={body.get('read_days_cnt')!r:>4} "
                  f"sum_days={total:5d} name={(body.get('book_info') or {}).get('name')!r}")
        except Exception as e:  # noqa: BLE001
            print(f"  {label:12s} ERROR {e}")
