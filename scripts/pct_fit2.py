#!/usr/bin/env python3
"""多种字符口径, 逐本反推掌阅 readPercent 的隐含总量 T。

对每本已知 (chapterId, readPercent), 算 implied_T = cum(ch+1) / pct,
再比 implied_T 与各口径的 sum, 差值最小者即为真口径。
"""
import html
import json
import re
import sys
import zipfile
from pathlib import Path

BOOKS = Path("/vol2/1001/dav/Books/掌阅")
ZP = Path("/vol2/1001/zhangyue-bridge/state/zp.json")

TAG = re.compile(r"<[^>]+>", re.S)
RUBY = re.compile(r"<ruby.*?</ruby>", re.S)
RT = re.compile(r"<rt.*?</rt>", re.S)
BODY = re.compile(r"<body\b[^>]*>(.*?)</body>", re.S | re.I)
WS = re.compile(r"\s+")
CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
ALNUM = re.compile(r"[0-9A-Za-z\u3400-\u9fff]")
COMMENTS = re.compile(r"<!--.*?-->", re.S)


def _strip_all(s):
    return html.unescape(TAG.sub("", COMMENTS.sub("", s)))


METRICS = {
    "raw":       lambda s: len(s),
    "raw_bytes": lambda s: len(s.encode("utf-8")),
    "text":      lambda s: len(_strip_all(s)),
    "nows":      lambda s: len(WS.sub("", _strip_all(s))),
    "body":      lambda s: len(_strip_all(BODY.search(s).group(1))) if BODY.search(s) else 0,
    "body_nows": lambda s: len(WS.sub("", _strip_all(BODY.search(s).group(1)))) if BODY.search(s) else 0,
    "body_noruby": lambda s: len(WS.sub("", _strip_all(RT.sub("", BODY.search(s).group(1))))) if BODY.search(s) else 0,
    "body_cjk":  lambda s: len(CJK.findall(_strip_all(BODY.search(s).group(1)))) if BODY.search(s) else 0,
    "body_alnum": lambda s: len(ALNUM.findall(_strip_all(BODY.search(s).group(1)))) if BODY.search(s) else 0,
}


def opf(z):
    for n in z.namelist():
        if n.lower().endswith(".opf"):
            return n
    raise SystemExit("no opf")


def spine_hrefs(z):
    o = opf(z)
    t = z.read(o).decode("utf-8", "replace")
    base = o.rsplit("/", 1)[0] + "/" if "/" in o else ""
    items = {}
    for m in re.finditer(r"<item\b[^>]*>", t):
        i = re.search(r'\bid="([^"]+)"', m.group(0))
        h = re.search(r'\bhref="([^"]+)"', m.group(0))
        if i and h:
            items[i.group(1)] = base + h.group(1)
    m = re.search(r"<spine\b[^>]*>(.*?)</spine>", t, re.S)
    return [items[r] for r in re.findall(r'idref="([^"]+)"', m.group(1)) if r in items]


def docs(path):
    z = zipfile.ZipFile(path)
    out = []
    for h in spine_hrefs(z):
        try:
            out.append(z.read(h).decode("utf-8", "replace"))
        except KeyError:
            pass
    z.close()
    return out


BOOK_MAP = {
    "6818BFD9C92C8576A0E21AD13CD28CC28B4CAD7E27E236AA2B64CDE9D137FBCE": "狐的故事.epub",
    "DD2F1C22349AF31D06991F85F62D07057D42CE2F7A1EF527B2727BFF086119DF": "路邊的藤井_-_卷01.epub",
    "7B4CC4A3CAE761D3057D855C22CA697069A75D71F141039946161773E8728AE8": "路邊的藤井_-_卷02.epub",
    "5D250B5E8EA7F238116FF483B52033F7C10A1125309CAA788D98CA2AE7148C3A": "路邊的藤井_-_卷03.epub",
    "70776A64C32C1C065DAB241F3E9F133A55C7D8B38C5C6FBC79271E87E00E9526": "剑来（1-54册）完结精校版.epub",
}


def main():
    zp = json.loads(ZP.read_text())
    cases = []
    for rec in zp["body"]:
        bid = rec["bookId"]
        rp = rec["readProcess"]
        if bid not in BOOK_MAP or rp["readingSource"] != "app":
            continue
        cases.append((bid[:8], BOOK_MAP[bid], int(rp["chapterId"]), float(rp["readPercent"]),
                      rp["readPosition"]))
    print(f"{'case':8} {'ch':>4} {'pct':>12}  metric       sum        cum       implied_T    ratio")
    agg = {k: [] for k in METRICS}
    for tag, name, ch, pct, pos in cases:
        d = docs(BOOKS / name)
        if ch + 1 > len(d):
            continue
        print(f"-- {tag} {name} spine={len(d)} ch={ch} pct={pct}")
        for mk, fn in METRICS.items():
            lens = [fn(x) for x in d]
            tot = sum(lens)
            cum = sum(lens[:ch + 1])
            imp = cum / pct if pct else 0.0
            ratio = imp / tot if tot else 0
            agg[mk].append(ratio)
            err = abs(ratio - 1.0)
            print(f"   {tag} {ch:>4} {pct:>12.8f}  {mk:12} {tot:9d} {cum:9d} {imp:12.0f} {ratio:8.5f} {'  <== ' if err < 0.02 else ''}")
        print()
    print("=" * 78)
    print("汇总 (|ratio-1| 均值, 越小越像真口径):")
    for mk in sorted(METRICS, key=lambda k: sum(abs(x - 1) for x in agg[k]) / max(1, len(agg[k]))):
        if agg[mk]:
            e = sum(abs(x - 1) for x in agg[mk]) / len(agg[mk])
            print(f"  {mk:12} mean|ratio-1| = {e:.5f}   ratios={[round(x,4) for x in agg[mk]]}")


if __name__ == "__main__":
    main()