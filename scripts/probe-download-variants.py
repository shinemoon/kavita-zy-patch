#!/usr/bin/env python3
"""探 downloadDataV2 为何返空: 变体矩阵, 每个只打摘要。"""
import gzip
import json
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, "/vol2/1001/zhangyue-bridge/sync")
from sync import zy_notes as Z  # noqa: E402

EMPTY = "670b14728ad9902aecba32e22fa4f6bd"
BASE = "https://icloud.zhangyue.com/cloud/storage2/safe"


def call(path, body, extra_headers=None):
    auth = Z.load_zy_auth()
    params = {"usr": auth["usr"], "zysid": auth["zysid"],
              "p29": auth.get("p29", "zye5b814"), "p2": auth.get("p2", "104583"),
              "p3": auth.get("p3", "101010010"), "p4": auth.get("p4", "501603")}
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    raw = gzip.compress(json.dumps(body).encode("utf-8"))
    req = urllib.request.Request(url, data=raw, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Content-Encoding", "gzip")
    req.add_header("Accept-Encoding", "gzip")
    for k, v in (extra_headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            data = gzip.decompress(data)
    try:
        return json.loads(data)
    except Exception:
        return {"_raw": data[:300].decode("utf-8", "replace")}


def pick(book32):
    v = Z.data_ver(book32)
    notes = [{"uniquecheck": it.get("uniquecheck"), "marktime": it.get("marktime")}
             for it in (v.get("booknotesVerList") or [])]
    marks = [{"uniquecheck": it.get("uniquecheck"), "marktime": it.get("marktime")}
             for it in (v.get("bookmarksVerList") or [])]
    return v, notes, marks


def variant(name, body):
    d = call("/downloadDataV2", body)
    res = d.get("res")
    summary = {"code": d.get("code"), "status": d.get("status"), "msg": d.get("msg")}
    if isinstance(res, dict) and res:
        summary["res_keys"] = list(res.keys())
        summary["notes"] = len(res.get("booknotes") or [])
        summary["marks"] = len(res.get("bookmarks") or [])
    print(f"--- {name}: {json.dumps(summary, ensure_ascii=False)}")
    return d


def main():
    book = sys.argv[1] if len(sys.argv) > 1 else "70776a64c32c1c065dab241f3e9f133a"
    b32 = book[:32]
    v, notes, marks = pick(b32)
    print("server md5:", v.get("booknotesmd5"), "notes:", len(notes), "del:", len(v.get("delList") or []))

    base = {"usr": Z.load_zy_auth()["usr"], "rgt": Z.load_zy_auth()["zysid"]}

    def vl(**kw):
        entry = {"booknotesmd5": EMPTY, "bookScaleNotesMd5": EMPTY, "bookmarksmd5": EMPTY,
                 "booknotesVerList": [], "bookScaleNotesVerList": [], "bookmarksVerList": []}
        entry.update(kw)
        return {b32: entry}

    variant("A sentinel + empty lists", {**base, "versionlist": vl()})
    variant("B server md5 + empty lists", {**base, "versionlist": vl(
        booknotesmd5=v.get("booknotesmd5"), bookmarksmd5=v.get("bookmarksmd5"))})
    variant("C sentinel + real VerList", {**base, "versionlist": vl(
        booknotesVerList=notes, bookmarksVerList=marks)})
    variant("D add bookWriteNotesMd5", {**base, "versionlist": vl(
        bookWriteNotesMd5=EMPTY,
        booknotesVerList=notes, bookmarksVerList=marks)})
    variant("E 64hex key", {**base, "versionlist": {book: vl()[b32]}})
    variant("F uppercase key", {**base, "versionlist": {b32.upper(): vl()[b32]}})
    variant("G add sign/zyeid/version", {**base, "versionlist": vl(),
                                         "sign": "", "zysid": base["rgt"], "version": "1"})
    for p in ("/downloadData", "/downloadDataV3"):
        d = call(p, {**base, "versionlist": vl()})
        print(f"--- {p}: {json.dumps({k: d.get(k) for k in ('code','status','msg','res')}, ensure_ascii=False)[:300]}")


if __name__ == "__main__":
    main()
