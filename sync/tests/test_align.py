"""两套系统进度口径的对齐测试 (2026-10-23).

跑法:  cd /vol2/1001/zhangyue-bridge/sync && python3 -m pytest tests/test_align.py -q
      (没装 pytest 也能当脚本: python3 tests/test_align.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sync import kpages                       # noqa: E402
from sync.sync import push_decision           # noqa: E402


def _sc(n=100, T=327151, docs=(433, 1233, 2978, 2781, 1341, 75425, 80464, 69630, 92866)):
    step = T // n
    pages = [{"i": i, "s": i * step, "e": min(T, (i + 1) * step)} for i in range(n)]
    pages[-1]["e"] = T
    gs, d = 0, []
    for i, sz in enumerate(docs):
        d.append({"idx": i, "path": f"d{i}.xhtml", "gs": gs, "size": sz, "page": 0})
        gs += sz
    return {"pages": pages, "src_total_bytes": T, "docs": d, "total_pages": n,
            "library_path": ""}


# ---- pct <-> 页号 ---------------------------------------------------------

def test_pct_to_page_is_containing_page():
    sc = _sc()
    T = sc["src_total_bytes"]
    for pct in (0.0, 0.004, 0.4896, 0.5, 0.999):
        g = int(pct * T)
        p = kpages.pages_read_for_pct(sc, pct)
        assert sc["pages"][p]["s"] <= g < sc["pages"][p]["e"], (pct, p)


def test_page_to_pct_is_page_start_bytes():
    sc = _sc()
    T = sc["src_total_bytes"]
    for p in (0, 1, 47, 48, 99):
        pct = kpages.pct_for_pages_read(sc, p)
        assert abs(pct - sc["pages"][p]["s"] / T) < 1e-12


def test_roundtrip_is_fixed_point():
    """pct → 页 → pct → 页 必须收敛到同一页 (不来回跳)。"""
    sc = _sc()
    for p in range(0, 100):
        pct = kpages.pct_for_pages_read(sc, p)
        assert kpages.pages_read_for_pct(sc, pct) == p, p


def test_roundtrip_error_within_one_page():
    sc = _sc()
    for p in range(0, 100):
        pct = kpages.pct_for_pages_read(sc, p)
        back = kpages.pages_read_pct_exact(sc, kpages.pages_read_for_pct(sc, pct))
        assert abs(back - pct) <= 1.0 / 100


def test_no_round_up_to_next_page():
    """老实现用 round(pct*N) 会把 48.96% 抬到第 49 页 (内容超前 1 页)。"""
    sc = _sc()
    assert kpages.pages_read_for_pct(sc, 0.4896) == 48
    assert round(0.4896 * 100) == 49          # 反例: 这就是被废掉的算法


def test_finished_maps_to_full_pages():
    sc = _sc()
    assert kpages.pages_read_for_pct(sc, 0.94, finished=True) == 100
    assert kpages.pct_for_pages_read(sc, 100) == 1.0
    # 没读完时永远落在 0..N-1, 不会假装 100%
    assert kpages.pages_read_for_pct(sc, 0.999) == 99


# ---- push 防回退闸 -------------------------------------------------------

def test_push_skips_regression():
    # 掌阅 48.96%, Kavita 页 48 起点 47.98% → 不许往回拨
    ok, why = push_decision(0.4896, False, 0.4798, False, 100)
    assert not ok and why.startswith("regress")


def test_push_allows_forward():
    ok, why = push_decision(0.20, False, 0.35, False, 100)
    assert ok and why == "forward"


def test_push_skips_noop_within_half_page():
    ok, why = push_decision(0.27185, False, 0.27167, False, 600)
    assert not ok and why == "noop"


def test_push_small_step_forward_still_pushed():
    # 前进正好一页 → 必须推
    ok, _ = push_decision(0.40, False, 0.41, False, 100)
    assert ok


def test_push_never_downgrades_finished():
    ok, why = push_decision(0.99, True, 0.95, False, 100)
    assert not ok and why == "zy-already-done"


def test_push_can_finish():
    ok, why = push_decision(0.99, False, 1.0, True, 100)
    assert ok and why == "finish"


def test_push_rewind_override():
    ok, _ = push_decision(0.50, False, 0.10, False, 100, allow_rewind=True)
    assert ok


# ---- totalCount 口径 (2026-10-24) ---------------------------------------

def test_catalog_count_matches_app_values():
    """目录章数要跟掌阅 App 记的 totalCount 一致 (apk: Book.K()).

    spine / NCX / app 记录 三者对照, 用真实 epub 验:
      狐 9/8/8 · 剑来 590/588/588 · 藤井 193/无NCX/192
    """
    from sync import zpct
    bridge = Path("/vol2/1001/zhangyue-bridge/books")
    cases = [("狐的故事.epub", 9, 8, 8),
             ("剑来（1-54册）完结精校版.epub", 590, 588, 588)]
    for name, spine, cat, app in cases:
        p = bridge / name
        if not p.is_file():
            continue
        g = zpct.load_geometry(p)
        assert len(g.sizes) == spine, (name, len(g.sizes))
        assert g.catalog == cat, (name, g.catalog)
        assert g.total_count == app, (name, g.total_count)


def test_catalog_count_falls_back_to_spine_minus_cover(tmp_path):
    """没有 NCX (epub3) 时: spine 剔掉封面/扉页文档。"""
    import zipfile
    from sync import zpct
    pth = tmp_path / "b.epub"
    opf = """<?xml version="1.0"?>
<package version="3.0" xmlns="http://www.idpf.org/2007/opf">
 <manifest>
  <item id="c" href="cover.xhtml" media-type="application/xhtml+xml"/>
  <item id="a" href="p1.xhtml" media-type="application/xhtml+xml"/>
  <item id="b" href="p2.xhtml" media-type="application/xhtml+xml"/>
 </manifest>
 <spine><itemref idref="c"/><itemref idref="a"/><itemref idref="b"/></spine>
</package>"""
    with zipfile.ZipFile(pth, "w") as z:
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/cover.xhtml", "<html><body>cover</body></html>")
        z.writestr("OEBPS/p1.xhtml", "<html><body>" + "甲" * 40 + "</body></html>")
        z.writestr("OEBPS/p2.xhtml", "<html><body>" + "乙" * 40 + "</body></html>")
    g = zpct.load_geometry(pth)
    assert g.catalog == 2, g.catalog
    assert g.total_count == 2, g.total_count


def test_total_count_prefers_app_value():
    """掌阅现有值自洽时原样保留 (App 自己写的最准)。"""
    from sync import sync as S
    from sync.mapping import BookLink

    class G:                                    # 假几何: catalog 589
        catalog = 589
        total_count = 589

    l = BookLink(kavita_series_id=27, zy_book_id="b", zy_book_name="剑来",
                 zy_anchor_chapter_id="154", kavita_chapter_count=1)
    assert S._total_count_for(l, G(), 588, 154) == 588      # 保留 App 的 588
    assert S._total_count_for(l, G(), None, 154) == 589     # 没值 → 目录章数
    # 卷01 被我们写坏成 1 (chapterId=22 装不下 1) → 不许保留
    assert S._total_count_for(l, G(), 1, 22) == 589
    # 完全没有几何 → 映射表旧值
    assert S._total_count_for(l, None, None, 0) == 1


def test_push_meta_only_when_pct_unchanged():
    """进度一致但 totalCount 不同: 允许写一次 (meta), 回退仍然挡住。

    2026-10-26 改: 以前"半页容差内"就允许 meta 写 —— 那等于允许小幅回拨
    (狐 50.33% 被写成 50.22%)。现在 meta 写要求 new_pct >= cur_pct。
    """
    ok, why = push_decision(0.11739, False, 0.11739, False, 195, meta_stale=True)
    assert ok and why == "meta"
    ok, why = push_decision(0.11739, False, 0.11739, False, 195)
    assert not ok and why == "noop"
    ok, why = push_decision(0.11795, False, 0.11739, False, 195, meta_stale=True)
    assert not ok, why          # 小幅落后也不写 (不许回拨)
    ok, why = push_decision(0.5, False, 0.1, False, 195, meta_stale=True)
    assert not ok and why.startswith("regress")


def test_best_percent_prefers_notebooks_when_further():
    """掌阅两套进度存储: getProgress (我们写) 与 getUserNotebooks (App 写)。

    2026-10-26 剑来实证: App 27.755% / getProgress 27.387% → 必须取前者,
    否则 pull 反复只看到 27.4%。
    """
    import sync.sync as S
    bid = "70776a64c32c1c065dab241f3e9f133a55c7d8b38c5c6fbc79271e87e00e9526"
    orig = S._zy_notebook_progress
    try:
        S._zy_notebook_progress = lambda force=False: {
            bid[:32]: (0.2775522, "158_0_0$##3-0-158-35920-0##$", 1792979258634)}
        pct, ok, src = S._best_zy_percent(bid, 0.27387166, True)
        assert ok and src == "notebooks" and abs(pct - 0.2775522) < 1e-9
        # App 那份落后 → 仍用 getProgress
        S._zy_notebook_progress = lambda force=False: {
            bid[:32]: (0.10, "10_0_0$##3-0-10-0-0##$", 1)}
        pct, ok, src = S._best_zy_percent(bid, 0.27387166, True)
        assert src == "progress" and abs(pct - 0.27387166) < 1e-9
        # 本地拿的 readPercent 解析失败 → App 那份兜底
        S._zy_notebook_progress = lambda force=False: {
            bid[:32]: (0.05, "5_0_0$##3-0-5-0-0##$", 1)}
        pct, ok, src = S._best_zy_percent(bid, 0.0, False)
        assert ok and src == "notebooks" and abs(pct - 0.05) < 1e-9
        # 两套都没有这本书 → 原样返回
        S._zy_notebook_progress = lambda force=False: {}
        pct, ok, src = S._best_zy_percent(bid, 0.2, True)
        assert src == "progress" and abs(pct - 0.2) < 1e-9
    finally:
        S._zy_notebook_progress = orig


# ---- edition 自检 -------------------------------------------------------

def test_validate_catches_broken_ncx(tmp_path):
    import zipfile
    p = tmp_path / "bad.epub"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("OEBPS/content.opf", """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf"><manifest>
<item id="p1" href="kp/p0001.xhtml" media-type="application/xhtml+xml"/>
</manifest><spine><itemref idref="p1"/></spine></package>""")
        z.writestr("OEBPS/kp/p0001.xhtml", "<html><body><p>x</p></body></html>")
        z.writestr("OEBPS/toc.ncx", """<?xml version="1.0"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap><navPoint id="n1">
<navLabel><text>c1</text></navLabel><content src="p0001.xhtml"/></navPoint></navMap></ncx>""")
    probs = kpages.validate_edition(p)
    assert any("toc.ncx" in x and "p0001.xhtml" in x for x in probs), probs


def test_validate_passes_good_epub(tmp_path):
    import zipfile
    p = tmp_path / "good.epub"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("OEBPS/content.opf", """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf"><manifest>
<item id="p1" href="kp/p0001.xhtml" media-type="application/xhtml+xml"/>
</manifest><spine><itemref idref="p1"/></spine></package>""")
        z.writestr("OEBPS/kp/p0001.xhtml", "<html><body><p>x</p></body></html>")
        z.writestr("OEBPS/toc.ncx", """<?xml version="1.0"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap><navPoint id="n1">
<navLabel><text>c1</text></navLabel><content src="kp/p0001.xhtml"/></navPoint></navMap></ncx>""")
    assert kpages.validate_edition(p) == []




# ---- 卷级映射 (2026-10-24) ------------------------------------------------

def _fresh_mapping_db():
    """把 mapping 指到一个临时 sqlite, 返回 (mapping 模块, 还原函数)。"""
    import tempfile
    from sync import mapping
    old = mapping.STATE_DB
    path = Path(tempfile.mkdtemp()) / "sync.sqlite"
    mapping.STATE_DB = str(path)
    return mapping, (lambda: setattr(mapping, "STATE_DB", old)), path


def test_volume_level_mapping_resolve():
    mapping, restore, _ = _fresh_mapping_db()
    try:
        mapping.link(29, "bid-vol1", "卷01", kavita_chapter_id=36, kavita_chapter_count=1)
        mapping.link(29, "bid-vol2", "卷02", kavita_chapter_id=37, kavita_chapter_count=1)
        assert mapping.get(29, 36).zy_book_id == "bid-vol1"
        assert mapping.get(29, 37).zy_book_id == "bid-vol2"
        assert mapping.get(29, mapping.SERIES_LEVEL) is None
        # 卷级缺失、也没有系列级 → 不能兜底到别的卷 (否则进度串书)
        assert mapping.resolve(29, 35) is None
        # 系列级映射作为整本兜底
        mapping.link(30, "bid-x", "单本")
        assert mapping.resolve(30, 5).zy_book_id == "bid-x"
        # 有卷级就用卷级, 其它章仍落系列级
        mapping.link(30, "bid-y", "卷5", kavita_chapter_id=5)
        assert mapping.resolve(30, 5).zy_book_id == "bid-y"
        assert mapping.resolve(30, 6).zy_book_id == "bid-x"
        assert len(mapping.links_for_series(29)) == 2
    finally:
        restore()


def test_migrate_v1_single_pk_becomes_series_level():
    """老库 (单列主键) 升级后, 老行必须变 chapter_id=-1, 多卷新行不冲突。"""
    import sqlite3
    mapping, restore, path = _fresh_mapping_db()
    try:
        c = sqlite3.connect(str(path))
        c.executescript("""
            CREATE TABLE book_link (
                kavita_series_id INTEGER PRIMARY KEY,
                zy_book_id TEXT NOT NULL,
                zy_book_name TEXT NOT NULL,
                zy_anchor_chapter_id TEXT,
                kavita_chapter_count INTEGER,
                epub_path TEXT,
                note TEXT,
                created_utc TEXT DEFAULT (datetime('now')),
                updated_utc TEXT DEFAULT (datetime('now'))
            );
            INSERT INTO book_link (kavita_series_id, zy_book_id, zy_book_name,
                                   zy_anchor_chapter_id, kavita_chapter_count, note)
            VALUES (26, 'bid-fox', '狐的故事', '5', 1, 'old row');
        """)
        c.commit(); c.close()
        links = mapping.all_links()                     # 触发 _migrate
        assert len(links) == 1, links
        assert links[0].kavita_chapter_id == mapping.SERIES_LEVEL
        assert links[0].zy_book_id == "bid-fox"
        mapping.link(29, "bid-vol1", "卷01", kavita_chapter_id=36)
        assert len(mapping.all_links()) == 2
    finally:
        restore()


# ---- 掌阅 bookId 双 key / chapterId 口径 (2026-10-26) --------------------

def test_app_book_keys_writes_both_local_keys():
    """本地导入书的 App key = 32 hex 大写 (文件 MD5), 64 hex 只是云书架 id。

    2026-10-26 实测: 只写 64 hex 时 App (读 32 hex) 永远看不到 → "push 了没变"。
    """
    from sync.sync import app_book_keys
    bid = "70776a64c32c1c065dab241f3e9f133a55c7d8b38c5c6fbc79271e87e00e9526"
    keys = app_book_keys(bid)
    assert keys == ["70776A64C32C1C065DAB241F3E9F133A", bid], keys
    # 已经是 32 hex / 数字 id / 空 → 不加派生 key
    assert app_book_keys("70776A64C32C1C065DAB241F3E9F133A") == \
        ["70776A64C32C1C065DAB241F3E9F133A"]
    assert app_book_keys("1379") == ["1379"]
    assert app_book_keys("") == []


def test_catalog_chapter_excludes_front_matter():
    """App 的 chapterId 是**目录项下标**, 比 spine-1 少 front 篇。

    实证: 剑来 spine 590 / catalog 588 → App ch=157 而位置 pre=159;
    藤井卷03 spine 193 / catalog 192 → App ch=189 而 pre=190。
    """
    from sync.sync import _catalog_chapter

    class G:
        catalog = 588
        sizes = [1000] * 590
    assert _catalog_chapter(G(), 158) == 157      # 剑来型 (front=2)
    assert _catalog_chapter(G(), 0) == 0

    class G1:
        catalog = 192
        sizes = [1000] * 193
    assert _catalog_chapter(G1(), 189) == 189     # 藤井卷03 型 (front=1)

    class G0:                                     # 没 NCX / catalog 未知 → 原样
        catalog = 0
        sizes = [1000] * 5
    assert _catalog_chapter(G0(), 3) == 3
    assert _catalog_chapter(None, 3) == 3


def test_push_meta_never_rewinds():
    """修元信息也不许把进度往回拨 (半页容差内也不例外)。"""
    ok, why = push_decision(0.5033, False, 0.5022, False, 100, meta_stale=True)
    assert not ok, why
    ok, why = push_decision(0.27999589, False, 0.27999589, False, 3000, meta_stale=True)
    assert ok and why == "meta"
    ok, why = push_decision(0.2795, False, 0.2800, False, 3000, meta_stale=True)
    assert ok and why == "forward"


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            import tempfile
            import types
            tmp = Path(tempfile.mkdtemp())
            try:
                if fn.__code__.co_argcount:
                    fn(tmp)
                else:
                    fn()
                print("PASS", name)
            except AssertionError as e:
                fails += 1
                print("FAIL", name, e)
    raise SystemExit(1 if fails else 0)
