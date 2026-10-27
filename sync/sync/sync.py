"""双向同步主流程。

push (Kavita -> 掌阅): 每本 Kavita 已读的章节, 把百分比推到掌阅
pull (掌阅 -> Kavita): getProgress, 更新 PagesRead / TotalReads

策略:
    - 章节同步粒度: 单章。Kavita PagesRead / Chapter.Pages = 进度比
    - chapter_id 锚定: 首次同步该书时把章节 ID 写进 mapping, 之后继续用,
      若 Kavita 已读到新章, 用映射的 "锚 + 偏移" 估算 (现在的实现:
      仅推当前已读章, 不动 chapterId 在掌阅侧含义)
    - 推送 failure: 落 state/sync.log, 移动到一个 next_retry_at 表, 跳过不阻塞
"""
from __future__ import annotations
import json
import logging
import os
import re
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from . import kavita_db, mapping, zy_client, zpct, kpages
from .config import ensure_dirs, load_zy_auth, LOG_DIR, find_zy_epub, PULL_PAGE_MODE


log = logging.getLogger("zhangyue-bridge.sync")

# 允许 push 把掌阅进度往回拨 (默认关)。只有"我在 Kavita 里故意重置了进度"
# 这种情况才需要打开: ZY_PUSH_ALLOW_REWIND=1
ALLOW_PUSH_REWIND = (os.environ.get("ZY_PUSH_ALLOW_REWIND") or "").strip().lower() in (
    "1", "true", "yes", "on")


def _setup_log():
    ensure_dirs()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(LOG_DIR / "sync.log"),
            logging.StreamHandler(),
        ],
        force=True,
    )


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _normalize_percent(raw) -> tuple[float, bool]:
    """掌阅 readPercent -> 0.0-1.0。

    2026-10-22 反推结论 (notes/01-readpercent-formula.md): App 写的三个
    非零样本全是 0-1 比例 (狐 0.29326823 / 藤01 0.009963691 /
    藤03 0.98467165)。唯一的 "100" 是**我们自己**用 kavita-bridge 写
    进去的剑来记录。所以这里只做 float() + 钳位, 不做量纲嗅探。
    """
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 0.0, False
    if v < 0:
        return 0.0, False
    if v > 1.0:
        v = v / 100.0          # 我们自己写的旧记录 (百分数形态)
    return min(1.0, v), True


_NB_CACHE: dict = {"ts": 0.0, "data": None}


def _zy_notebook_progress(force: bool = False) -> dict[str, tuple[float | None, str | None, int]]:
    """getUserNotebooks 里的 per-book 进度 —— 掌阅有**两套**进度存储。

    1. ``uploadProcess`` / ``getProgress``: 我们 push 写这里, 也是原来 pull
       唯一读的地方。
    2. ``getUserNotebooks`` 的 ``readpostion`` / ``readpercent``: **App 自己**
       写这里 (带 deviceName / updatetime), 有自己的更新节奏。

    实测 2026-10-26 剑来: App 已读到 27.755% (spine 158), 而 getProgress 还
    停在我们 00:39 push 的 27.387% (spine 155) → 5012 反复 pull 只能看到
    27.4%, 用户看到的是 App 里的 27.7%。两份取**更靠前**的那份。

    返回 {bookid 前 32 hex 小写: (pct01|None, readpostion|None, updatetime_ms)}
    """
    now = time.time()
    if (not force and _NB_CACHE.get("data") is not None
            and now - float(_NB_CACHE.get("ts") or 0) < 120):
        return _NB_CACHE["data"]
    out: dict[str, tuple[float | None, str | None, int]] = {}
    try:
        from . import zy_notes            # 延迟 import: zy_notes 依赖较重
        rows = zy_notes.notebooks() or []
    except Exception:
        log.warning("getUserNotebooks 读取失败, 本轮只看 getProgress", exc_info=True)
        rows = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        bid = str(row.get("bookid") or "").strip().lower()[:32]
        if not bid:
            continue
        pct, ok = _normalize_percent(row.get("readpercent"))
        try:
            ts = int(row.get("updatetime") or 0)
        except (TypeError, ValueError):
            ts = 0
        out[bid] = (pct if ok else None, row.get("readpostion") or None, ts)
    _NB_CACHE.update(ts=now, data=out)
    return out


_HEX64_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def app_book_keys(zy_book_id: str) -> list[str]:
    """掌阅 App 侧真正读写进度用的 bookId 列表 (顺序: App 优先)。

    2026-10-26 实测 (这是"push 了手机没变"的真正原因):

      * App 拉进度 (`kd/e.java:o()`, `q6()`) 用的是
        `BookHolder.mLocalBookFileMd5` = `core.getFileMD5(file).toUpperCase()`
        —— **32 hex 大写 = 文件内容 MD5**
      * 云书架给的是 64 hex (= MD5 + MD5(path)), 我们以前只往这条写
      * 这两条在服务端是**两份独立记录**, 互不同步:
        我们写 64 后 App 读 32 仍是它自己的旧值, 所以永远"没对上"

    所以本地导入书必须同时写 32 和 64: 32 给 App 看, 64 保留云书架视图
    (以及历史记录)。非 64 hex 的 id (商店书数字 id 等) 原样返回。
    """
    bid = (zy_book_id or "").strip()
    if not bid:
        return []
    keys = [bid]
    if _HEX64_RE.match(bid):
        k32 = bid[:32].upper()
        if k32 not in keys:
            keys.insert(0, k32)
    return keys


def _canonical_bid(bid: str) -> str:
    return (bid or "").strip().upper()


def _catalog_chapter(geo, spine_chapter: int) -> int:
    """spine 章号 (= locate 给的 chapterId) -> App 的 catalog 章号。

    App 的 `chapterId` 是**目录项下标**: `BookItem.mChapterCount =
    core.getCatalogCount()`, 目录项 = 目录清单 (不含封面/制作信息这些
    前置文档)。而 readPercent 用的是 "已读完几篇 spine", 两者相差
    front = len(spine) - catalog_count 篇。

    实证: 剑来 spine 590 / catalog 588 (front=2), App 记录 ch=157 而
    position pre=159 → 157 = 159 - 2 ✓; 藤井卷03 spine 193 / 192 (front=1)
    → App ch=189, pre=190 ✓。以前我们一律写 spine-1, 只有 front=1 的书
    碰巧对, 剑来这种多前置文档的书 chapterId 会多 1。
    """
    try:
        ch = int(spine_chapter)
    except (TypeError, ValueError):
        return 0
    catalog = getattr(geo, "catalog", 0) or 0
    sizes = getattr(geo, "sizes", None) or []
    if not catalog or not sizes:
        return ch
    front = len(sizes) - int(catalog)
    if front <= 0:
        return ch
    return max(0, ch + 1 - front)


def _best_zy_percent(zy_book_id: str, pct_from_progress: float, ok: bool) -> tuple[float, bool, str]:
    """getProgress 与 getUserNotebooks 两份进度取靠前者。

    返回 (pct01, ok, 来源说明)。App 那份更新时以它为准 —— 读者真实位置在
    App 本地, 它写的 readpercent/readpostion 才是权威。
    """
    nb = _zy_notebook_progress().get(str(zy_book_id or "").strip().lower()[:32])
    if nb and nb[0] is not None and (not ok or nb[0] > pct_from_progress + 1e-9):
        return nb[0], True, "notebooks"
    return pct_from_progress, ok, "progress"


def _pagemap_for(link) -> dict | None:
    """取这本书的 kpages sidecar (Kavita 版重排的映射表)。

    校验: sidecar 的总页数必须跟 Kavita 里 Chapter.Pages 对得上 —— 不一致
    说明库还没重扫 / sidecar 过期, 这时退回旧路径比乱写安全。
    """
    sc = kpages.load(link.kavita_series_id)
    if not sc:
        return None
    chs = kavita_db.list_chapters(link.kavita_series_id)
    cap = sum((c["ChPages"] or 0) for c in chs)
    if cap and cap != len(sc.get("pages", [])):
        log.warning("pagemap series=%s 过期 (kavita pages=%s sidecar pages=%s), 忽略",
                    link.kavita_series_id, cap, len(sc.get("pages", [])))
        return None
    return sc


def _geom_for(link) -> "zpct.BookGeometry | None":
    """拿一本书的 spine 几何。

    优先用 sidecar —— 库里那份 epub 可能已被换成重排版 (spine 页数变了),
    但掌阅的 readPercent 口径永远是**原书**的字节比例。
    """
    sc = kpages.load(link.kavita_series_id)
    if sc:
        return kpages.geometry_from_sidecar(sc)
    path = link.epub_path or find_zy_epub(link.zy_book_name, link.zy_book_id)
    if not path or not Path(path).exists():
        return None
    try:
        return zpct.load_geometry(path, link.zy_book_name)
    except (OSError, ValueError, zipfile.BadZipFile) as e:
        log.warning("geometry load fail %s: %s", path, e)
        return None


def _epub_path_for(link) -> str:
    """拿一本书的本地 epub 绝对路径 (给 DocIndex 用)。"""
    path = link.epub_path or find_zy_epub(link.zy_book_name, link.zy_book_id)
    return path if path and Path(path).exists() else ""


def _zy_position_to_kavita(geo, epub_path: str, zy_chapter_id: int, f3: int
                          ) -> tuple[int, str]:
    """掌阅 (chapterId, f3) → Kavita (PagesRead, BookScrollId xpath)。

    **两侧粒度不同**, 这是整个桥接的核心:

        掌阅: chapterId = "最后一篇读完的文档", f3 = 下一篇里的字节偏移
              → 当前停在 spine[chapterId + 1] 的第 f3 字节
        Kavita: PagesRead = 文档下标 (0 基), BookScrollId = 文档内 xpath
              → 续读页 = spine[PagesRead], 位置在 BookScrollId 指向的元素

    所以 PagesRead = chapterId + 1 (差 1 篇, 见 sync.py 里那张标定表)。
    BookScrollId 则是**可选的精度增强**: 有 epub 时能算出 f3 落在哪个元素,
    写成 xpath 就能让 Kavita 的"继续阅读"直接跳到段落, 而不是只记"第几篇"。

    返回 (pages_read, xpath)。xpath 算不出时返回空串 (调用方保留原值)。
    """
    spine_idx = zy_chapter_id + 1
    if spine_idx < 0:
        spine_idx = 0
    xpath = ""
    if epub_path and 0 <= spine_idx < len(geo.sizes):
        di = zpct.build_doc_index(epub_path, spine_idx)
        if di is not None:
            xp = di.xpath_of_offset(f3)
            # offset 0 落在 <body> 之前 → 没有有意义的元素定位, 别写脏值
            if xp and xp != "//body":
                xpath = xp
    return spine_idx, xpath


def _kavita_position_to_zy(geo, epub_path: str, pages_read: int, book_scroll_id: str
                          ) -> tuple[int, int]:
    """Kavita (PagesRead, BookScrollId) → 掌阅 (chapterId, f3 字节偏移)。

    push 侧的逆运算。有 BookScrollId 时能算出文档内的精确字节偏移,
    不然就退回"章首" (f3=0, 误差 ≤ 一篇文档长度)。

    注意 PagesRead 的语义是"续读页的下标" = spine[PagesRead],
    而掌阅 chapterId 是"最后一篇读完的文档" = spine 下标 - 1。
    """
    spine_idx = max(0, min(pages_read, len(geo.sizes)))
    chapter_id = max(0, spine_idx - 1)
    f3 = 0
    if book_scroll_id and epub_path and 0 <= spine_idx < len(geo.sizes):
        di = zpct.build_doc_index(epub_path, spine_idx)
        if di is not None:
            off = di.offset_of_xpath(book_scroll_id)
            if off:
                f3 = off
    return chapter_id, f3


def _absolute_position(geo, pages_read: int, book_scroll_id: str, epub_path: str
                       ) -> float:
    """Kavita 进度 → 全书绝对字节位置 (0.0-1.0)。

    用于比较"谁更靠前"。PagesRead 只能定到文档级, BookScrollId 能定到
    文档内字节 —— 两者合起来才能跟掌阅的 pct 同精度比较。
    """
    total = geo.total_bytes
    if total <= 0:
        return 0.0
    spine_idx = max(0, min(pages_read, len(geo.sizes)))
    prefix = geo.prefix_bytes(spine_idx - 1) if spine_idx > 0 else 0
    inner = 0
    if book_scroll_id and epub_path and 0 <= spine_idx < len(geo.sizes):
        di = zpct.build_doc_index(epub_path, spine_idx)
        if di is not None:
            inner = di.offset_of_xpath(book_scroll_id)
    return max(0.0, min(1.0, (prefix + inner) / total))


def _push_chapter_from_kavita(link, pages_read: int) -> int:
    """把 Kavita 的 pages_read 落到掌阅 chapterId。

    关键事实 (2026-10-22 验证): Kavita 对 epub 是 **1 个 spine 文档 = 1 页**。
        狐的故事   Chapter.Pages=9   spine=9
        剑来       Chapter.Pages=590 spine=590

    所以页号直接就是 spine 下标, 不需要字节换算:

        PagesRead = N  →  已读完 spine[0 .. N-1]  →  当前在 spine[N]
                     →  掌阅 chapterId = spine 下标 - 1 = N - 1

    字节比例法只在页号与 spine 索引失配时才用 (Kavita 页数被人工改过时)。

    2026-10-24 修: 卷级映射 (多卷一本书) 时页号只对**那一卷**的 Pages,
    拿系列合页数算比例会把 卷03 的 190/193 算成 62 号章 (整系列 581 页里的
    位置)。所以卷级行必须只跟自己那一章的 ChPages 比。
    """
    geo = _geom_for(link)
    if not geo:
        return int(link.zy_anchor_chapter_id or 0)
    kchs = kavita_db.list_chapters(link.kavita_series_id)
    if not kchs:
        return int(link.zy_anchor_chapter_id or 0)
    n_spine = len(geo.sizes)

    if not link.series_level:
        # 卷级: 只看自己这一卷
        mine = next((k for k in kchs if k["ChId"] == link.kavita_chapter_id), None)
        if mine is None:
            return int(link.zy_anchor_chapter_id or 0)
        cpages = mine["ChPages"] or 0
        if cpages == n_spine:
            idx = max(0, min(pages_read, n_spine))
            return max(0, idx - 1)
        if cpages <= 0:
            return int(link.zy_anchor_chapter_id or 0)
        target_bytes = min(1.0, pages_read / cpages) * geo.total_bytes
        cum = 0
        for idx, sz in enumerate(geo.sizes):
            cum += sz
            if cum >= target_bytes:
                return max(0, idx - 1)
        return max(0, n_spine - 2)

    total_pages = sum(k["ChPages"] or 0 for k in kchs)
    if total_pages <= 0:
        return int(link.zy_anchor_chapter_id or 0)

    # 首选: 页号直译 (页 == spine 文档)
    if len(kchs) == 1 and kchs[0]["ChPages"] == n_spine:
        idx = max(0, min(pages_read, n_spine))     # 当前 spine 下标
        return max(0, idx - 1)                             # -> chapterId

    # 回退: 字节比例定位 (页数与 spine 数不一致)
    ratio = max(0.0, min(1.0, pages_read / total_pages))
    target_bytes = ratio * geo.total_bytes
    cum = 0
    for idx, sz in enumerate(geo.sizes):
        cum += sz
        if cum >= target_bytes:
            return max(0, idx - 1)
    return max(0, len(geo.sizes) - 2)


PUSH_REWIND_TOLERANCE_PAGES = 0.5   # 允许回退的阈值 (半页以内不算回退)


def _total_count_for(link, geo, cur_total: int | None,
                     chapter_id: int | None = None) -> int:
    """掌阅 readProcess.totalCount 该写什么。

    语义 (2026-10-24 从 apk 反查, 见 notes/04-totalcount-semantics.md):
        掌阅 App 写的是 `Book.K()` → `core.getCatalogCount()`, 即**引擎的
        目录章数** (catalogCount), chapterId 的合法范围是 0..catalogCount-1。
        * 不是页数, 不是 spine 长度。
        * BookHolder.mChapterCount 也直接吃这个值, 书架"第 N 章/剩余 N 章"
          的文案就靠它 (pf/z.java Q0 里 `i11 - i12`、`i12 + 1`)。

    取值优先级:
      1. 掌阅现有值, 只要它**自洽** (> 本轮要写的 chapterId) —— App 自己写的
         最准, 我们算的可能差 1~2 (剑来 NCX 588 vs spine-1 589)。
      2. epub 的目录章数 (NCX navMap / spine 剔封面)。
      3. 映射表里的旧值。
    """
    ch = chapter_id
    if ch is None:
        try:
            ch = int(link.zy_anchor_chapter_id or 0)
        except (TypeError, ValueError):
            ch = 0
    if cur_total and cur_total >= 2 and cur_total >= ch + 1:
        return int(cur_total)                       # 1) 掌阅自洽值, 原样保留
    if geo is not None and geo.total_count > 0:
        return int(geo.total_count)                 # 2) 目录章数
    return int(link.kavita_chapter_count or 8)


def push_decision(cur_pct, cur_done, new_pct, new_done, pages: int, *,
                  allow_rewind: bool = False, meta_stale: bool = False,
                  ) -> tuple[bool, str]:
    """要不要把 Kavita 的进度写在掌阅上。

    掌阅是真正在读的那一端 (手机), Kavita 只是镜像: 镜像落后时 pull 会把它
    拉上来。所以 **push 只在真前进时才写**。

    2026-10-23 事故: 没有这道闸时, 每次 push 都把掌阅拨回"当前页开头"
    (页 48 起点 = 47.98%), 读者会看到掌阅进度在 48.96% → 47.98% 之间来回跳,
    而且每 30s daemon 一轮就再拨一次。

    meta_stale (2026-10-24 加): 进度没动, 但**元信息**写错了 (totalCount /
    chapterId)。这时允许"进度不变"地写一次 —— 否则错误的 totalCount=1
    永远修不掉, 因为 pct 一样必然被判 noop。回退闸仍然优先。
    """
    if pages <= 0:
        pages = 1
    tol = PUSH_REWIND_TOLERANCE_PAGES / pages
    if cur_pct is None:
        return True, "no-baseline"
    if cur_done and not new_done:
        return False, "zy-already-done"
    if new_done and not cur_done:
        return True, "finish"
    if not allow_rewind:
        if new_pct < cur_pct - tol:
            return False, "regress(%.4f<%.4f,pages=%s)" % (new_pct, cur_pct, pages)
        if abs(new_pct - cur_pct) <= tol * 0.5:
            # 元信息修一次可以, 但**绝不为修元信息把进度往回拨** —— 掌阅是
            # 读者眼前那一端, 少 1 页也不该由我们制造。
            if meta_stale and new_pct >= cur_pct - 1e-6:
                return True, "meta"
            return False, "noop"
    return True, "forward"


# ---- PUSH ---------------------------------------------------------------

def push_once(auth: dict | None = None, *, dry_run: bool = False,
              series_ids: set[int] | None = None) -> dict:
    """Kavita -> 掌阅 单次同步. 返回 {pushed, skipped, failed, detail:[]}.

    series_ids: 只处理这些 Kavita series (自动钩子用, 单本触发)。
    """
    _setup_log()
    auth = auth or load_zy_auth()
    # 卷级映射: key = (series_id, chapter_id), chapter_id=-1 表示整本/系列级。
    # 多卷系列 (一个 Kavita 系列挂多本掌阅书) 靠这个区分, 否则进度会串到同一本。
    chosen = [l for l in mapping.all_links()
              if series_ids is None or l.kavita_series_id in series_ids]
    links = {(l.kavita_series_id, l.kavita_chapter_id): l for l in chosen}
    pushed = skipped = failed = 0
    detail: list[dict] = []
    # 掌阅侧当前进度 (防回退闸的基线)。取不到就降级成 no-baseline, 不阻塞推送。
    # bid -> (pct01|None, finished, 掌阅 chapterId, 掌阅 totalCount)
    # 注意: 32 hex (App 真正读的那条) 与 64 hex (云书架 id) 是**两份独立记录**,
    # 两边历史值可能不同 → 全部读回来, 取"最靠前"的那份当基线。
    zy_state: dict[str, tuple[float | None, bool, str | None, int | None]] = {}
    key_owner: dict[str, str] = {}
    all_keys: list[str] = []
    try:
        for l in chosen:
            for k in app_book_keys(l.zy_book_id):
                key_owner[_canonical_bid(k)] = l.zy_book_id
                if k not in all_keys:
                    all_keys.append(k)
        recs = zy_client.get_progress(auth, all_keys) or {}
        for key, rec in recs.items():
            owner = key_owner.get(_canonical_bid(key)) or key_owner.get(_canonical_bid(key[:32])) or _canonical_bid(key)
            rp = (rec or {}).get("readProcess") or {}
            v, ok = _normalize_percent(rp.get("readPercent"))
            # 防回退基线也要看 App 那份 (getUserNotebooks), 否则 App 读到
            # 27.7% 时我们还以为掌阅只到 27.4%, 会把进度往回拨。
            v, ok, _src = _best_zy_percent(owner, v, ok)
            prev = zy_state.get(owner)
            if prev is not None and prev[0] is not None and (not ok or v <= prev[0] + 1e-12):
                continue      # 保留更靠前的那条记录 (含它的 chapterId/totalCount)
            try:
                tc = int(str(rp.get("totalCount")))
            except (TypeError, ValueError):
                tc = None
            zy_state[owner] = (v if ok else None,
                               str(rp.get("readComplete") or "0").strip() == "1",
                               (str(rp["chapterId"]) if rp.get("chapterId") not in (None, "")
                                else None),
                               tc)
    except Exception:
        log.warning("push: 读掌阅当前进度失败, 本轮回退检查降级", exc_info=True)
    # getProgress 里根本没这本书 (从没同步过) 但 App 有记录 → 也当基线
    try:
        nb_all = _zy_notebook_progress()
        for l in chosen:
            if l.zy_book_id in zy_state:
                continue
            nb = nb_all.get(str(l.zy_book_id).strip().lower()[:32])
            if nb and nb[0] is not None:
                zy_state[l.zy_book_id] = (nb[0], False, None, None)
    except Exception:
        log.debug("push: notebooks 基线合并失败", exc_info=True)
    for p in kavita_db.iter_progress():
        if series_ids is not None and p.series_id not in series_ids:
            continue
        link = (links.get((p.series_id, p.chapter_id))
                or links.get((p.series_id, mapping.SERIES_LEVEL)))
        refined = False          # 只有走重排版(页内定位)路径才会变 True
        if not link:
            skipped += 1
            log.info("skip series=%s chapter=%s no link", p.series_id, p.chapter_id)
            continue
        # 掌阅 readPercent = (Σ bytes(spine[0..chapterId]) + f3) / Σ bytes(spine)
        # (2026-10-22 反推, float32 bit 级吻合, 见 notes/01-readpercent-formula.md)
        geo = _geom_for(link)
        pm = _pagemap_for(link)
        # 掌阅侧基线先取出来: 后面几个分支都要用 (cur_ch 还兼作 anchor 兜底),
        # 放在分支之后再解包会踩 UnboundLocalError。
        cur_pct, cur_done, cur_ch, cur_total = zy_state.get(
            link.zy_book_id, (None, False, None, None))
        if pm and geo:
            # 重排版: 页号 → 原书字节位置 → (chapterId, f3) → 掌阅百分比/位置
            refined = False
            if p.pages_read >= len(pm["pages"]):
                g = pm["src_total_bytes"]
            else:
                g = kpages.bytes_of_page(pm, p.pages_read)
                # 页内精度: Kavita 只在 PagesRead 里记"读到第几页", 页内位置在
                # BookScrollId。不还原它的话, 大书 (一页 2 万字) 读一小时掌阅
                # 侧的 pct 都不会动。
                if p.book_scroll_id:
                    g2 = kpages.bytes_of_scroll_id(pm, p.pages_read, p.book_scroll_id)
                    pg = pm["pages"][p.pages_read]
                    if g2 is not None and g <= g2 <= pg["e"]:
                        g = g2
                        refined = True
            chapter_id, f3 = geo.locate(g)
            pct01 = geo.percent(chapter_id, f3)
            total_count = str(_total_count_for(link, geo, cur_total, chapter_id))
            position = zpct.build_position(chapter_id, f3)
        elif geo:
            chapter_id = _push_chapter_from_kavita(link, p.pages_read)
            # f3 (章内字节偏移) 我们算不出来 — 书是整本 EPUB 传书,
            # 没有章节正文接口。写 0 = "停在章首", 误差 ≤ 一篇文档长度。
            pct01 = geo.percent(chapter_id, 0)
            total_count = str(_total_count_for(link, geo, cur_total, chapter_id))
            position = zpct.build_position(chapter_id, 0)
        else:
            # 退回旧路径: 没 epub 几何就只有 Kavita 的章级比例
            pct01 = min(1.0, max(0.0, p.percent / 100.0))
            # anchor 空时不能写 0: 掌阅 readPosition 的 chapterId 会被当成"第 0 篇"
            chapter_id = int(link.zy_anchor_chapter_id or cur_ch or 0)
            total_count = str(link.kavita_chapter_count or 8)
            position = (mapping.state_get(f"zy_position_{p.series_id}")
                        or zpct.build_position(chapter_id, 0))
        complete = "1" if p.finished else "0"
        # chapterId 字段要用 **目录项下标** (App 口径), 而 readPosition 里用的是
        # spine 下标。两者相差"前置文档数", 以前混用导致剑来这类多前置文档的
        # 书记录 chapterId 多 1 (见 _catalog_chapter)。
        catalog_chapter = _catalog_chapter(geo, chapter_id)
        # 防回退闸: 只有真前进才写掌阅 (掌阅才是读者在用的那一端)
        n_pages = len(pm["pages"]) if pm else (geo.total_count if geo else 100)
        # 元信息写错 (totalCount / chapterId) 但进度一致时也要修一次,
        # 否则 "totalCount=1" 这种值永远修不掉 (pct 不变 → 被判 noop)。
        meta_stale = False
        if cur_total is not None and str(cur_total) != str(total_count):
            meta_stale = True
        if cur_ch is not None and str(cur_ch) != str(catalog_chapter):
            meta_stale = True
        allow, why = push_decision(cur_pct, cur_done, pct01, complete == "1", n_pages,
                                   allow_rewind=ALLOW_PUSH_REWIND,
                                   meta_stale=meta_stale)
        if not allow:
            skipped += 1
            log.info("skip push series=%s (%s) kavita=%.4f zy=%s",
                     p.series_id, why, pct01,
                     "?" if cur_pct is None else round(cur_pct, 4))
            detail.append({"series": p.series_id, "chapter": p.chapter_id,
                           "skipped": why,
                           "pct": round(pct01, 6),
                           "zy": None if cur_pct is None else round(cur_pct, 6)})
            continue
        if dry_run:
            log.info("DRY push bid=%s ch=%s pct01=%s done=%s geo=%s why=%s",
                     link.zy_book_id, chapter_id, round(pct01, 6), complete,
                     bool(geo), why)
            detail.append({"bid": link.zy_book_id, "pct": round(pct01, 6),
                           "chapter": chapter_id, "dry": True, "why": why})
            continue
        try:
            # 32 hex (App 读的那条) + 64 hex (云书架 id) 各写一份 —— 服务端
            # 是两份独立记录, 只写 64 App 永远看不到 (见 app_book_keys)。
            ok_keys, errs = [], []
            for key in app_book_keys(link.zy_book_id):
                try:
                    zy_client.upload_progress(
                        auth,
                        book_id=key,
                        book_name=link.zy_book_name,
                        chapter_id=catalog_chapter,
                        percent=f"{pct01:.8f}".rstrip("0").rstrip("."),
                        read_complete=complete,
                        total_count=total_count,
                        read_position=position,
                    )
                    ok_keys.append(key)
                except Exception as e:
                    errs.append(f"{key[:8]}:{e}")
            if not ok_keys:
                raise RuntimeError("; ".join(errs) or "no key")
            pushed += 1
            mapping.state_set(f"push_last_{p.series_id}", _now())
            # 按 (series, chapter) 也记一份 —— 多卷系列 (藤井) 一个 series 挂多本,
            # 阅读器角标要显示"当前这本"的上次推送时间, 不能用系列级的。
            _lk = f"push_last_{p.series_id}_{p.chapter_id}"
            mapping.state_set(_lk, _now())
            mapping.state_set(_lk + "_pct", f"{pct01:.8f}")

            detail.append({"bid": link.zy_book_id, "series": p.series_id,
                           "chapter": p.chapter_id, "pct": round(pct01, 6), "ok": True,
                           "keys": [k[:8] for k in ok_keys]})
            log.info("push OK bid=%s pct01=%s%s%s", link.zy_book_id, round(pct01, 6),
                     " (页内)" if refined else "",
                     ("  keys=" + ",".join(k[:8] for k in ok_keys)) if len(ok_keys) > 1 else "")
            if errs:
                log.warning("push 部分 key 失败 bid=%s %s", link.zy_book_id, "; ".join(errs))
            detail[-1]["refined"] = refined
        except Exception as e:
            failed += 1
            log.exception("push FAIL bid=%s", link.zy_book_id)
            detail.append({"bid": link.zy_book_id, "series": p.series_id,
                           "chapter": p.chapter_id, "err": str(e)})
        time.sleep(0.3)  # 友好: 不连轰
    mapping.state_set("push_last_run_utc", _now())
    return {"pushed": pushed, "skipped": skipped, "failed": failed, "detail": detail}


# ---- PULL ---------------------------------------------------------------

def pull_once(auth: dict | None = None, *, dry_run: bool = False,
              series_ids: set[int] | None = None,
              only_forward: bool = False) -> dict:
    """掌阅 -> Kavita. 把掌阅侧已知进度更新进 Kavita.

    series_ids:   只处理这些 Kavita series (自动钩子用, 单本触发)。
    only_forward: True 时本地比掌阅靠前就不写 (自动钩子用)。
                  手动 pull 仍是"掌阅权威", 可以往回覆盖。
    """
    _setup_log()
    auth = auth or load_zy_auth()
    links = [l for l in mapping.all_links()
             if series_ids is None or l.kavita_series_id in series_ids]
    if not links:
        return {"fetched": 0, "updated": 0, "skipped": 0}
    # 32 hex (App 真正读写的记录) 与 64 hex (云书架 id) 是两份独立记录 ——
    # 两边都有值时取"最靠前"的那份, 否则 App 侧新写的进度会被旧记录盖掉。
    key_owner: dict[str, str] = {}
    all_keys: list[str] = []
    for l in links:
        for k in app_book_keys(l.zy_book_id):
            key_owner[_canonical_bid(k)] = l.zy_book_id
            if k not in all_keys:
                all_keys.append(k)
    try:
        zy_raw = zy_client.get_progress(auth, all_keys) or {}
    except Exception as e:
        log.exception("getProgress fail"); return {"error": str(e)}
    zy_rec: dict[str, dict] = {}
    for key, rec in zy_raw.items():
        owner = key_owner.get(_canonical_bid(key)) or key_owner.get(_canonical_bid(key[:32])) or key
        rp = (rec or {}).get("readProcess") or {}
        v, ok = _normalize_percent(rp.get("readPercent"))
        prev = zy_rec.get(owner)
        if prev is not None:
            pv, pok = _normalize_percent((prev.get("readProcess") or {}).get("readPercent"))
            if pok and ok and v <= pv + 1e-12:
                continue
            if pok and not ok:
                continue
        zy_rec[owner] = rec
    fetched = len(zy_rec)
    updated = skipped = 0
    detail: list[dict] = []
    for l in links:
        rec = zy_rec.get(l.zy_book_id)
        if not rec or not rec.get("readProcess"):
            skipped += 1
            continue
        rp = rec["readProcess"]
        pct01, pct_ok = _normalize_percent(rp.get("readPercent"))
        # App 自己写的那份可能更新 (见 _zy_notebook_progress): 取靠前者,
        # 否则 App 已读到 27.7% 我们还在拿 27.4% 反复 pull。
        pct01, pct_ok, pct_src = _best_zy_percent(l.zy_book_id, pct01, pct_ok)
        if pct_src != "progress":
            log.info("pull series=%s 用 App 侧进度 %.4f%% (getProgress=%.4f%%)",
                     l.kavita_series_id, pct01 * 100.0,
                     (_normalize_percent(rp.get("readPercent"))[0]) * 100.0)
        ch = rp.get("chapterId") or "0"
        pct = pct01 * 100.0
        kavita_chapters = kavita_db.list_chapters(l.kavita_series_id)
        if not kavita_chapters:
            skipped += 1
            continue
        # 掌阅 chapterId 是掌阅私有章号, 与 Kavita Chapter.Id 是两套编号,
        # 直接相等匹配几乎必然失败 → 必然落到 fallback。
        # Kavita 里一本书 = 1 个 chapter (按 volume 合并), 所以章内定位
        # 只能靠比例本身, 落到唯一那个 chapter 上。
        if not l.series_level:
            # 卷级映射: 这一行只负责它自己那一个 chapter/卷, 不做猜章
            target = next((c for c in kavita_chapters
                           if c["ChId"] == l.kavita_chapter_id), None)
            if not target:
                skipped += 1
                log.warning("pull series=%s link -> chapter=%s 在库里不存在 (卷被重建?)",
                            l.kavita_series_id, l.kavita_chapter_id)
                detail.append({"kavita_series_id": l.kavita_series_id,
                               "kavita_chapter_id": l.kavita_chapter_id,
                               "skipped": "chapter_missing"})
                continue
        else:
            target = next((c for c in kavita_chapters if str(c["ChId"]) == ch), None)
            if not target and ch != "0":
                target = kavita_chapters[0]
            elif ch == "0":
                target = kavita_chapters[-1] if pct01 >= 0.995 else kavita_chapters[0]
            if not target:
                skipped += 1
                continue
        # 页号两条口径 (见 config.PULL_PAGE_MODE 的说明):
        #   掌阅 chapterId = "最后一篇读完的文档", f3 = 下一篇里的字节偏移
        #                → 当前停在 spine[chapterId + 1]
        #   Kavita 的书架百分比 = PagesRead / Chapter.Pages (前端自己算),
        #       页数 = spine 篇数, 而且 reader 的 loadPage(pageNum) 直接取
        #       spine[pageNum] —— 百分比和续读位置绑在同一个数上。
        #   各篇字节数不均时二者不可兼得, 默认按百分比反算。
        geo = _geom_for(l)
        epub_path = _epub_path_for(l)
        pm = _pagemap_for(l)
        zy_chapter_id = None
        f3 = 0
        true_spine = 0
        cap = target["ChPages"] or 0
        scroll_id = None
        zy_done = (str(rp.get("readComplete") or "0").strip() == "1")
        if pm:
            # 重排版: 页 = 原书字节的等分切片, 页号直接由掌阅 pct 算出。
            # 这一步同时解决两个老问题:
            #   * 百分比: pages_read/N == pct (误差 < 1 页)
            #   * 续读位置: 打开的就是那段正文, 不再是"目录页/封面"
            # 旧 BookScrollId 是上一套页号下的 xpath, 一律清掉。
            pages_read = kpages.pages_read_for_pct(pm, pct01, finished=zy_done)
            if cap:
                pages_read = max(0, min(pages_read, cap))
            # 页内精确定位 (可选增强): 有 xpath 就落到同一段落, 没有就页首。
            # 旧 BookScrollId 是上一套页号下的 xpath, 必须重算不能留。
            scroll_id = kpages.scroll_id_for_pct(pm, pct01)
        elif geo:
            zy_chapter_id, f3 = geo.solve(pct01)
            true_spine = zy_chapter_id + 1
            if PULL_PAGE_MODE == "position":
                pages_read = true_spine
            else:
                pages_read = int(round(pct01 * cap)) if cap else true_spine
        else:
            # 无几何时只能线性估, 只能保证不越界, 精度不指望
            pages_read = int(round(pct01 * cap)) if cap else 0
        if cap and not pm:
            pages_read = max(0, min(pages_read, cap))
        total_reads = 1 if (zy_done
                            or (cap and pages_read >= cap)
                            or pct01 >= 0.995) else 0

        # BookScrollId 只在"页号 == 真实所在篇"时写 —— 否则 xpath 指到别的
        # spine 文档, Kavita 打开时会读不到该节点, 等于脏数据。
        if not pm and geo and zy_chapter_id is not None:
            _spine, xp = _zy_position_to_kavita(geo, epub_path, zy_chapter_id, f3)
            if xp and _spine == pages_read:
                scroll_id = xp

        # 防回退: 掌阅解析异常 (bad readPercent / 算出 0 页) 时不许把
        # Kavita 里已有的进度抹成 0 —— 那正是"打开从头开始"的成因。
        if not pct_ok:
            log.warning("pull series=%s SKIP bad readPercent=%r (keep local)",
                        l.kavita_series_id, rp.get("readPercent"))
            skipped += 1
            detail.append({"kavita_series_id": l.kavita_series_id, "skipped": "bad_percent"})
            continue
        prev = kavita_db.get_progress_for_chapter(target["ChId"])
        if prev is None and pages_read == 0:
            skipped += 1          # 掌阅没读、本地也没进度 → 不建空行
            detail.append({"kavita_series_id": l.kavita_series_id,
                           "kavita_chapter_id": target["ChId"],
                           "skipped": "zero_no_prev"})
            continue
        if prev is not None and prev[0] > 0 and pages_read == 0:
            log.warning("pull series=%s SKIP would regress %s->0 (keep local)",
                        l.kavita_series_id, prev[0])
            skipped += 1
            detail.append({"kavita_series_id": l.kavita_series_id,
                           "skipped": "would_regress", "local_pages": prev[0]})
            continue
        # 掌阅是唯一真正在读的一端, 以它为准: 本地比它靠前也照样覆盖。
        # (旧版这里 skip, 结果 Kavita 里 100% 的书永远拉不回来 —— 剑来卡 590/590)
        prev_pages = prev[0] if prev else 0
        if prev is not None and pages_read < prev_pages:
            if only_forward:
                log.info("pull series=%s ch=%s auto hold local %s > zy %s (不自动回退)",
                         l.kavita_series_id, target["ChId"], prev_pages, pages_read)
                if l.zy_anchor_chapter_id != ch:
                    mapping.update_anchor(l.kavita_series_id, ch, l.kavita_chapter_id)
                skipped += 1
                detail.append({"kavita_series_id": l.kavita_series_id,
                               "skipped": "auto_hold", "local_pages": prev_pages,
                               "zy_pages": pages_read})
                continue
            log.info("pull series=%s overwrite local %s -> %s (zy 权威)",
                     l.kavita_series_id, prev_pages, pages_read)

        # 锚定更新 (校验通过后再写, 避免脏值污染 mapping)
        if l.zy_anchor_chapter_id != ch:
            mapping.update_anchor(l.kavita_series_id, ch, l.kavita_chapter_id)
        if dry_run:
            detail.append({"kavita_series_id": l.kavita_series_id,
                           "kavita_chapter_id": target["ChId"],
                           "target_chapter": target["ChTitle"],
                           "pct": pct, "pages_read": pages_read,
                           "pages": cap, "mode": PULL_PAGE_MODE,
                           "scroll_id": scroll_id, "dry": True})
            continue
        try:
            kavita_db.upsert_progress(
                chapter_id=target["ChId"], pages_read=pages_read,
                total_reads=total_reads, book_scroll_id=scroll_id,
            )
            updated += 1
            shown = (pages_read * 100.0 / cap) if cap else 0.0
            detail.append({"kavita_series_id": l.kavita_series_id,
                           "kavita_chapter_id": target["ChId"],
                           "target_chapter": target["ChTitle"], "pct": round(pct, 4),
                           "kavita_pct": round(shown, 2), "pages_read": pages_read,
                           "pages": cap, "ok": True})
            log.info("pull OK series=%s chapter=%s/%s zy=%.2f%% kavita=%s/%s=%.1f%%",
                     l.kavita_series_id, target["ChId"], target["ChTitle"], pct,
                     pages_read, cap, shown)
            if cap and abs(shown - pct) > 3.0:
                log.warning("pull series=%s 粒度限制: zy=%.2f%% 只能落到 %s/%s=%.1f%% "
                            "(篇数与篇幅不均; 想按位置写: ZY_PULL_PAGE_MODE=position)",
                            l.kavita_series_id, pct, pages_read, cap, shown)
        except Exception:
            log.exception("pull FAIL"); detail.append({"err": "see log"})
    mapping.state_set("pull_last_run_utc", _now())
    return {"fetched": fetched, "updated": updated, "skipped": skipped, "detail": detail}


# ---- STATUS -------------------------------------------------------------

def status() -> dict:
    _setup_log()
    links = mapping.all_links()
    auth = load_zy_auth()
    bid_list = [l.zy_book_id for l in links]
    zy = {}
    if bid_list:
        try:
            zy = zy_client.get_progress(auth, bid_list)
        except Exception as e:
            zy = {"_error": str(e)}
    out = {"links": [], "last_push": mapping.state_get("push_last_run_utc"),
           "last_pull": mapping.state_get("pull_last_run_utc")}
    for l in links:
        rec = zy.get(l.zy_book_id, {}) if isinstance(zy, dict) and "_error" not in zy else {}
        rp = rec.get("readProcess") if rec else None
        zy_pct01, _ = _normalize_percent(rp.get("readPercent")) if rp else (None, False)
        out["links"].append({
            "kavita_series_id": l.kavita_series_id,
            "kavita_chapter_id": l.kavita_chapter_id,
            "zy_book_id": l.zy_book_id,
            "zy_book_name": l.zy_book_name,
            "anchor": l.zy_anchor_chapter_id,
            "zy_pct": round(zy_pct01, 4) if zy_pct01 is not None else None,
            "zy_pct_raw": rp.get("readPercent") if rp else None,
            "zy_chapter": rp.get("chapterId") if rp else None,
            "zy_updated": rec.get("updateTime") if rec else None,
        })
    return out


# ---- DAEMON -------------------------------------------------------------

def daemon(poll_seconds: int = 30) -> None:
    _setup_log()
    log.info("daemon starting, poll=%ss", poll_seconds)
    last_pull = 0.0
    while True:
        try:
            r = push_once()
            log.info("push: %s", r)
            # pull 比 push 慢, 5 分钟一次
            if time.time() - last_pull > 300:
                r = pull_once()
                log.info("pull: %s", r)
                last_pull = time.time()
        except KeyboardInterrupt:
            log.info("daemon exiting"); return
        except Exception:
            log.exception("daemon iter error")
        time.sleep(poll_seconds)
