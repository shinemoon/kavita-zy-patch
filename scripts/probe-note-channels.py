#!/usr/bin/env python3
"""探「云端划线正文」所有可能通道 (ah2 legacy / api-pc 猜测)。"""
import gzip
import json
import ssl
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, "/vol2/1001/zhangyue-bridge/sync")
from sync import zy_notes as Z  # noqa: E402

AUTH = Z.load_zy_auth()
QS = {"usr": AUTH["usr"], "zysid": AUTH["zysid"],
      "p29": AUTH.get("p29", "zye5b814"), "p2": AUTH.get("p2", "104583"),
      "p3": AUTH.get("p3", "101010010"), "p4": AUTH.get("p4", "501603")}
BOOK32 = sys.argv[1] if len(sys.argv) > 1 else "70776a64c32c1c065dab241f3e9f133a"
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def get(url, label):
    try:
        req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip",
                                                   "User-Agent": "iReader/8.4.0"})
        with urllib.request.urlopen(req, timeout=25, context=CTX) as r:
            raw = r.read()
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            txt = raw.decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        print(f"--- {label}: ERR {type(e).__name__} {e}")
        return None
    print(f"--- {label}: {txt[:500]}")
    return txt


def q(url, extra=None):
    params = {**QS, **(extra or {})}
    return url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)


# 1. legacy cloud notebook list (App 的「云端笔记本」)
for act in ("GetList",):
    get(q("https://ah2.zhangyue.com/zybk/api/note/index", {"act": act}), f"ah2 notebook {act}")

# 2. ah2 带 bookid
get(q("https://ah2.zhangyue.com/zybk/api/note/index",
      {"act": "GetList", "bookid": BOOK32}), "ah2 notebook GetList + bookid")

# 3. api-pc 猜测 (与 bookshelf 同风格)
for path in ("/bookstore/open/cloud/note/list", "/bookstore/open/cloud/notes/list",
             "/bookstore/open/cloud/bookshelf/notes", "/bookstore/open/user/note/list",
             "/bookstore/open/cloud/mark/list", "/bookstore/open/cloud/note/get"):
    get(q("https://api-pc.zhangyue.com" + path, {"bookid": BOOK32}), "api-pc " + path)
