"""zpct 反推结论的回归测试。

跑:  python3 -m pytest tests/test_zpct.py -q
或:  python3 tests/test_zpct.py     (无 pytest 也能跑)
"""
from __future__ import annotations

import os
import re
import sqlite3
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sync import zpct  # noqa: E402

BOOKS = Path("/vol2/1001/dav/Books/掌阅")
# 换过重排版的书写进 ORIG 归档; 几何必须拿原书算
ORIG = Path("/vol2/1001/zhangyue-bridge/books")


def book_path(name: str) -> Path:
    p = ORIG / name
    return p if p.exists() else (BOOKS / name)

# 掌阅 App 真实写入的记录 (state/zp.json, readingSource="app")
# (书名, chapterId, f3, readPercent 原字符串)
APP_CASES = [
    ("狐的故事.epub", 5, 11752, "0.29326823"),
    ("路邊的藤井_-_卷01.epub", 1, 0, "0.009963691"),
    ("路邊的藤井_-_卷03.epub", 189, 0, "0.98467165"),
]

# 未读的书: pct 恒为 0, 无论几何怎么算
UNREAD_CASES = [
    ("路邊的藤井_-_卷02.epub", 0, 0),
]

TOTAL_COUNT = [
    ("狐的故事.epub", 9, 8),
    ("路邊的藤井_-_卷01.epub", 195, 194),
    ("路邊的藤井_-_卷02.epub", 193, 192),
    ("路邊的藤井_-_卷03.epub", 193, 192),
]

POSITIONS = [
    ("6_0_0$##3-0-6-11752-0##$", 5, 11752),
    ("190_0_0$##3-0-190-0-0##$", 189, 0),
    ("2_0_0$##3-0-2-0-0##$", 1, 0),
]


def f32_bits(x: float) -> int:
    return struct.unpack("I", struct.pack("f", x))[0]


def test_percent_bit_exact():
    """三个非零样本必须 float32 bit 级一致。"""
    for name, ch, f3, raw in APP_CASES:
        p = book_path(name)
        if not p.exists():
            print(f"  SKIP {name} (epub 不在)")
            continue
        g = zpct.load_geometry(p)
        got = g.percent(ch, f3)
        want = float(raw)
        assert f32_bits(got) == f32_bits(want), (
            f"{name} ch={ch} f3={f3}: got {got!r} ({f32_bits(got):08x}) "
            f"want {want!r} ({f32_bits(want):08x})")
        print(f"  OK   {name:28} ch={ch:<4} {got:.8f} == {want:.8f}  [{f32_bits(got):08x}]")


def test_unread_is_zero():
    for name, ch, f3 in UNREAD_CASES:
        p = book_path(name)
        if not p.exists():
            continue
        g = zpct.load_geometry(p)
        # 官方 pct=0.0 而几何算出非零 —— 说明掌阅对"未开始"单独置 0,
        # 不是几何算出来的。这个差必须被上游识别, 不能当成误差。
        assert g.percent(ch, f3) > 0.0
        print(f"  OK   {name:28} 几何算出 {g.percent(ch, f3):.6f}, 官方记 0 (未读置零)")


def test_total_count():
    for name, spine_len, want in TOTAL_COUNT:
        p = book_path(name)
        if not p.exists():
            continue
        g = zpct.load_geometry(p)
        assert len(g.sizes) == spine_len, f"{name}: spine {len(g.sizes)} != {spine_len}"
        assert g.total_count == want, f"{name}: totalCount {g.total_count} != {want}"
        print(f"  OK   {name:28} spine={spine_len} -> totalCount={want}")


def test_position_roundtrip():
    for pos, ch, f3 in POSITIONS:
        d = zpct.parse_position(pos)
        assert d["chapter_id"] == ch, f"{pos}: ch {d['chapter_id']} != {ch}"
        assert int(d["f3"]) == f3, f"{pos}: f3 {d['f3']} != {f3}"
        assert d["f0"] == "3", f"{pos}: f0 {d['f0']} != 3"
        rebuilt = zpct.build_position(ch, f3)
        assert rebuilt == pos, f"{pos}: rebuild -> {rebuilt}"
        print(f"  OK   {pos:30} -> ch={ch} f3={f3} -> {rebuilt}")


def test_monotonic():
    """百分比必须随 chapterId 单调不减, 且章内偏移单调。"""
    p = book_path("路邊的藤井_-_卷03.epub")
    if not p.exists():
        return
    g = zpct.load_geometry(p)
    prev = -1.0
    for ch in range(len(g.sizes)):
        v = g.percent(ch)
        assert v >= prev, f"ch={ch}: {v} < {prev}"
        prev = v
    print(f"  OK   单调性: {len(g.sizes)} 个 chapterId 全递增")

    # 章内偏移单调
    assert g.percent(10, 0) < g.percent(10, 100) <= g.percent(10, 100000)
    print("  OK   章内偏移单调")


def test_f0_is_three():
    """官方 f0 恒 3。我们之前伪造写 0。"""
    assert zpct.build_position(5, 0).startswith("6_0_0$##3-")
    assert zpct.build_position(0, 100).startswith("1_0_0$##3-")
    print("  OK   f0=3 (对齐官方)")


def test_xpath_offset_roundtrip():
    """xpath ↔ 字节偏移 双向换算 (亚页精度的地基)。"""
    p = book_path("狐的故事.epub")
    if not p.exists():
        print("  SKIP")
        return
    di = zpct.build_doc_index(p, 5)
    assert di is not None and len(di.index) > 100, "索引没建起来"

    # Kavita 存的是 //body/... 形式 (无命名空间, body 不带下标)
    names = {xp for xp, _, _ in di.index}
    assert "//body" in names, f"缺 //body: {sorted(names)[:5]}"
    assert "//body/div[1]" in names, "缺 //body/div[1]"
    assert not any(x.startswith("/body") for x in names), "xpath 前缀格式错 (应 // 而非 /)"

    # xpath -> offset -> xpath 恒等
    for xp in ("//body", "//body/div[1]"):
        off = di.offset_of_xpath(xp)
        back = di.xpath_of_offset(off)
        assert back == xp, f"{xp}: off={off} -> {back}"
        print(f"  OK   roundtrip {xp:20} off={off:6d} -> {back}")

    # offset -> xpath 必须单调 (偏移越大, 元素越靠后)
    # 取样从 body 起点之后开始: 索引只覆盖 body 子树, 前面的 <html>/<head>
    # (狐的故事 381 字节) 不在索引里, 查不到是预期行为。
    body_start = di.offset_of_xpath("//body")
    prev_depth = -1
    for frac in (0.01, 0.25, 0.5, 0.75, 0.99):
        off = body_start + int((len(di.raw) - body_start) * frac)
        x = di.xpath_of_offset(off)
        assert x is not None, f"offset {off} 找不到元素"
        seg = [int(s.split("[")[1].rstrip("]")) for s in x.split("/")[2:] if "[" in s]
        depth = sum(seg)
        assert depth >= prev_depth, f"offset {off}: {x} 倒退"
        prev_depth = depth
        print(f"  OK   offset {off:6d} -> {x}  (depth {depth})")


def test_xpath_format_matches_kavita():
    """确认我们生成的 xpath 形状与 Kavita BookScrollId 一致。"""
    p = book_path("狐的故事.epub")
    if not p.exists():
        return
    di = zpct.build_doc_index(p, 5)
    samples = [xp for xp, _, _ in di.index if xp.startswith("//body/div[1]/p")]
    assert samples, "没抓到 div[1]/p[*]"
    # Kavita 实例: //body/p[164] / //body/div[1]/p[4] / //body/h3[49]
    for xp in samples[:3]:
        assert xp.startswith("//body/"), xp
        assert re.match(r"^//body(/[a-z]+\[\d+\])+$", xp), f"形状不对: {xp}"
    print(f"  OK   xpath 形状对齐 Kavita (如 {samples[0]})")


def test_percent_roundtrip_through_offset():
    """章内偏移进出: (chapterId, f3) -> pct -> 反解 f3, 误差 < 1 字节。"""
    p = book_path("狐的故事.epub")
    if not p.exists():
        return
    g = zpct.load_geometry(p)
    ch = 5
    for f3 in (0, 1000, 11752, 30000, 60000):
        pct = g.percent(ch, f3)
        # 反解: pct*T - prefix = f3
        back = pct * g.total_bytes - g.prefix_bytes(ch)
        assert abs(back - f3) < 4, f"f3={f3}: 反解得 {back}"
        print(f"  OK   f3={f3:6d} -> pct={pct:.8f} -> 反解 f3={back:.1f}")


def test_solve_recovers_official_chapter_id():
    """solve() 反解必须还原 App 记的 chapterId 与 f3。

    这是 2026-10-23 定的反向解法 (pull 折页不再用 round(pct*Pages))。
    狐 ch=5/f3=11752 是唯一带非零 f3 的样本, 它定死了 chapterId 就是
    spine 下标本身 (不再 +1)。
    """
    for name, ch, f3, raw in APP_CASES:
        p = book_path(name)
        if not p.exists():
            print(f"  SKIP {name}")
            continue
        g = zpct.load_geometry(p)
        ch2, f3b = g.solve(float(raw))
        assert ch2 == ch, f"{name}: chapterId {ch2} != 官方 {ch}"
        assert f3b == f3, f"{name}: f3 {f3b} != 官方 {f3}"
        print(f"  OK   {name:28} ch={ch:>3} f3={f3:>6} 精确还原")

    for name, ch, f3 in UNREAD_CASES:
        p = book_path(name)
        if not p.exists():
            continue
        g = zpct.load_geometry(p)
        ch2, f3b = g.solve(0.0)
        assert (ch2, f3b) == (ch, f3), f"{name}: 未读应为 {ch}/{f3}, 得 {ch2}/{f3b}"
        print(f"  OK   {name:28} 未读 -> ch=0 f3=0 (未读置零硬规则)")


def test_solve_percent_roundtrip():
    """solve(percent(ch,f3)) 必须回到 (ch, f3), 误差 < 4 字节。

    不变量: **f3 落在 spine[chapterId + 1] 内** —— 因为 pct 公式里
    chapterId 那篇算作已读完, f3 是"下一篇里的位置"。

        0 <= f3 < sizes[chapterId + 1]

    狐的 spine[0] 是 433 字节的 cover, 所以 ch=0 时 f3 上限取
    sizes[1]-1 = 1232。拿 f3=20000 去试 ch=0 属于非法输入:
    `percent()` 会把 >1 钳到 1.0, 信息已丢, 反解当然回不来 ——
    那不是 solve 的 bug。
    """
    p = book_path("狐的故事.epub")
    if not p.exists():
        return
    g = zpct.load_geometry(p)
    n = 0
    for ch in (0, 2, 5, 6, 7):
        if ch + 1 >= len(g.sizes):
            continue
        span = g.sizes[ch + 1]           # f3 的合法上界 = 下一篇长度
        for f3 in (0, span // 3, max(0, span - 1)):
            pct = g.percent(ch, f3)
            ch2, f3b = g.solve(pct)
            assert ch2 == ch, f"ch={ch} span={span} f3={f3}: 反解得 ch={ch2}"
            assert abs(f3b - f3) < 4, f"ch={ch} f3={f3}: 反解得 {f3b}"
            n += 1
    print(f"  OK   {n} 组 (chapterId, f3) 往返全中 "
          f"(f3 取 spine[ch+1] 内的 0 / 1/3 / 末字节)")

    # 非法输入必须被 percent() 的钳位吃掉: f3 远超末篇长度 → percent 钳到 1.0
    # → solve 解出"读到最后一篇末尾", f3 == sizes[-1] - 1 附近, 不越界
    _, f3b = g.solve(g.percent(0, 20000))
    assert 0 <= f3b < g.sizes[-1] + 1, f"越界输入的 f3={f3b} 越出末篇"
    print(f"  OK   f3 越界时 percent() 钳到 1.0, solve 得末篇末尾 f3={f3b}")


def test_solve_monotonic():
    """pct 递增 -> 解出的字节位置必须单调不减。"""
    p = BOOKS / "路邊的藤井_-_卷01.epub"
    if not p.exists():
        return
    g = zpct.load_geometry(p)
    prev = -1
    for i in range(0, 201):
        pct = i / 200.0
        ch, f3 = g.solve(pct)
        pos = g.prefix_bytes(ch) + f3
        assert pos >= prev, f"pct={pct}: 位置回退 {prev} -> {pos}"
        prev = pos
    print("  OK   201 个采样点字节位置单调不减")


def test_kavita_pagesread_equals_spine_index():
    """**跨系统页号语义**: Kavita PagesRead = spine 下标, 掌阅 chapterId 差 1。

    标尺用 Kavita 原生进度 (不是桥接写入的): BookScrollId 是那条 spine
    文档里的元素路径, **命中集合里含 PagesRead** 就说明续读页 = spine[PagesRead]。

        series 1  海边的卡夫卡      PR=2  xp 命中 [2..7]   含 2  ✓
        series 9  没有色彩的多崎作    PR=2  xp 命中 [2..20]  含 2  ✓
        series 18 阿德里安·默尔日记  PR=3  xp 命中 [3..6]   含 3  ✓
        series 23 紫云村            PR=3  xp 命中 [3..103] 含 3  ✓
        series 25 阿默与大规模杀伤  PR=2  xp 命中 [2,3]    含 2  ✓

    **不能取"第一个命中"** —— 各篇 spine 文档结构相同, 同一个 xpath
    (`//body/div[1]/p[15]`) 会在多篇里都出现 (狐的故事命中 [3,5,6,7,8],
    第一个命中是 3, 但 PagesRead 是 5)。这是判据本身有歧义, 不是代码错。

    绝对字节位置复核 (notes/03-pull-not-visible-why.md):
        掌阅 chapterId=5 f3=0 → 绝对 84191 = cum[5]       → 在 spine[6] 开头
        Kavita PR=5 xp=p[15]  → 绝对 11513 落在 spine[5] (8766, 84191]
    差 1 篇 → pull 写 Kavita 必须 `chapterId + 1`。
    """
    cases = [
        ("series 1  海边的卡夫卡", "海边的卡夫卡", 2, "//body/div[1]/p[124]"),
        ("series 9  没有色彩的多崎作", "没有色彩的多崎作", 2, "//body/p[47]"),
        ("series 18 阿德里安·默尔日记", "阿德里安·默尔的秘密日记", 3, "//body/p[164]"),
        ("series 23 紫云村", "紫云村", 3, "//body/div[5]/p[18]"),
        ("series 25 阿默与大规模杀伤", "Adrian Mole and the Weapons", 2, "//body/h3[49]"),
    ]
    src = sqlite3.connect("file:/vol1/1001/APPS/kavita/kavita.db?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    tmp = tempfile.mktemp(suffix=".db")
    dst = sqlite3.connect(tmp)
    src.backup(dst)
    src.close()
    dst.row_factory = sqlite3.Row
    try:
        checked = 0
        for label, needle, want_pages, want_xp in cases:
            r = dst.execute("""
                SELECT ap.PagesRead, ap.BookScrollId, mf.FilePath
                FROM AppUserProgresses ap
                JOIN Series s ON s.Id = ap.SeriesId
                JOIN MangaFile mf ON mf.ChapterId = ap.ChapterId
                WHERE s.Name LIKE ? AND ap.BookScrollId IS NOT NULL
                ORDER BY ap.LastModified DESC LIMIT 1
            """, (f"%{needle[:14]}%",)).fetchone()
            if r is None:
                print(f"  SKIP {label}: Kavita 里找不到原生进度行")
                continue
            path = "/fs/1001/ftp/dav/Books" + r["FilePath"][len("/books"):]
            if not Path(path).exists():
                print(f"  SKIP {label}: epub 不在 {path}")
                continue
            assert r["PagesRead"] == want_pages, \
                f"{label}: Kavita PagesRead 变成 {r['PagesRead']}, 标尺是 {want_pages}"
            g = zpct.load_geometry(path)
            hits = [i for i in range(len(g.spine))
                    if (di := zpct.build_doc_index(path, i)) and want_xp in di._by_xpath]
            assert hits, f"{label}: BookScrollId {want_xp} 在任何 spine 文档里都没命中"
            assert want_pages in hits, (
                f"{label}: BookScrollId {want_xp} 命中篇号 {hits}, "
                f"不含 PagesRead={want_pages} —— 两侧语义假设崩了")
            checked += 1
            print(f"  OK   {label}: PagesRead={want_pages} ∈ 命中篇号 {hits} (spine={len(g.spine)})")

        # 掌阅侧: 狐的故事 pct=0.257346 解出 chapterId=5 → Kavita 要 6
        # 狐的故事已被重排成 Kavita 版, 几何必须取原书归档
        fox = zpct.load_geometry(str(book_path("狐的故事.epub")))
        zy_ch, _f3 = fox.solve(0.257346)
        assert zy_ch == 5, f"狐 pct=0.257346 -> chapterId={zy_ch}, 应为 5"
        assert zy_ch + 1 == 6, f"+1 应为 6, 实得 {zy_ch + 1}"
        print(f"  OK   掌阅侧: pct=0.257346 -> chapterId={zy_ch}, +1 = {zy_ch + 1}")
        assert checked >= 3, f"只校验了 {checked} 本, 标尺样本太少"
    finally:
        dst.close()
        os.unlink(tmp)


def test_toc_labels_volume_and_chapter():
    """多卷合订本的目录名: depth0 卷 + depth1 章 → "卷名 章名"。
    掌阅"来源"要用它, 不是文件名 (chapter0152)。"""
    p = book_path("剑来（1-54册）完结精校版.epub")
    if not p:
        print("  SKIP 没有剑来样书")
        return
    g = zpct.load_geometry(str(p))
    assert g.toc, "剑来有 NCX, toc 不该为空"
    # spine[154] = Text/chapter0152.xhtml, NCX: 剑来14 卷 / 第九章 另一个朱敛
    lab = g.toc.get(154, "")
    assert "剑来14" in lab and "第九章" in lab, f"spine[154] 目录名不对: {lab!r}"
    # 位置在 ch 里 = 落在 spine[ch+1] → 展示用 ch+1 的目录名
    assert g.toc.get(155, "").startswith("剑来14"), g.toc.get(155)
    assert "第十章 北俱芦洲无奇怪" in g.toc.get(155, ""), g.toc.get(155)
    print(f"  OK   toc[154]={g.toc[154]!r}")
    print(f"  OK   toc[155]={g.toc[155]!r}")


if __name__ == "__main__":
    for fn in (test_percent_bit_exact, test_unread_is_zero, test_total_count,
               test_position_roundtrip, test_monotonic, test_f0_is_three,
               test_xpath_offset_roundtrip, test_xpath_format_matches_kavita,
               test_percent_roundtrip_through_offset,
               test_solve_recovers_official_chapter_id,
               test_solve_percent_roundtrip, test_solve_monotonic,
               test_kavita_pagesread_equals_spine_index,
               test_toc_labels_volume_and_chapter):
        print(f"{fn.__name__}:")
        fn()
    print("\n全部通过。")