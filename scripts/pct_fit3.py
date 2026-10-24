#!/usr/bin/env python3
"""精确定位掌阅 readPercent 公式。

已得线索:
  - 掌阅 chapterId = epub spine 下标 - 1 (spine[0]=cover 不计)
  - 分子 = 已读章字节累计 + 章内偏移 f3 (readPosition 第 4 段)
  - 分母 = 全书字节
"""
import json
import re
import zipfile
from pathlib import Path

BOOKS = Path("/vol2/1001/dav/Books/掌阅")
ZP = Path("/vol2/1001/zhangyue-bridge/state/zp.json")

BOOK_MAP = {
    "6818BFD9C92C8576A0E21AD13CD28CC28B4CAD7E27E236AA2B64CDE9D137FBCE": "狐的故事.epub",
    "DD2F1C22349AF31D06991F85F62D07057D42CE2F7A1EF527B2727BFF086119DF": "路邊的藤井_-_卷01.epub",
    "7B4CC4A3CAE761D3057D855C22CA697069A75D71F141039946161773E8728AE8": "路邊的藤井_-_卷02.epub",
    "5D250B5E8EA7F238116FF483B52033F7C10A1125309CAA788D98CA2AE7148C3A": "路邊的藤井_-_卷03.epub",
}


def spine_bytes(path):
    z = zipfile.ZipFile(path)
    opf = [n for n in z.namelist() if n.lower().endswith(".opf")][0]
    t = z.read(opf).decode("utf-8", "replace")
    base = opf.rsplit("/", 1)[0] + "/" if "/" in opf else ""
    items = {}
    for m in re.finditer(r"<item\b[^>]*>", t):
        i = re.search(r'\bid="([^"]+)"', m.group(0))
        h = re.search(r'\bhref="([^"]+)"', m.group(0))
        if i and h:
            items[i.group(1)] = base + h.group(1)
    sm = re.search(r"<spine\b[^>]*>(.*?)</spine>", t, re.S)
    hrefs = [items[r] for r in re.findall(r'idref="([^"]+)"', sm.group(1)) if r in items]
    out = []
    for h in hrefs:
        try:
            out.append(len(z.read(h)))
        except KeyError:
            out.append(0)
    z.close()
    return hrefs, out


def parse_pos(pos):
    """`6_0_0$##3-0-6-11752-0##$` -> {'pre':'6_0_0','f':['3','0','6','11752','0']}"""
    m = re.match(r"^(.*?)\$##(.*?)##\$$", pos)
    if not m:
        return None
    return {"pre": m.group(1), "f": m.group(2).split("-")}


def main():
    zp = json.loads(ZP.read_text())
    for rec in zp["body"]:
        bid = rec["bookId"]
        rp = rec["readProcess"]
        if bid not in BOOK_MAP:
            continue
        name = BOOK_MAP[bid]
        hrefs, blen = spine_bytes(BOOKS / name)
        ch = int(rp["chapterId"])
        pct = float(rp["readPercent"])
        f3 = int(parse_pos(rp["readPosition"])["f"][3])
        n = len(blen)

        variants = {
            "A 全书字节, 分子 spine[0..ch] + f3": (sum(blen), sum(blen[:ch + 1]) + f3),
            "B 跳过 cover, spine[1..ch+1] + f3": (sum(blen[1:]), sum(blen[1:ch + 2]) + f3),
            "C 全书字节, 分子 spine[0..ch]": (sum(blen), sum(blen[:ch + 1])),
            "D 跳过 cover, spine[1..ch+1]": (sum(blen[1:]), sum(blen[1:ch + 2])),
            "E 全书字节, 分子 spine[0..ch] + f3, T=spine[1:]": (sum(blen[1:]), sum(blen[:ch + 1]) + f3),
        }
        print(f"=== {bid[:8]} {name} spine={n} totalCount={rp['totalCount']} ch={ch} f3={f3} pct={pct}")
        for k, (T, num) in variants.items():
            got = num / T if T else 0
            d = got - pct
            print(f"    {k:42} num={num:9d} T={T:9d} -> {got:.9f}  diff={d:+.9f}")
        print()


if __name__ == "__main__":
    main()