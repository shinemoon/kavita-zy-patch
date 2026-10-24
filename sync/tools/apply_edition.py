#!/usr/bin/env python3
"""把某本掌阅书重排成 "Kavita 页=等字节片" 的版本, 换进 Kavita 的库里。

用法:
    python3 tools/apply_edition.py --series 26 --dry-run     # 只构建+自检
    python3 tools/apply_edition.py --series 26               # 换库 + 重扫
    python3 tools/apply_edition.py --all --dry-run

为什么要换文件: Kavita 的 EPUB 阅读器 1 个 spine 文档 = 1 页, 书架百分比
= PagesRead/页数。原书前 5 篇是封面/制作信息/简介/目录 (只占 2.7% 字节,
却占 55% 的页), 所以"掌阅 48.96% → Kavita 4/9=44% 且打开是目录页"。
把整本按字节均匀切成 N 页后, PagesRead/N == 掌阅 readPercent。

原书不会丢: 第一次跑时拷到 bridge/books/<书名>.epub, 之后几何都从
state/kpages/<series>.json 的 sidecar 取。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sync import kavita_db, kpages, mapping  # noqa: E402
from sync.config import ZY_EPUB_DIR  # noqa: E402
from tools import kapi  # noqa: E402

BRIDGE = Path("/vol2/1001/zhangyue-bridge")
ORIG_DIR = BRIDGE / "books"
BACKUP_DIR = BRIDGE / "state" / "backup"


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _library_dir(series_id: int) -> Path:
    """Kavita 看到的那个目录 (ext4 侧路径)。"""
    return ZY_EPUB_DIR


def _pick_source(link: mapping.BookLink) -> tuple[Path, Path]:
    """返回 (原书路径, 目标库路径)。"""
    lib = _library_dir(link.kavita_series_id) / f"{link.zy_book_name}.epub"
    orig = ORIG_DIR / f"{link.zy_book_name}.epub"
    if orig.exists():
        return orig, lib
    if lib.exists() and not kpages.is_edition(lib):
        return lib, lib
    if link.epub_path and Path(link.epub_path).exists() \
            and not kpages.is_edition(link.epub_path):
        return Path(link.epub_path), lib
    raise SystemExit(f"找不到原书: {link.zy_book_name} (lib={lib})")


def _rescan(series_id: int, library_id: int = 1) -> None:
    st, body = kapi.call("/api/series/scan", "POST",
                         {"libraryId": library_id, "seriesId": series_id,
                          "forceUpdate": True})
    _log(f"rescan series={series_id} -> HTTP {st} {body[:120] if body else ''}")


def _wait_pages(series_id: int, want: int, timeout: float = 180.0) -> tuple[bool, list[dict]]:
    t0 = time.time()
    while time.time() - t0 < timeout:
        chs = kavita_db.list_chapters(series_id)
        pages = sum((c["ChPages"] or 0) for c in chs)
        if pages == want:
            return True, chs
        time.sleep(3)
    return False, kavita_db.list_chapters(series_id)


def apply_one(series_id: int, *, dry_run: bool = False, rescan: bool = True,
              target_bytes: int = kpages.TARGET_PAGE_BYTES) -> int:
    link = mapping.get(series_id)
    if not link:
        _log(f"series={series_id} 没有映射, 跳过")
        return 2
    src, lib = _pick_source(link)
    _log(f"series={series_id} 《{link.zy_book_name}》 原书={src} 库={lib}")

    tmp = lib.with_suffix(".kptmp.epub")
    sc = kpages.build_edition(src, tmp, target_bytes=target_bytes)
    sc["series_id"] = series_id
    sc["library_path"] = str(lib)
    sc["book_name"] = link.zy_book_name
    _log(f"构建完成: {sc['total_pages']} 页, 源 {sc['src_total_bytes']} 字节 "
         f"(每页 ~{sc['src_total_bytes'] // sc['total_pages']}B)")

    # 自检: 页数 / 大小
    n = sc["total_pages"]
    bad = [p for p in sc["pages"] if p["e"] <= p["s"]]
    if bad:
        _log(f"!! 有 {len(bad)} 个空页, 放弃")
        return 3
    _log(f"自检通过 ({n} 页, 0 空页)")

    if dry_run:
        sc_path = kpages.save(sc, series_id)
        _log(f"--dry-run 不换库; sidecar 写 {sc_path}")
        tmp.unlink(missing_ok=True)
        return 0

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if lib.exists() and not kpages.is_edition(lib):
        ORIG_DIR.mkdir(parents=True, exist_ok=True)
        if not (ORIG_DIR / src.name).exists():
            shutil.copy2(src, ORIG_DIR / src.name)
            _log(f"原书归档 -> {ORIG_DIR / src.name}")
        bak = BACKUP_DIR / f"{lib.stem}.epub.bak-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(lib, bak)
        _log(f"库内旧文件备份 -> {bak}")

    os.replace(tmp, lib)
    _log(f"已换库: {lib} ({lib.stat().st_size} 字节)")
    sc_path = kpages.save(sc, series_id)
    _log(f"sidecar -> {sc_path}")

    if rescan:
        _rescan(series_id)
        ok, chs = _wait_pages(series_id, n)
        pages = sum((c["ChPages"] or 0) for c in chs)
        _log(f"重扫结果: {'OK' if ok else '超时'} chapter_pages={pages} (期望 {n})")
        if not ok:
            return 4
        for c in chs:
            _log(f"   chapter id={c['ChId']} pages={c['ChPages']} title={c['ChTitle']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", type=int, action="append")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-rescan", action="store_true")
    ap.add_argument("--target-bytes", type=int, default=kpages.TARGET_PAGE_BYTES)
    a = ap.parse_args()

    ids = a.series or []
    if a.all:
        ids = [l.kavita_series_id for l in mapping.all_links()]
    if not ids:
        ap.error("要 --series ID 或 --all")
    rc = 0
    for sid in ids:
        try:
            rc |= apply_one(sid, dry_run=a.dry_run, rescan=not a.no_rescan,
                            target_bytes=a.target_bytes)
        except SystemExit as e:
            _log(str(e))
            rc |= 2
    return rc


if __name__ == "__main__":
    sys.exit(main())
