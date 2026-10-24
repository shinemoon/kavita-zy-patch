#!/usr/bin/env python3
"""B 方案可行性裁决: 只凭 BookScrollId xpath 能否唯一定位掌阅 spine 文档。

判据三个:
  D1 歧义度   — 有几篇 spine 文档都能命中同一条 xpath
  D2 p 序号上限 — xpath 末段 p[N]/h3[N] 要求的节点数, 哪些文档满足
  D3 相对位置   — 在命中集合里, 各文档的 xpath 相对偏移分布

裁决: 若 D1 > 1, xpath 不能唯一定位 -> B 单靠 xpath 不可行。
"""
from __future__ import annotations

import re
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/vol2/1001/zhangyue-bridge/sync")
from sync import zpct  # noqa: E402

KAVITA_DB = "/vol1/1001/APPS/kavita/kavita.db"
BOOKS_ROOT = Path("/fs/1001/ftp/dav/Books")
OVERRIDE = {
    "Adrian Mole and the Weapons of Mass Destruction":
        "阿默/07 阿德里安·默尔与大规模杀伤性武器.epub",
    "阿德里安·默尔的秘密日记，13¾岁":
        "阿默/01 阿德里安·默尔的秘密日记，13又3之4岁.epub",
}


def snap() -> sqlite3.Connection:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    s = sqlite3.connect(f"file:{KAVITA_DB}?mode=ro", uri=True)
    d = sqlite3.connect(tmp)
    s.backup(d)
    s.close()
    d.row_factory = sqlite3.Row
    return d


def norm(s):
    return "".join(c for c in (s or "").lower() if c.isalnum())


def find_epub(folder, series):
    if series in OVERRIDE:
        p = BOOKS_ROOT / OVERRIDE[series]
        return p if p.exists() else None
    rel = (folder or "").replace("/books/", "").strip("/")
    d = BOOKS_ROOT / rel if rel else None
    cands = sorted(d.glob("*.epub")) if d and d.is_dir() else sorted(BOOKS_ROOT.rglob("*.epub"))
    if not cands:
        return None
    k = norm(series)
    # 优先: 文件名原样包含系列名 (去掉 z-library 之类的尾巴干扰)
    for c in cands:
        if series in c.stem:
            return c
    for pred in (lambda c: norm(c.stem) == k,
                 lambda c: k and k in norm(c.stem),
                 lambda c: norm(c.stem) and norm(c.stem) in k):
        hit = [c for c in cands if pred(c)]
        if hit:
            return hit[0]
    return None


def count_tag(di, tag):
    n = 0
    for x, _s, _e in di.index:
        if x.rsplit("/", 1)[-1].split("[", 1)[0] == tag:
            n += 1
    return n


def main():
    db = snap()
    rows = db.execute("""
        SELECT ap.SeriesId, ap.ChapterId, ap.PagesRead, ap.BookScrollId,
               c.Pages ChPages, s.Name SerName, s.FolderPath
        FROM AppUserProgresses ap
        LEFT JOIN Chapter c ON c.Id = ap.ChapterId
        LEFT JOIN Series  s ON s.Id = ap.SeriesId
        WHERE ap.AppUserId = 1 AND ap.BookScrollId IS NOT NULL
        ORDER BY ap.SeriesId
    """)
    n_ok = n_bad = 0
    for r in rows:
        name = r["SerName"] or "?"
        xp = r["BookScrollId"]
        ep = find_epub(r["FolderPath"] or "", name)
        if not ep:
            continue
        g = zpct.load_geometry(ep)
        hits = []
        for probe in range(len(g.spine)):
            di = zpct.build_doc_index(ep, probe)
            if di is None or xp not in di._by_xpath:
                continue
            off = di.offset_of_xpath(xp)
            hits.append((probe, off, len(di.raw), off / max(1, len(di.raw))))

        idx = r["PagesRead"] or 0
        m = re.search(r"([a-zA-Z0-9]+)\[(\d+)\]$", xp)
        verdict = "UNIQUE" if len(hits) == 1 else "AMBIG"
        ok = (len(hits) == 1) and hits[0][0] == idx
        if ok:
            n_ok += 1
        else:
            n_bad += 1
        print(f"\n[{verdict}] {name[:34]}")
        print(f"  xpath={xp}")
        print(f"  PagesRead={idx}  Chapter.Pages={r['ChPages']}  spine={len(g.spine)}")
        print(f"  命中 {len(hits)} 篇: " + ", ".join(
            f"spine[{p}]({rel*100:.0f}%)" + ("*" if p == idx else "") for p, _o, _l, rel in hits))
        if m:
            tag, need = m.group(1), int(m.group(2))
            counts = []
            for p, _o, _l, _rel in hits:
                di = zpct.build_doc_index(ep, p)
                counts.append(f"spine[{p}]:{count_tag(di, tag)}")
            print(f"  <{tag}> 上限判据 (需 >={need}): " + " ".join(counts))
        sel = [h for h in hits if h[0] == idx]
        if sel:
            print(f"  => PagesRead 命中篇相对位置 {sel[0][3]*100:.1f}% "
                  f"(off={sel[0][1]}/{sel[0][2]})")
        else:
            print("  => PagesRead 指向的 spine 里根本没有这条 xpath")
    print(f"\n=== 裁决: 单条 xpath 唯一定位且等于 PagesRead 的 {n_ok} 本, "
          f"歧义或错配 {n_bad} 本 ===")
    db.close()


if __name__ == "__main__":
    main()