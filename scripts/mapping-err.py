#!/usr/bin/env python3
"""量化 Kavita(页) ↔ 掌阅(字节) 的映射误差。

现状: Kavita 一本书 = 1 个 chapter, PagesRead 是"已读第几篇 spine 文档"。
      掌阅 readPercent 是字节比例。两者口径不同 → 换算必然有误差。

本脚本: 对每本掌阅书, 把 Kavita 的 pages_read 当成"读到第 N 篇文档",
      转成字节位置, 与掌阅真实位置比, 报告差多少字节 / 折合多少字符。

目标: 200-300 字符以内。
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/vol2/1001/zhangyue-bridge/sync")
from sync import zpct  # noqa: E402

KAVITA_DB = "/vol1/1001/APPS/kavita/kavita.db"
ZP = Path("/vol2/1001/zhangyue-bridge/state/zp.json")
BOOKS = Path("/vol2/1001/dav/Books/掌阅")

BOOK_MAP = {
    "6818BFD9C92C8576A0E21AD13CD28CC28B4CAD7E27E236AA2B64CDE9D137FBCE": "狐的故事.epub",
    "70776A64C32C1C065DAB241F3E9F133A55C7D8B38C5C6FBC79271E87E00E9526": "剑来（1-54册）完结精校版.epub",
    "DD2F1C22349AF31D06991F85F62D07057D42CE2F7A1EF527B2727BFF086119DF": "路邊的藤井_-_卷01.epub",
    "7B4CC4A3CAE761D3057D855C22CA697069A75D71F141039946161773E8728AE8": "路邊的藤井_-_卷02.epub",
    "5D250B5E8EA7F238116FF483B52033F7C10A1125309CAA788D98CA2AE7148C3A": "路邊的藤井_-_卷03.epub",
}
SERIES_MAP = {"狐的故事.epub": 26, "剑来（1-54册）完结精校版.epub": 27}


def kavita_snapshot() -> sqlite3.Connection:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    s = sqlite3.connect(f"file:{KAVITA_DB}?mode=ro", uri=True)
    d = sqlite3.connect(tmp)
    s.backup(d)
    s.close()
    d.row_factory = sqlite3.Row
    return d


def main():
    db = kavita_snapshot()
    zp = json.loads(ZP.read_text())
    print(f"{'book':26} {'kav_pages':>9} {'zy_ch':>5} {'zy_pct':>12} "
          f"{'kav_derived_pct':>15} {'err_pct':>11} {'err_bytes':>10}")
    print("-" * 100)
    worst = 0.0
    for rec in zp["body"]:
        bid = rec["bookId"]
        rp = rec["readProcess"]
        if bid not in BOOK_MAP:
            continue
        name = BOOK_MAP[bid]
        g = zpct.load_geometry(BOOKS / name)
        sid = SERIES_MAP.get(name)
        kav_pages = kav_total = None
        if sid:
            r = db.execute("""SELECT ap.PagesRead, c.Pages FROM AppUserProgresses ap
                              JOIN Chapter c ON c.Id=ap.ChapterId
                              WHERE ap.SeriesId=?""", (sid,)).fetchone()
            if r:
                kav_pages, kav_total = r["PagesRead"], r["Pages"]
        zy_pct = float(rp["readPercent"])
        if kav_total:
            # Kavita 页 = spine 文档下标。读到第 N 页 => spine[0..N-1]
            idx = min(max(0, kav_pages), len(g.sizes) - 1)
            cum = sum(g.sizes[:idx])
            derived = cum / g.total_bytes if g.total_bytes else 0.0
            err_bytes = (derived - zy_pct) * g.total_bytes
            print(f"{name[:26]:26} {kav_pages:>4}/{kav_total:<4} {rp['chapterId']:>5} "
                  f"{zy_pct:>12.8f} {derived:>15.8f} {derived-zy_pct:>+11.6f} {err_bytes:>+10.0f}")
            worst = max(worst, abs(err_bytes))
        else:
            print(f"{name[:26]:26} {'-':>9} {rp['chapterId']:>5} {zy_pct:>12.8f} "
                  f"{'(Kavita 无此书)':>15}")
    print()
    print(f"最坏字节误差: {worst:.0f}  (剑来 total_bytes≈41907094, "
          f"折合中文 {worst/3:.0f} 字)")
    print(f"占全书比例: {worst/41907094*100:.4f}%")
    db.close()


if __name__ == "__main__":
    main()