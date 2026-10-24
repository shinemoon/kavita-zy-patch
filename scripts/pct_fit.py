#!/usr/bin/env python3
"""从 epub spine 逐章统计字符数, 与掌阅 readPercent 对账。

用途: 反推掌阅 readPercent 的算法 (纯字节数 / 去空白 / 去标签 / 加权累计)。
数据源:
  - epub: /vol2/1001/dav/Books/掌阅/*.epub
  - 掌阅: /vol2/1001/zhangyue-bridge/state/zp.json (getProcess 返回)
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
WS = re.compile(r"\s+")


def opf_path(z):
    for n in z.namelist():
        if n.lower().endswith(".opf"):
            return n
    raise SystemExit("no opf")


def manifest(z, opf):
    t = z.read(opf).decode("utf-8", "replace")
    base = opf.rsplit("/", 1)[0] + "/" if "/" in opf else ""
    out = {}
    for m in re.finditer(r"<item\b[^>]*>", t):
        tag = m.group(0)
        i = re.search(r'\bid="([^"]+)"', tag)
        h = re.search(r'\bhref="([^"]+)"', tag)
        if i and h:
            out[i.group(1)] = base + h.group(1)
    return out, t


def spine_ids(z, opf):
    _, t = manifest(z, opf)
    m = re.search(r"<spine\b[^>]*>(.*?)</spine>", t, re.S)
    if not m:
        return []
    return re.findall(r'idref="([^"]+)"', m.group(1))


def clean(raw: str) -> str:
    return html.unescape(TAG.sub("", raw))


def chapter_lengths(path: Path):
    """返回 [(id, href, raw_len, text_len, nows_len)] 按 spine 顺序。"""
    z = zipfile.ZipFile(path)
    items, _ = manifest(z, opf_path(z))
    out = []
    for sid in spine_ids(z, opf_path(z)):
        href = items.get(sid)
        if not href:
            continue
        try:
            raw = z.read(href).decode("utf-8", "replace")
        except KeyError:
            continue
        txt = clean(raw)
        out.append((sid, href, len(raw), len(txt), len(WS.sub("", txt))))
    z.close()
    return out


def pct_by_sum(chaps, chapter_id, char_idx, cum_before_extra=0):
    """(chapter_id 前所有章 + char_idx) / 全书总字符。char_idx 用 4=nows / 3=text。"""
    return None


def analyze(path: Path, chapter_id: int, char_idx: int, label: str):
    chaps = chapter_lengths(path)
    lens = [c[char_idx] for c in chaps]
    total = sum(lens)
    cum = sum(lens[:chapter_id])
    return {
        "label": label, "chapters": len(lens), "total": total,
        "cum": cum, "pct": cum / total if total else 0,
        "lens": lens,
    }


if __name__ == "__main__":
    zp = json.loads(ZP.read_text())
    for rec in zp["body"]:
        rp = rec["readProcess"]
        print("=" * 70)
        print(rec["bookId"][:12], "ch=", rp["chapterId"], "total=", rp["totalCount"],
              "pct=", rp["readPercent"], "pos=", rp["readPosition"],
              "src=", rp["readingSource"])
    print()
    for p in sorted(BOOKS.glob("*.epub")):
        chaps = chapter_lengths(p)
        print(p.name, "spine docs:", len(chaps),
              "chars(nows):", sum(c[4] for c in chaps),
              "chars(text):", sum(c[3] for c in chaps))