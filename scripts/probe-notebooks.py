import json
import sys

sys.path.insert(0, "/vol2/1001/zhangyue-bridge/sync")
from sync import zy_notes  # noqa: E402

rows = zy_notes.notebooks()
print("rows:", len(rows))
for r in rows:
    print(json.dumps({
        "bookid": r.get("bookid"),
        "book_name": r.get("book_name") or r.get("bookname"),
        "notenum": r.get("notenum"),
        "marknum": r.get("marknum"),
        "readpercent": r.get("readpercent"),
        "updatetime": r.get("updatetime"),
        "deviceName": r.get("deviceName"),
    }, ensure_ascii=False))
