"""下载即入库: 原书归档 + Kavita 切片版 (kpages) 一次做完。

为什么不"先入库、事后修补"

    Kavita 的 epub 页粒度 = spine 文档, 而"书架百分比 = PagesRead /
    Chapter.Pages"跟"续读位置"是**同一个数**。原书 spine 常常只有 5-20 篇,
    封面/版权/目录占前几篇 → 掌阅 48.96% 落成 Kavita 4/9=44%, 而且点"继续
    阅读"打开的是目录页。只有把整本按字节等分切开, PagesRead/N 才等于掌阅
    readPercent。

    所以对"要跟掌阅对进度"的书 (掌阅云书必然要), 重排不是事后修补, 而是
    **入库前的准备**。下载完成时直接产出两件东西:

        原书     -> bridge/books/<name>.epub   字节几何的唯一权威 (算 readPercent)
        切片版   -> Kavita 库目录同名文件       Kavita 读的那份, sidecar 记录页映射

    好处: 库里的书从第一次被扫到就是细分页, 不会出现"原书进库 → 进度对不上
    → 事后想起来再重排"的中间态。

两条硬约束

    1. **原书必须是未改动过的下载字节**。补 nav 之类的修补只能作用在切片版上,
       不能作用在原书上 —— nav 塞 100KB 进 66MB 就是 0.15% 的几何漂移, 而
       readPercent 是字节比例, 漂移直接变成进度错位。
    2. 只处理"一系列一个文件"的书。一个系列多本 (路邊的藤井 三卷) 时 Kavita
       的 PagesRead 是**卷内**页号, 掌阅是整本 readPercent, 中间还差一层卷
       映射; 这种情况本轮跳过, 不硬做。
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path

from . import kavita_db, kpages, mapping
from .config import ZY_EPUB_DIR, ZY_ORIG_DIR

BRIDGE = Path("/vol2/1001/zhangyue-bridge")
LIB = Path(ZY_EPUB_DIR)
ORIG = Path(ZY_ORIG_DIR)
BACKUP = BRIDGE / "state" / "backup"

LIBRARY_ID = 1                     # Kavita 的 Book 库 (docker 里挂 /books)
SCAN_WAIT = float(os.environ.get("ZY_PREP_SCAN_WAIT") or 240)


def _share(path: Path) -> None:
    """root 侧进程写出来的文件, 让 CLI(claud) 也读写得了。

    属主跟父目录对齐 (不硬编码用户名), 权限放宽到 644 —— 换库靠 rename, 只要有
    目录写权限就行, 所以 644 足够, 不需要给文件本身写权限。
    """
    try:
        st, pst = path.stat(), path.parent.stat()
        if os.geteuid() == 0 and (st.st_uid != pst.st_uid or st.st_gid != pst.st_gid):
            os.chown(path, pst.st_uid, pst.st_gid)
        os.chmod(path, 0o644)
    except OSError:
        pass


def _say(msg: str, log=print) -> None:
    try:
        log(msg)
    except Exception:                                   # noqa: BLE001
        pass


def in_library(path: str | Path) -> bool:
    """目标是不是落在 Kavita 的库目录里 (只有库里的书才需要重排)。"""
    try:
        p = Path(path).resolve()
        base = LIB.resolve()
    except OSError:
        return False
    return p == base or base in p.parents


def _scan_library() -> None:
    """让 Kavita 扫一遍全书库 (新书入库 / 换文件后用)。"""
    root = Path(__file__).resolve().parents[1]          # 仓库根
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from tools import kapi                              # noqa: PLC0415
    st, body = kapi.call(f"/api/library/scan?libraryId={LIBRARY_ID}", "POST", {})
    _say(f"扫库 -> HTTP {st} {str(body)[:80]}")


def resolve_series(target: Path, *, scan: bool = True, wait: float | None = None,
                   log=print) -> int | None:
    """文件名 → Kavita 系列 id。库里还没有就先扫一次库再等。"""
    sid = kavita_db.series_for_file(target.name)
    if sid:
        return sid
    if not scan:
        return None
    _say("Kavita 里还没有这本书, 先扫库…", log)
    _scan_library()
    deadline = time.time() + (wait if wait is not None else SCAN_WAIT)
    while time.time() < deadline:
        sid = kavita_db.series_for_file(target.name)
        if sid:
            return sid
        time.sleep(3)
    return None


def _chapter_pages(series_id: int) -> int:
    return sum((c["ChPages"] or 0) for c in kavita_db.list_chapters(series_id))


def _wait_pages(series_id: int, want: int, wait: float = 120.0) -> bool:
    deadline = time.time() + wait
    while time.time() < deadline:
        if _chapter_pages(series_id) == want:
            return True
        time.sleep(3)
    return False


def _ensure_mapping(series_id: int, *, zy_book_id: str, book_name: str,
                    orig: Path, log=print) -> str:
    """给新入库的书写一条 bridge 映射 (没有就建, 有就只补空字段)。"""
    cur = mapping.get(series_id)
    if cur is None:
        if not zy_book_id:
            _say(f"系列 {series_id} 没有映射, 且不知道掌阅 book_id → 跳过建档", log)
            return "no_zy_id"
        mapping.link(series_id, zy_book_id, book_name or orig.stem,
                     kavita_chapter_count=len(kavita_db.list_chapters(series_id)),
                     epub_path=str(orig),
                     note="cloud download 自动建档")
        _say(f"映射已建立 series={series_id} ← 掌阅《{book_name or orig.stem}》", log)
        return "created"
    if zy_book_id and not cur.zy_book_id:
        mapping.link(series_id, zy_book_id, book_name or cur.zy_book_name,
                     kavita_chapter_count=cur.kavita_chapter_count or 1,
                     epub_path=str(orig), note=cur.note or "补 zy book_id")
        return "filled"
    return "exists"


def _ensure_volume_mapping(series_id: int, target: Path, *, zy_book_id: str,
                           book_name: str, log=print) -> str:
    """多卷系列: 这一本 epub 只对应系列里的一个 chapter/卷 → 建卷级映射。

    没有卷级映射时 push/pull 只能落到 series 级, 三卷共用一个进度格 (进度互串)。
    """
    if not zy_book_id:
        _say(f"系列 {series_id} 多卷, 但不知道掌阅 book_id → 跳过建档", log)
        return "no_zy_id"
    ch = kavita_db.chapter_for_file(series_id, target.name)
    if not ch:
        _say(f"系列 {series_id} 里找不到 {target.name} 对应的卷 → 无法建卷级映射", log)
        return "no_chapter"
    cur = mapping.get(series_id, ch["ChId"])
    mapping.link(series_id, zy_book_id, book_name or target.stem,
                 kavita_chapter_count=1,
                 note=f"cloud download 卷级建档 (chapter={ch['ChId']})",
                 kavita_chapter_id=ch["ChId"])
    _say(f"卷级映射已建立 series={series_id} chapter={ch['ChId']} "
         f"← 掌阅《{book_name or target.stem}》", log)
    return "updated" if cur is not None else "created"


def _pull_now(series_id: int, res: dict, log=print) -> None:
    """把掌阅进度落到刚建好档的那本/那卷 (失败不影响入库)。"""
    try:
        from . import sync as syncmod                    # noqa: PLC0415 — 避免环形 import
        out = syncmod.pull_once(series_ids={series_id}, only_forward=True)
        res["pull"] = out
        _say(f"pull: {out}", log)
    except Exception as e:                              # noqa: BLE001
        res["pull"] = {"error": f"{type(e).__name__}: {e}"}
        _say(f"pull 失败 (不影响入库): {res['pull']['error']}", log)


def prep_download(target: str | Path, *, book_name: str = "", zy_book_id: str = "",
                  series_id: int | None = None, rescan: bool = True,
                  pull: bool = True, target_bytes: int | None = None,
                  log=print) -> dict:
    """下载完的 epub → 原书归档 + 切片版换进库 + 建档 + pull。

    返回 dict, state ∈ {ok, edition, skipped, no_series, error}。
    任何一步失败都**不**动库里的文件 (先建 tmp, 校验通过才 os.replace)。
    """
    res: dict = {"state": "skipped", "msgs": []}

    def say(m: str) -> None:
        res["msgs"].append(m)
        _say(m, log)

    tgt = Path(target)
    tb = target_bytes if target_bytes is not None else kpages.TARGET_PAGE_BYTES

    if not in_library(tgt):
        res["why"] = f"不在 Kavita 库目录 {LIB}"
        return res
    if kpages.is_edition(tgt):
        res.update(state="edition", why="文件已经是切片版")
        return res

    # ---- 1. 原书归档 (字节权威) ----
    try:
        ORIG.mkdir(parents=True, exist_ok=True)
        orig = ORIG / tgt.name
        if orig.exists() and orig.stat().st_size == tgt.stat().st_size:
            say(f"原书已在 {orig} (字节数一致, 不覆盖)")
        else:
            shutil.copy2(tgt, orig)
            _share(orig)
            say(f"原书归档 -> {orig} ({tgt.stat().st_size} 字节)")
        res["orig"] = str(orig)
    except OSError as e:
        res.update(state="error", error=f"归档原书失败: {type(e).__name__}: {e}")
        return res

    # ---- 2. 已有多卷系列 → 不自动重排 ----
    sid = series_id or kavita_db.series_for_file(tgt.name)
    files = kavita_db.series_files(sid) if sid else []
    if sid and len(files) > 1:
        res.update(state="skipped", series_id=sid, multi_volume=len(files))
        mstate = _ensure_volume_mapping(sid, tgt, zy_book_id=zy_book_id,
                                       book_name=book_name, log=say)
        res["mapping"] = mstate
        res["why"] = (f"系列有 {len(files)} 个文件 (多卷), 卷级映射 {mstate}, "
                      f"不做字节切片 (掌阅按整本 pct, Kavita 按卷内页)")
        say(res["why"])
        if pull and mstate in ("created", "updated"):
            _pull_now(sid, res, log)
        return res

    # ---- 2b. 图片型 epub (一图一页) 不用重排 ----
    try:
        reason = kpages.image_book_reason(kpages.load_epub(orig))
    except Exception:                                   # noqa: BLE001 — 解析失败留给 build 报错
        reason = None
    if reason:
        # 不重排不是不建档: 没档就永远推不动 (2026-10-24 藤井 卷01 的坑)
        if not sid:
            sid = resolve_series(tgt, log=say)
        res.update(state="skipped", why=f"图片型 epub, 保持原样: {reason}",
                   series_id=sid)
        if sid:
            mstate = (_ensure_volume_mapping(sid, tgt, zy_book_id=zy_book_id,
                                             book_name=book_name, log=say)
                      if len(kavita_db.series_files(sid)) > 1
                      else _ensure_mapping(sid, zy_book_id=zy_book_id,
                                           book_name=book_name, orig=orig, log=say))
            res["mapping"] = mstate
            if pull and mstate in ("created", "updated", "filled"):
                _pull_now(sid, res, log)
        say(res["why"])
        return res

    # ---- 3. 建切片版 (失败不动库) ----
    tmp = tgt.with_name(tgt.name + ".kptmp.epub")
    try:
        sc = kpages.build_edition(orig, tmp, target_bytes=tb)
    except Exception as e:                              # noqa: BLE001
        tmp.unlink(missing_ok=True)
        res.update(state="error", error=f"{type(e).__name__}: {e}")
        say(f"构建切片版失败, 库里文件保持原样: {res['error']}")
        return res
    res.update(pages=sc["total_pages"],
               bytes_per_page=sc["src_total_bytes"] // max(1, sc["total_pages"]),
               opf_downgraded=bool(sc.get("opf_version_downgraded")))

    # ---- 4. 换库 ----
    try:
        BACKUP.mkdir(parents=True, exist_ok=True)
        bak = BACKUP / f"{tgt.stem}.epub.bak-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(tgt, bak)
        os.replace(tmp, tgt)
        _share(tgt)
        say(f"切片版 {sc['total_pages']} 页 (每页 ~{res['bytes_per_page']}B) -> {tgt}")
        res["backup"] = str(bak)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        res.update(state="error", error=f"换库失败: {type(e).__name__}: {e}")
        return res

    # ---- 5. 找系列 (新书这里才扫库) ----
    if not sid:
        sid = resolve_series(tgt, log=log)
    if not sid:
        res.update(state="no_series", why="扫库后仍没找到这本书的系列", rescan_pending=True)
        say(res["why"])
        return res
    res["series_id"] = sid

    sc["series_id"] = sid
    sc["library_path"] = str(tgt)
    sc["book_name"] = book_name or tgt.stem
    sc_path = kpages.save(sc, sid)
    say(f"sidecar -> {sc_path}")

    # ---- 6. 重扫 + 等页数 ----
    if rescan:
        _scan_library()
        ok = _wait_pages(sid, sc["total_pages"])
        have = _chapter_pages(sid)
        res.update(rescanned=bool(ok), kavita_pages=have)
        say(f"重扫结果: Kavita {have} 页 (期望 {sc['total_pages']})"
            + ("" if ok else " — 还没对上, pull 会退回旧口径"))
        if not ok:
            res["state"] = "ok"
            res["warn"] = "Kavita 页数还没跟 sidecar 对上"
            return res

    # ---- 7. 映射 + pull (把掌阅进度立刻落到新页号) ----
    mstate = _ensure_mapping(sid, zy_book_id=zy_book_id,
                             book_name=book_name, orig=orig, log=log)
    res["mapping"] = mstate
    if pull:
        _pull_now(sid, res, log)

    res["state"] = "ok"
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description="把库里的 epub 原书做成 Kavita 切片版")
    ap.add_argument("--file", action="append", required=True,
                    help="库目录里的 epub 路径 (或文件名, 自动补库目录前缀)")
    ap.add_argument("--no-rescan", action="store_true")
    ap.add_argument("--no-pull", action="store_true")
    ap.add_argument("--target-bytes", type=int, default=None)
    a = ap.parse_args()

    rc = 0
    for f in a.file:
        p = Path(f)
        if not p.is_absolute():
            p = LIB / f
        print(f"\n=== {p}")
        r = prep_download(p, rescan=not a.no_rescan, pull=not a.no_pull,
                          target_bytes=a.target_bytes)
        for m in r.pop("msgs", []):
            print("  ·", m)
        print("  =>", r)
        if r.get("state") not in ("ok", "edition"):
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
