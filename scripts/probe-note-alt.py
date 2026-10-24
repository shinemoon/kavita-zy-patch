#!/usr/bin/env python3
"""探「拿云端划线正文」的替代通道 (downloadDataV2 返空时的备选)。"""
import gzip
import json
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, "/vol2/1001/zhangyue-bridge/sync")
from sync import zy_notes as Z  # noqa: E402

AUTH = Z.load_zy_auth()
QS = {"usr": AUTH["usr"], "zysid": AUTH["zysid"],
      "p29": AUTH.get("p29", "zye5b814"), "p2": AUTH.get("p2", "104583"),
      "p3": AUTH.get("p3", "101010010"), "p4": AUTH.get("p4", "501603")}
BOOK = sys.argv[1] if len(sys.argv) > 1 else "70776a64c32c1c065dab241f3e9f133a"


def fetch(url, data=None, label=""):
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    if data:
        req.add_header("Content-Type", "application/json")
        req.add_header("Content-Encoding", "gzip")
    req.add_header("Accept-Encoding", "gzip")
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read()
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            txt = raw.decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        print(f"--- {label}: ERR {e}")
        return
    try:
        d = json.loads(txt)
    except Exception:  # noqa: BLE001
        print(f"--- {label}: non-json {txt[:200]}")
        return
    brief = json.dumps(d, ensure_ascii=False)
    print(f"--- {label}: {brief[:400]}")


def g(path_qs, base="https://icloud.zhangyue.com/cloud/storage2/safe"):
    fetch(base + path_qs + ("&" if "?" in path_qs else "?") + urllib.parse.urlencode(QS), label=path_qs)


g("/getUserNotebooks?queryType=2")
g("/getUserNotebooks?bookid=" + BOOK)
g("/getUserNotebooks?bookid=" + BOOK + "&queryType=2")
g("/getUserNotebooks?bookId=" + BOOK + "&queryType=2")

# old UGC note api
ugc = ("https://ebook.ugc.corp3g.cn/zybk/api/note/index?" +
       urllib.parse.urlencode({**QS, "act": "GetList", "bookid": BOOK}))
fetch(ugc, label="ugc GetList")
ugc2 = ("https://ebook.ugc.corp3g.cn/zybk/api/note/index?" +
        urllib.parse.urlencode({**QS, "act": "GetList", "bookId": BOOK, "chapterId": "0"}))
fetch(ugc2, label="ugc GetList camel")
