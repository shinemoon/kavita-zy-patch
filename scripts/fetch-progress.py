#!/usr/bin/env python3
"""拉取掌阅云端阅读进度 (getProcess)。

端点来自 jadx 反编译:
  GET https://icloud.zhangyue.com/cloud/storage2/safe/getProcess?bookIds=<ids>
  + Account.j()  ->  zysid=<sid>&usr=<usr>&rgt=<token>&p1=<gt>
  + Device.h()    ->  pc=10&p2=..&p3=..&p4=..&p5=..&p7=..&p9=..&p16=..&p21=..
                     p22=..&p25=..&p28=..&p29=..&p31=..&p33=com.chaozh.iReaderFree&p34=..
"""
import json, os, sys, urllib.parse, urllib.request

BASE = "https://icloud.zhangyue.com/cloud/storage2/safe"
AUTH = "/vol2/1001/zhangyue-bridge/.auth.json"


def device_params(a):
    return {
        "pc": "10",
        "p2": a["p2"],
        "p3": a["p3"],
        "p4": a["p4"],
        "p5": "0",
        "p7": "null",
        "p9": "wifi",
        "p16": "PC-Bridge",
        "p21": "wifi",
        "p22": "13",
        "p25": "10000",
        "p28": "0",
        "p29": a["p29"],
        "p31": "null",
        "p33": a.get("p33", "com.chaozh.iReaderFree"),
        "p34": "bridge",
    }


def account_params(a):
    return {"zysid": a["zysid"], "usr": a["usr"], "rgt": a.get("rgt", ""), "p1": a.get("p1", "null")}


def build(a, book_ids):
    q = {}
    q.update(account_params(a))
    q.update(device_params(a))
    q["bookIds"] = book_ids
    return BASE + "/getProcess?" + urllib.parse.urlencode(q, quote_via=urllib.parse.quote)


def get(path):
    req = urllib.request.Request(path, headers={
        "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 13)",
        "Accept": "*/*",
    })
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read().decode("utf-8", "replace")


if __name__ == "__main__":
    a = json.load(open(AUTH))
    ids = sys.argv[1] if len(sys.argv) > 1 else ""
    url = build(a, ids)
    print("URL:", url[:180], "...\n")
    try:
        body = get(url)
    except Exception as e:
        print("ERR", type(e).__name__, e)
        sys.exit(1)
    print("RAW:", body[:1200])