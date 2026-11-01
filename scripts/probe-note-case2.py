"""对比: 剑来 大/小写 book32 下的笔记条目, 与本地 zy_note_link 的对应关系。"""
import json
import sqlite3
import sys

sys.path.insert(0, "/vol2/1001/zhangyue-bridge/sync")
from sync import zy_notes  # noqa: E402

BID = "70776A64C32C1C065DAB241F3E9F133A55C7D8B38C5C6FBC79271E87E00E9526"

for case in ("lower", "upper"):
    k = BID[:32].lower() if case == "lower" else BID[:32].upper()
    row = zy_notes.data_ver(k)
    print(f"=== {case} {k} keys={list(row.keys())}")
    for n in row.get("booknotesVerList") or []:
        print("  note", json.dumps(n, ensure_ascii=False))
    for m in row.get("bookmarksVerList") or []:
        print("  mark", json.dumps(m, ensure_ascii=False))
    for d in row.get("delList") or []:
        print("  del ", json.dumps(d, ensure_ascii=False)[:200])

c = sqlite3.connect("/vol2/1001/zhangyue-bridge/state/sync.sqlite")
c.row_factory = sqlite3.Row
print("=== local zy_note_link")
for r in c.execute("select kind, bookid, key, series_id, chapter_id, marktime, source from zy_note_link"):
    print(dict(r))
