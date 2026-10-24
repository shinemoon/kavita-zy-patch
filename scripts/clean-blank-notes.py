#!/usr/bin/env python3
"""清掉正文为空的划线 (零长度选择那次) 以便重拉补上上下文。"""
import sqlite3
import sys

sys.path.insert(0, "/vol2/1001/zhangyue-bridge/sync")
from sync.config import KAVITA_DB, STATE_DB  # noqa: E402

c = sqlite3.connect(STATE_DB)
c.row_factory = sqlite3.Row
rows = c.execute("select * from zy_note_link where kind='note'").fetchall()
kc = sqlite3.connect(KAVITA_DB)
kill = []
for r in rows:
    aid = r["kavita_annotation_id"]
    if not aid:
        continue
    t = kc.execute("select length(SelectedText) from AppUserAnnotation where Id=?",
                   (aid,)).fetchone()
    if t and t[0] == 0:
        kill.append((r["kind"], r["zy_book_id"], r["zy_key"], int(aid)))
print("blank rows:", [(k[0], k[2][:30], k[3]) for k in kill])
for kind, bid, key, aid in kill:
    kc.execute("delete from AppUserAnnotation where Id=?", (aid,))
    c.execute("delete from zy_note_link where kind=? and zy_book_id=? and zy_key=?",
              (kind, bid, key))
kc.commit()
c.commit()
kc.close()
c.close()
print("cleaned", len(kill))
