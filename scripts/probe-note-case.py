"""探查: getUserDataVer 对小写 / 大写 book32 的响应差异 (笔记条数)。"""
import json
import sys

sys.path.insert(0, "/vol2/1001/zhangyue-bridge/sync")
from sync import zy_notes  # noqa: E402

BOOKS = [
    ("26 狐的故事", "6818BFD9C92C8576A0E21AD13CD28CC28B4CAD7E27E236AA2B64CDE9D137FBCE"),
    ("27 剑来", "70776A64C32C1C065DAB241F3E9F133A55C7D8B38C5C6FBC79271E87E00E9526"),
    ("29 藤井01", "DD2F1C22349AF31D06991F85F62D07057D42CE2F7A1EF527B2727BFF086119DF"),
    ("29 藤井02", "7B4CC4A3CAE761D3057D855C22CA697069A75D71F141039946161773E8728AE8"),
    ("29 藤井03", "5D250B5E8EA7F238116FF483B52033F7C10A1125309CAA788D98CA2AE7148C3A"),
    ("30 万古江河", "3749B3442F756DAA98CBD7B49A23F6A2158A8B612753132D856D75D380FD18B0"),
]


def summarize(row):
    if not row:
        return "EMPTY"
    return dict(
        bookid=row.get("bookid"),
        notes=len(row.get("booknotesVerList") or []),
        marks=len(row.get("bookmarksVerList") or []),
        scale=len(row.get("bookScaleNotesVerList") or []),
        del_=len(row.get("delList") or row.get("delItems") or []),
    )


for label, bid in BOOKS:
    for case in ("lower", "upper"):
        k = bid[:32].lower() if case == "lower" else bid[:32].upper()
        try:
            row = zy_notes.data_ver(k)
        except Exception as e:  # noqa: BLE001
            print(f"{label} {case}: ERR {e}")
            continue
        print(f"{label} {case} key={k}: {json.dumps(summarize(row), ensure_ascii=False)}")
