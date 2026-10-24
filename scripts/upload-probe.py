#!/usr/bin/env python3
"""掌阅 uploadProcess 写入探针。用法: python3 upload-probe.py <bookId> <chapterId> <percent> [position]"""
import json, sys, time, urllib.request, urllib.error, urllib.parse

AUTH = {
    "p29": "zye5b814", "p2": "104583", "p3": "101010010", "p4": "501603",
    "p33": "com.chaozh.iReaderFree", "usr": "__ZY_USR__",
    "zysid": "__ZY_ZYSID__",
}
GET = "https://icloud.zhangyue.com/cloud/storage2/safe/getProcess"
PUT = "https://icloud.zhangyue.com/cloud/storage2/safe/uploadProcess"


def qs(extra=None):
    p = dict(AUTH)
    if extra:
        p.update(extra)
    return urllib.parse.urlencode(p)


def read_progress(book_id):
    url = GET + "?" + qs({"bookIds": book_id})
    with urllib.request.urlopen(url, timeout=25) as r:
        raw = r.read().decode("utf-8", "replace")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw_head": raw[:200], "_raw_len": len(raw)}


def write_progress(book_id, book_name, chapter_id, percent, position, total, source="web"):
    now = str(int(time.time() * 1000))
    body = {
        "usr": AUTH["usr"],
        "bookId": book_id,
        "bookName": book_name,
        "deviceName": "ClaudBridge",
        "updateTime": now,
        "readProcess": {
            "readPosition": position,
            "readPercent": str(percent),
            "lastReadDate": now,
            "totalCount": str(total),
            "chapterId": str(chapter_id),
            "readComplete": "0",
            "bookOverStatus": 0,
            "isCartoon": 0,
            "isFineBook": 1,
            "readingSource": source,
        },
    }
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(PUT + "?" + qs(), data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return {"status": r.status, "body": r.read().decode("utf-8", "replace")[:400]}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "body": e.read().decode("utf-8", "replace")[:400]}
    except Exception as e:
        return {"error": repr(e)}


if __name__ == "__main__":
    bid = sys.argv[1]
    name = sys.argv[2] if len(sys.argv) > 2 else "?"
    if sys.argv[3] == "get":
        print(json.dumps(read_progress(bid), ensure_ascii=False, indent=1))
    else:
        ch = sys.argv[3]
        pct = sys.argv[4]
        pos = sys.argv[5] if len(sys.argv) > 5 else "1_0_0$##0-0-1-100-0##$"
        tot = sys.argv[6] if len(sys.argv) > 6 else "8"
        print("BEFORE:", json.dumps(read_progress(bid), ensure_ascii=False))
        print("WRITE :", json.dumps(write_progress(bid, name, ch, pct, pos, tot), ensure_ascii=False))
        time.sleep(1.5)
        print("AFTER :", json.dumps(read_progress(bid), ensure_ascii=False))
