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
    # 档在但字段缺 (老的自动建档只写了名字) → 补 epub_path / 章数, 幂等
    if not cur.epub_path or not cur.kavita_chapter_count:
        mapping.link(series_id, cur.zy_book_id, cur.zy_book_name,
                     kavita_chapter_count=cur.kavita_chapter_count
                     or len(kavita_db.list_chapters(series_id)),
                     epub_path=cur.epub_path or str(orig), note=cur.note or "")
        _say(f"映射补字段 series={series_id} epub_path={cur.epub_path or orig}", log)
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


# ---- 一站式收尾 (幂等, 可从磁盘现状补齐) --------------------------------

def _step(res: dict, name: str, ok: bool, detail: str = "") -> bool:
    res.setdefault("steps", []).append(
        {"name": name, "ok": bool(ok), "detail": str(detail)[:500]})
    return bool(ok)


def _pages_read(series_id: int) -> int:
    try:
        return int(kavita_db.pages_read_by_series().get(int(series_id), 0))
    except Exception:                                   # noqa: BLE001
        return 0


def ensure_ready(target: str | Path, *, book_name: str = "", zy_book_id: str = "",
                 series_id: int | None = None, target_bytes: int | None = None,
                 rescan: bool = True, pull: str | bool = "auto",
                 log=print) -> dict:
    """下载完成后的一站式收尾 —— **幂等, 可反复重跑**。

    一次跑完下面全部步骤, 每一步都记进 `steps` (UI 直接展示哪步成了/哪步缺):

        1. file     库里的文件在、能读
        2. archive  原书归档 bridge/books (readPercent 的唯一权威; 已是切片版时只校验)
        3. edition  库里那份是等字节切片版 (缺/坏就从原书重建, 原书不在才放弃)
        4. sidecar  state/kpages/<series>.json (页 ↔ 原书字节)
        5. series   找到 Kavita 系列 (没有就扫库)
        6. pages    重扫并等 Chapter.Pages == sidecar 页数
        7. mapping  book_link (多卷系列建卷级 / 单本建系列级)
        8. pull     掌阅进度落到新页号

    `pull="auto"` 只在**这本书在 Kavita 还没有任何进度**时自动 pull —— 新下载
    的书属于这种情况; 已有进度的书不动 (避免把读者读了一半的位置按云端旧值
    往回拨), 需要时手动点「从掌阅拉」。

    与 prep_download 的区别: prep_download 是"从原始文件出发的一次性准备",
    半路任何一步失败 (扫库 404 / API key 路径 / 页数没对上) 都会留下
    "库里是切片版、但没有 sidecar / 没有映射" 的中间态, 而它下次看到
    is_edition 就直接 return "edition"。ensure_ready 从**磁盘现状**出发补
    缺的那几步, 所以下载、覆盖下载、事后修复都走同一个入口。
    """
    res: dict = {"state": "skipped", "steps": [], "msgs": []}

    def say(m: str) -> None:
        res["msgs"].append(str(m))
        _say(m, log)

    tgt = Path(target)
    tb = target_bytes if target_bytes is not None else kpages.TARGET_PAGE_BYTES

    if not tgt.exists():
        res.update(state="error", error=f"文件不存在: {tgt}")
        _step(res, "file", False, res["error"])
        return res
    if not in_library(tgt):
        res["why"] = f"不在 Kavita 库目录 {LIB}"
        _step(res, "file", False, res["why"])
        return res
    _share(tgt)

    try:
        is_ed = kpages.is_edition(tgt)
    except Exception as e:                              # noqa: BLE001
        is_ed = False
        say(f"is_edition 判定失败 ({type(e).__name__}: {e}), 按原书处理")

    # ---- 2 原书归档 ----
    ORIG.mkdir(parents=True, exist_ok=True)
    orig = ORIG / tgt.name
    if is_ed:
        if orig.exists():
            res["orig"] = str(orig)
            _step(res, "archive", True, f"原书已在 {orig}")
        else:
            _step(res, "archive", False,
                  f"原书归档缺失 ({orig}) — 现在这份是切片版, 无法重建几何")
    else:
        try:
            if orig.exists() and orig.stat().st_size == tgt.stat().st_size:
                _step(res, "archive", True, f"原书已在 {orig} (字节数一致)")
            else:
                # 原书是 readPercent 的唯一权威 —— 覆盖前先把旧归档留一份,
                # 否则"库里的文件被别的工具改过 + 又下载了一次"就会把真原件弄丢。
                if orig.exists():
                    try:
                        BACKUP.mkdir(parents=True, exist_ok=True)
                        old = BACKUP / (f"{orig.stem}.orig.epub.bak-"
                                        + time.strftime("%Y%m%d-%H%M%S"))
                        shutil.copy2(orig, old)
                        say(f"旧原书归档备份 -> {old}")
                    except OSError as e:
                        say(f"旧原书归档备份失败 ({type(e).__name__}: {e}), 继续")
                shutil.copy2(tgt, orig)
                _share(orig)
                _step(res, "archive", True,
                      f"原书归档 -> {orig} ({tgt.stat().st_size}B)")
                say(f"原书归档 -> {orig}")
            res["orig"] = str(orig)
        except OSError as e:
            res.update(state="error", error=f"归档原书失败: {type(e).__name__}: {e}")
            _step(res, "archive", False, res["error"])
            return res

    # ---- 5' 先摸一遍系列 (可能还没有) ----
    sid = series_id or kavita_db.series_for_file(tgt.name)
    if sid:
        res["series_id"] = sid

    # ---- 多卷系列: 不切片, 但必须建卷级映射 ----
    files = kavita_db.series_files(sid) if sid else []
    if sid and len(files) > 1:
        _step(res, "edition", False,
              f"多卷系列 ({len(files)} 个文件) — 不做字节切片")
        mstate = _ensure_volume_mapping(sid, tgt, zy_book_id=zy_book_id,
                                       book_name=book_name, log=say)
        _step(res, "mapping", mstate in ("created", "updated"), mstate)
        _maybe_pull(res, sid, pull, say)
        res["state"] = "skipped"
        res["why"] = (f"系列有 {len(files)} 个文件 (多卷), 卷级映射 {mstate}, "
                      f"不做字节切片 (掌阅按整本 pct, Kavita 按卷内页)")
        return res

    # ---- 图片型 epub: 不切片, 建档即可 ----
    if not is_ed:
        try:
            reason = kpages.image_book_reason(kpages.load_epub(orig))
        except Exception:                               # noqa: BLE001
            reason = None
        if reason:
            _step(res, "edition", False, f"图片型 epub, 保持原样: {reason}")
            if not sid:
                sid = resolve_series(tgt, log=say)
            if not sid:
                res.update(state="no_series", why="扫库后仍没找到这本书的系列")
                _step(res, "series", False, res["why"])
                return res
            res["series_id"] = sid
            _step(res, "series", True, f"series={sid}")
            mstate = _ensure_mapping(sid, zy_book_id=zy_book_id,
                                     book_name=book_name, orig=orig, log=say)
            _step(res, "mapping", mstate != "no_zy_id", mstate)
            _maybe_pull(res, sid, pull, say)
            res["state"] = "skipped"
            res["why"] = f"图片型 epub, 保持原样: {reason}"
            return res

    # ---- 3 sidecar 现状 ----
    sc = kpages.load(sid) if sid else None
    sc_pages = len(sc.get("pages") or []) if sc else 0

    need_build = (not is_ed)
    why_build = "" if need_build else ""
    if is_ed and sc_pages == 0:
        # 切片版但 sidecar 丢了 → 用原书确定性重建一次 (库里的内容不变语义)
        need_build = True
        why_build = "切片版缺 sidecar, 用原书重建以恢复页映射"

    if need_build:
        if not orig.exists():
            res.update(state="error",
                       error="缺少原书 (bridge/books), 无法重建切片版")
            _step(res, "edition", False, res["error"])
            return res
        tmp = tgt.with_name(tgt.name + ".kptmp.epub")
        try:
            sc = kpages.build_edition(orig, tmp, target_bytes=tb)
        except Exception as e:                          # noqa: BLE001
            tmp.unlink(missing_ok=True)
            res.update(state="error", error=f"构建切片版失败: {type(e).__name__}: {e}")
            _step(res, "edition", False, res["error"])
            say(f"构建切片版失败, 库里文件保持原样: {res['error']}")
            return res
        try:
            BACKUP.mkdir(parents=True, exist_ok=True)
            bak = BACKUP / f"{tgt.stem}.epub.bak-{time.strftime('%Y%m%d-%H%M%S')}"
            shutil.copy2(tgt, bak)
            os.replace(tmp, tgt)
            _share(tgt)
            res["backup"] = str(bak)
        except OSError as e:
            tmp.unlink(missing_ok=True)
            res.update(state="error", error=f"换库失败: {type(e).__name__}: {e}")
            _step(res, "edition", False, res["error"])
            return res
        sc_pages = len(sc.get("pages") or [])
        res.update(pages=sc_pages,
                   bytes_per_page=sc["src_total_bytes"] // max(1, sc_pages),
                   opf_downgraded=bool(sc.get("opf_version_downgraded")))
        _step(res, "edition", True,
              (why_build + " " if why_build else "")
              + f"{sc_pages} 页 (每页 ~{res['bytes_per_page']}B)" + f" -> {tgt}")
        say(f"切片版 {sc_pages} 页 (每页 ~{res['bytes_per_page']}B) -> {tgt}")
    else:
        res["pages"] = sc_pages
        _step(res, "edition", True, f"已是切片版 ({sc_pages} 页, sidecar 在)")

    # ---- 5 找系列 (新书这里才扫库) ----
    if not sid:
        sid = resolve_series(tgt, log=say)
    if not sid:
        res.update(state="no_series", why="扫库后仍没找到这本书的系列")
        _step(res, "series", False, res["why"])
        say(res["why"])
        return res
    res["series_id"] = sid
    _step(res, "series", True, f"series={sid} {kavita_db.series_name(sid)}")

    # ---- 4 sidecar (缺/刚建才写) ----
    if sc is not None:
        sc["series_id"] = sid
        sc["library_path"] = str(tgt)
        sc["book_name"] = book_name or tgt.stem
        try:
            sc_path = kpages.save(sc, sid)
            _step(res, "sidecar", True, str(sc_path))
        except Exception as e:                          # noqa: BLE001
            _step(res, "sidecar", False, f"{type(e).__name__}: {e}")
    else:
        _step(res, "sidecar", False, "没有 sidecar 且未重建")

    # ---- 6 重扫 + 等页数 ----
    want = len(sc.get("pages") or []) if sc else 0
    have = _chapter_pages(sid)
    if rescan and want and have != want:
        for _ in range(2):
            _scan_library()
            if _wait_pages(sid, want, wait=min(SCAN_WAIT, 120)):
                break
        have = _chapter_pages(sid)
    pages_ok = (not want) or have == want
    _step(res, "pages", pages_ok, f"Kavita {have} 页 / 期望 {want}")
    res.update(rescanned=True, kavita_pages=have)
    if not pages_ok:
        res["warn"] = f"Kavita 页数 {have} 还没跟 sidecar {want} 对上"

    # ---- 7 映射 ----
    mstate = _ensure_mapping(sid, zy_book_id=zy_book_id,
                             book_name=book_name, orig=orig, log=say)
    _step(res, "mapping", mstate != "no_zy_id", mstate)

    # ---- 8 pull ----
    _maybe_pull(res, sid, pull, say)

    ok_all = all(s["ok"] for s in res["steps"]
                 if s["name"] in ("file", "archive", "edition", "sidecar",
                                  "series", "mapping"))
    res["state"] = "ok" if ok_all else "partial"
    return res


def _maybe_pull(res: dict, sid: int, pull: str | bool, say) -> None:
    """按 pull 策略把掌阅进度落到 Kavita (只前进)。

    pull=True   → 总是 pull
    pull="auto" → 只有这本书在 Kavita 还没有任何进度时才 pull (新书)
    pull=False  → 不 pull
    """
    if pull is False:
        res.setdefault("steps", []).append(
            {"name": "pull", "ok": True, "detail": "跳过 (未要求)"})
        return
    if pull == "auto":
        cur = _pages_read(sid)
        if cur > 0:
            res.setdefault("steps", []).append(
                {"name": "pull", "ok": True,
                 "detail": f"跳过 (Kavita 已有进度 {cur} 页, 用「从掌阅拉」手动覆盖)"})
            return
    try:
        from . import sync as syncmod                    # noqa: PLC0415 — 环形 import
        out = syncmod.pull_once(series_ids={sid}, only_forward=True)
        res["pull"] = out
        _step(res, "pull", not out.get("error"),
              f"updated={out.get('updated')} skipped={out.get('skipped')}"
              + (f" error={out['error']}" if out.get("error") else ""))
        say(f"pull: {out}")
    except Exception as e:                              # noqa: BLE001
        res["pull"] = {"error": f"{type(e).__name__}: {e}"}
        _step(res, "pull", False, res["pull"]["error"])
        say(f"pull 失败 (不影响入库): {res['pull']['error']}")


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
