#!/usr/bin/env python3
"""B 方案验证 (第四轮): 消歧 + 语义确认

问题: 同一个 xpath 在相邻 spine 文档里都能命中 (比如 //body/div[1]/p[124]
      在两篇都有 124 个 p 的文档里都存在)。所以"xpath 命中"不能唯一
      定位当前页。

要找的独立判据: **相对位置一致性**。
    若当前页 = spine[PagesRead], 则 xpath 在该文档里的相对偏移应该
    "看起来像读过一点" (不是 0, 也不是 100%)。
    若误判到相邻文档, 相对偏移会跑偏。

另一个判据: **p 序号上限**。
    //body/p[164] 只在有 >=164 个 p 的文档里存在。
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

EPUB_OVERRIDE = {
    "Adrian Mole and the Weapons of Mass Destruction":
        "阿默/07 阿德里安·默尔与大规模杀伤性武器.epub",
}


def kavita_snapshot() -> sqlite3.Connection:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    s = sqlite3.connect(f"file:{KAVITA_DB}?mode=ro", uri=True)
    d = sqlite3.connect(tmp)
    s.backup(d)
    s.close()
    d.row_factory = sqlite3.Row
    return d


def _norm(s: str) -> str:
    return "".join(c for c in (s or "").lower() if c.isalnum() or c.isascii())


def find_epub(folder_path: str, series_name: str) -> Path | None:
    if series_name in EPUB_OVERRIDE:
        p = BOOKS_ROOT / EPUB_OVERRIDE[series_name]
        return p if p.exists() else None
    rel = (folder_path or "").replace("/books/", "").strip("/")
    d = BOOKS_ROOT / rel if rel else None
    cands = sorted(d.glob("*.epub")) if d and d.is_dir() else sorted(BOOKS_ROOT.rglob("*.epub"))
    key = _norm(series_name)
    for pred in (lambda c: _norm(c.stem) == key,
                 lambda c: key and key in _norm(c.stem),
                 lambda c: _norm(c.stem) and _norm(c.stem) in key):
        hit = [c for c in cands if pred(c)]
        if hit:
            return hit[0]
    return None


def main():
    db = kavita_snapshot()
    rows = list(db.execute("""
        SELECT ap.SeriesId, ap.ChapterId, ap.PagesRead, ap.TotalReads,
               ap.BookScrollId, c.Pages ChPages, s.Name SerName, s.FolderPath
        FROM AppUserProgresses ap
        LEFT JOIN Chapter c ON c.Id = ap.ChapterId
        LEFT JOIN Series s ON s.Id = ap.SeriesId
        WHERE ap.AppUserId = 1 AND ap.BookScrollId IS NOT NULL
        ORDER BY ap.SeriesId
    """))

    for r in rows:
        name = r["SerName"] or "?"
        xp = r["BookScrollId"]
        epub = find_epub(r["FolderPath"] or "", name)
        if not epub:
            print(f"\n### {name}: 无 epub")
            continue
        g = zpct.load_geometry(epub)
        idx = r["PagesRead"] or 0
        print(f"\n### {name[:40]}  PagesRead={idx} / Chapter.Pages={r['ChPages']} "
              f"/ spine={len(g.spine)}   xpath={xp}")

        # 找所有命中该 xpath 的 spine 文档
        hits = []
        for probe in range(len(g.spine)):
            di = zpct.build_doc_index(epub, probe)
            if di is None:
                continue
            if xp in di._by_xpath:
                off = di.offset_of_xpath(xp)
                s, e = di._by_xpath[xp]
                hits.append((probe, off, len(di.raw), off / max(1, len(di.raw))))

        print(f"    命中该 xpath 的 spine 文档: "
              + ", ".join(f"spine[{p}](off={o}, {rel*100:.1f}%)" for p, o, _, rel in hits))

        # PagesRead 指向的那篇文档里的 xpath 相对位置
        if 0 <= idx < len(g.spine):
            di = zpct.build_doc_index(epub, idx)
            if di and xp in di._by_xpath:
                off = di.offset_of_xpath(xp)
                print(f"    => spine[{idx}] 内相对位置 {off/len(di.raw)*100:.1f}% "
                      f"(off={off} / {len(di.raw)})  <-- PagesRead 指向这里")
            else:
                print(f"    => spine[{idx}] 内没有这个 xpath !!")

        # 提取该 xpath 的末段序号 (p[N] / h3[N]) 作为"文档规模"判据
        m = re.search(r"([a-zA-Z0-9]+)\[(\d+)\]$", xp)
        if m:
            tag, num = m.group(1), int(m.group(2))
            print(f"    该 xpath 要求文档至少有 {num} 个 <{tag}>")
            for probe, off, size, rel in hits:
                di = zpct.build_doc_index(epub, probe)
                cnt = 0
                for x, _s, _e in di.index:
                    last = x.rsplit("/", 1)[-1]
                    if last.split("[", 1)[0] == tag:
                        cnt += 1
                mark = "  <-- PagesRead" if probe == idx else ""
                print(f"      spine[{probe}] 实有 <{tag}> x {cnt} 个{mark}")
    db.close()


if __name__ == "__main__":
    main()
