"""掌阅 readPercent 算法 (2026-10-22 反推, 误差 float32 量级)

    readPercent = (Σ bytes(spine[0..chapterId]) + offset) / Σ bytes(spine)
    spine 下标 = chapterId + 1, 0 号 cover 计入分母
    offset     = readPosition 的 f3 (章内字节偏移)

详见 notes/01-readpercent-formula.md 的证据表。
"""
from __future__ import annotations

import re
import bisect
import struct
import zipfile
from dataclasses import dataclass
from pathlib import Path

_OPF = re.compile(r"\.opf$", re.I)
_ITEM = re.compile(r"<item\b[^>]*>")
_ID = re.compile(r'\bid="([^"]+)"')
_HREF = re.compile(r'\bhref="([^"]+)"')
_SPINE = re.compile(r"<spine\b[^>]*>(.*?)</spine>", re.S)
_IDREF = re.compile(r'idref="([^"]+)"')
_POS = re.compile(r"^(.*?)\$##(.*?)##\$$")
_MEDIA = re.compile(r'\bmedia-type="([^"]+)"')
_PROPS = re.compile(r'\bproperties="([^"]*)"')
_NAVMAP = re.compile(r"<navMap\b.*?</navMap>", re.S)
_CONTENT_SRC = re.compile(r'<content\b[^>]*\bsrc="([^"]+)"')
_COVER_NAME = re.compile(r"(?:^|/)(?:[^/]*cover[^/]*|titlepage[^/]*)\.x?html?$", re.I)

# 掌阅官方写死 f0=3; 我们伪造的写 0, app 可能不认
F0_OFFICIAL = "3"
# readPosition 的 pre 段: "<chapterId+1>_0_0"
PRE_TAIL = "_0_0"


def to_float32(v: float) -> float:
    """官方存的是 float32, 对齐它避免我们写出的值差最后一位。"""
    return struct.unpack("f", struct.pack("f", v))[0]


@dataclass
class BookGeometry:
    """一本 epub 的 spine 几何 — 算百分比和总数都用它。"""
    spine: list[str]          # spine 顺序的 zip 内路径
    sizes: list[int]          # 对应字节数
    name: str = ""
    catalog: int = 0          # 引擎目录章数 (catalogCount); 0 = 未知, 退回 spine-1

    @property
    def total_bytes(self) -> int:
        return sum(self.sizes)

    @property
    def total_count(self) -> int:
        """掌阅 totalCount = 引擎的目录章数 (catalogCount)。

        **不是** 简单的 spine-1。反推 (2026-10-24) 依据:
          Book.K() → core.getCatalogCount() → 写进 readProcess.totalCount
          (kd/h.java:197 `int K = bVar.K()`, kd/g.java:60)
        目录列表才是引擎的"章"; 封面/独立目录页不进目录。

        实测 4 本:
          狐的故事    spine 9   NCX navPoint 8   → app 记 8
          剑来        spine 590 NCX navPoint 588 → app 记 588  (spine-1=589 差 1)
          藤井 卷02/03 spine 193 无 NCX → app 记 192 (只剩封面被剔)
        见 catalog_count() 的判定顺序。
        """
        if self.catalog > 0:
            return self.catalog
        return max(0, len(self.sizes) - 1)

    def prefix_bytes(self, chapter_id: int) -> int:
        """已读章累计字节 = Σ spine[0 .. chapterId]。"""
        if chapter_id < 0:
            return 0
        return sum(self.sizes[:chapter_id + 1])

    def percent(self, chapter_id: int, offset: int = 0) -> float:
        """还原 readPercent。offset 用 readPosition 的 f3。"""
        t = self.total_bytes
        if t <= 0:
            return 0.0
        v = (self.prefix_bytes(chapter_id) + max(0, offset)) / t
        v = max(0.0, min(1.0, v))
        return to_float32(v)

    def spine_index(self, chapter_id: int) -> int:
        """chapterId -> spine 下标。"""
        return chapter_id + 1

    def chapter_at(self, spine_index: int) -> int:
        """spine 下标 -> chapterId (反向)。"""
        return spine_index - 1

    def locate(self, byte_pos: float) -> tuple[int, int]:
        """全书绝对字节位置 → (chapterId, f3)。`percent()` 的正向逆运算。

        chapterId = "最后一篇读完的文档" = spine 下标 - 1, f3 = 下一篇里的偏移。
        位置落在 spine[0] (封面) 里时只能给 (0, 0) —— 掌阅没有"负一章"。
        """
        total = self.total_bytes
        if total <= 0:
            return 0, 0
        g = max(0.0, min(float(byte_pos), float(total)))
        cum = 0
        for i, size in enumerate(self.sizes):
            if g <= cum + size:
                if i <= 0:
                    return 0, 0
                f3 = int(round(g - cum))
                return i - 1, max(0, min(f3, size - 1))
            cum += size
        return max(0, len(self.sizes) - 2), max(0, self.sizes[-1] - 1)

    def solve(self, pct01: float) -> tuple[int, int]:
        """readPercent -> (chapterId, f3 章内字节偏移)。**`percent()` 的逆**。

        官方语义 (2026-10-23 用 4 本 App 真实记录钉死; 狐 ch=5/f3=11752
        是唯一带非零 f3 的样本, 整条链靠它定死):

            pct   = ( Σ bytes(spine[0 .. chapterId]) + f3 ) / Σ bytes(spine)
            f3    = 落在 spine[chapterId + 1] 内的字节偏移, 即
                    0 <= f3 < sizes[chapterId + 1]

        chapterId 是"最后一篇读完的文档", f3 是"下一篇里的位置"。
        狐: ch=5 → cum[5]=84191, f3=11752 → 95943, 落在 spine[6] 区间
        (84191 .. 164655) 的偏移 11752 处, pct bit 级吻合 0.29326823。

        **k 的判定用严格不等 `cum[k] > target`** —— 即 target 落在
        (cum[k-1], cum[k]] 这个"半开"区间里时 k 就是 spine 下标。
        f3 = target - cum[k-1], chapterId = k - 1。
        取严格大于是因为 f3 必须严格小于本章长度; f3 == sizes[chapterId+1]
        (读完但还没记录) 时掌阅会写 chapterId+1/f3=0, 不存在半字节歧义。

        pct 是 float32 (24 位尾数), 反解回字节误差上界 `total * 2**-23`
        (狐 total=327151 → 0.039 字节, 藤03 total=105491 → 0.013,
        实测 0.003 —— 在界内)。所以比较时给这个相对容差。

        pct <= 0 走"未读置零"硬规则: 藤02 官方记 ch=0/f3=0/pct=0.0, 而几何
        算出 0.0049 —— 未开始的书 pct 恒 0, 那不是几何误差。
        """
        total = self.total_bytes
        if total <= 0 or pct01 <= 0.0:
            return 0, 0
        target = min(1.0, pct01) * total
        eps = max(1e-9, total * 2 ** -23)

        cum = []
        running = 0.0
        for size in self.sizes:
            running += size
            cum.append(running)

        # 最小 k 使 cum[k] > target + eps  →  target 在 (cum[k-1], cum[k]]
        k = bisect.bisect_right(cum, target + eps)
        k = max(1, min(k, len(cum)))
        offset = target - cum[k - 1]
        return k - 1, int(round(max(0.0, offset)))

    def bytes_per_chapter(self) -> list[int]:
        return list(self.sizes)


def _catalog_from_ncx(z: zipfile.ZipFile, opf: str, manifest: list[tuple[str, str, str]]) -> int:
    """epub2: 目录章数 = NCX navMap 里 content 条数 (按顺序去重)。

    manifest 是 (id_href, media_type, properties) 三元组列表。
    """
    from posixpath import dirname, join, normpath
    base = dirname(opf)
    ncx = next((h for (h, mt, _p) in manifest
                if mt == "application/x-dtbncx+xml" or h.lower().endswith(".ncx")), "")
    if not ncx:
        return 0
    try:
        raw = z.read(normpath(join(base, ncx))).decode("utf-8", "replace")
    except KeyError:
        return 0
    m = _NAVMAP.search(raw)
    body = m.group(0) if m else raw
    seen: list[str] = []
    for src in _CONTENT_SRC.findall(body):
        if src not in seen:
            seen.append(src)
    return len(seen)


def _catalog_from_spine(order: list[str]) -> int:
    """没有 NCX 时 (epub3 常见): spine 里剔掉封面/扉页文档。

    藤井 卷02/03: spine 193 (封面 + 191 页 + theend) → app 记 192。
    """
    if not order:
        return 0
    idx = 0
    while idx < min(3, len(order)) and _COVER_NAME.search(order[idx]):
        idx += 1
    if idx == 0:
        return 0            # 头几篇不像封面 → 不做推测, 让调用方退回 spine-1
    return max(0, len(order) - idx)


def catalog_count(epub_path: str | Path) -> int:
    """掌阅引擎的目录章数 (catalogCount = readProcess.totalCount)。

    判定顺序 (2026-10-24 反推, 见 notes/04-totalcount-semantics.md):
      1. 有 NCX → navMap 的 content 条数 (去重)
      2. 没有 NCX → spine 去掉封面/扉页文档
      3. 都不成立 → 0 (调用方退回 spine-1)
    """
    p = Path(epub_path)
    try:
        with zipfile.ZipFile(p) as z:
            opf = next((n for n in z.namelist() if _OPF.search(n)), None)
            if not opf:
                return 0
            raw = z.read(opf).decode("utf-8", "replace")
            base = opf.rsplit("/", 1)[0] + "/" if "/" in opf else ""
            manifest: list[tuple[str, str, str]] = []
            items: dict[str, str] = {}
            for m in _ITEM.finditer(raw):
                tag = m.group(0)
                i, h = _ID.search(tag), _HREF.search(tag)
                if not (i and h):
                    continue
                mt = _MEDIA.search(tag)
                pr = _PROPS.search(tag)
                manifest.append((h.group(1), mt.group(1) if mt else "",
                                 pr.group(1) if pr else ""))
                items[i.group(1)] = base + h.group(1)
            sm = _SPINE.search(raw)
            order = ([items[r] for r in _IDREF.findall(sm.group(1)) if r in items]
                     if sm else [])
            n = _catalog_from_ncx(z, opf, manifest) or _catalog_from_spine(order)
    except (OSError, ValueError, zipfile.BadZipFile):
        return 0
    if order and n > len(order):        # 目录条数超过 spine 显然不可信
        return 0
    return n


def load_geometry(epub_path: str | Path, name: str = "") -> BookGeometry:
    """读 epub 的 spine 顺序 + 每篇字节数 (+ 目录章数)。"""
    p = Path(epub_path)
    with zipfile.ZipFile(p) as z:
        opf = next((n for n in z.namelist() if _OPF.search(n)), None)
        if not opf:
            raise ValueError(f"{p}: no opf")
        raw = z.read(opf).decode("utf-8", "replace")
        base = opf.rsplit("/", 1)[0] + "/" if "/" in opf else ""
        items: dict[str, str] = {}
        manifest: list[tuple[str, str, str]] = []
        for m in _ITEM.finditer(raw):
            tag = m.group(0)
            i, h = _ID.search(tag), _HREF.search(tag)
            if i and h:
                items[i.group(1)] = base + h.group(1)
                mt = _MEDIA.search(tag)
                pr = _PROPS.search(tag)
                manifest.append((h.group(1), mt.group(1) if mt else "",
                                 pr.group(1) if pr else ""))
        sm = _SPINE.search(raw)
        if not sm:
            raise ValueError(f"{p}: no spine")
        order = [items[r] for r in _IDREF.findall(sm.group(1)) if r in items]
        sizes: list[int] = []
        for href in order:
            try:
                sizes.append(len(z.read(href)))
            except KeyError:
                sizes.append(0)
        catalog = _catalog_from_ncx(z, opf, manifest) or _catalog_from_spine(order)
        if catalog > len(order):
            catalog = 0
    return BookGeometry(spine=order, sizes=sizes, name=name or p.stem, catalog=catalog)


def parse_position(pos: str) -> dict:
    """`6_0_0$##3-0-6-11752-0##$` -> {'pre','f0','f1','f2','f3','f4','chapter_id'}"""
    m = _POS.match(pos or "")
    if not m:
        return {}
    f = (m.group(2) or "").split("-")
    while len(f) < 5:
        f.append("0")
    out = {"pre": m.group(1), "f0": f[0], "f1": f[1], "f2": f[2], "f3": f[3], "f4": f[4]}
    # chapterId 从 pre 的第一段读 (pre = "<chapterId+1>_0_0")
    try:
        out["chapter_id"] = int(m.group(1).split("_")[0]) - 1
    except (ValueError, IndexError):
        out["chapter_id"] = None
    return out


def build_position(chapter_id: int, offset: int = 0, *, f0: str = F0_OFFICIAL) -> str:
    """生成官方格式的 readPosition。

    f0 用 3 (官方恒值); 我们之前伪造写 0, 与 app 记录不一致。
    """
    n = chapter_id + 1
    return f"{n}{PRE_TAIL}$##{f0}-0-{n}-{max(0, int(offset))}-0##$"


def percent_from_pull(pct_raw, chapter_id: int, offset: int, geo: BookGeometry) -> float:
    """读掌阅记录 → 归一成 0-1 比例。

    实测三本 app 写的全是 0-1 比例 (狐 0.29326823 / 藤01 0.009963691 /
    藤03 0.98467165)。唯一的 "100" 是我们自己用 kavita-bridge 写进去的
    (剑来)。所以这里只做 float() 转换 + 钳位, 不做量纲嗅探。
    """
    try:
        v = float(pct_raw)
    except (TypeError, ValueError):
        return geo.percent(chapter_id, offset)
    if v < 0:
        return 0.0
    if v > 1.0:
        # 百分数形态 (我们自己写的旧记录) — 归一
        v = v / 100.0
    return max(0.0, min(1.0, v))


# ---- xpath ↔ 字节偏移 (亚页精度) --------------------------------------
#
# Kavita 存 BookScrollId = xpath (如 //body/div[1] 或 //body/p[164]),
# 掌阅存 readPosition.f3 = 章内**字节偏移**。两者要互通就得能互转。
#
# 字节偏移怎么来: 拿原文 byte slice, 用 lxml 逐元素解析并记录每个元素
# 起始标签在原文里的字节位置 (sourceline 给行, 再按行累加字节数定位)。
# 这样 xpath → offset 和 offset → xpath 都是查表。

_BYTE = "utf-8"


class _Node:
    """字节级扫描出来的元素节点。"""

    __slots__ = ("name", "start", "end", "children", "attrs", "parent")

    def __init__(self, name: str, start: int):
        self.name = name
        self.start = start
        self.end = start
        self.children: list[_Node] = []
        self.attrs: dict[str, str] = {}
        self.parent: "_Node | None" = None


def _find_tag_end(raw: bytes, lt: int) -> int:
    """从 `<` 找到配对的 `>` — 跳过引号内的 `>` (属性值里合法出现)。"""
    n = len(raw)
    i = lt + 1
    quote = 0
    while i < n:
        c = raw[i]
        if quote:
            if c == quote:
                quote = 0
        elif c == 0x22 or c == 0x27:      # " '
            quote = c
        elif c == 0x3E:                     # >
            return i
        i += 1
    return -1


_ATTR_RE = re.compile(rb"""([\w:.-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""")


def _parse_attrs(inner: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in _ATTR_RE.finditer(inner):
        val = m.group(2)
        if val is None:
            val = m.group(3)
        if val is None:
            val = m.group(4) or b""
        out[m.group(1).decode("utf-8", "replace").lower()] = val.decode("utf-8", "replace")
    return out


def _scan_elements(raw: bytes) -> list[_Node]:
    """字节级标签扫描 → 元素树。

    不用 lxml: sourceline 只给行号, 多元素同行时偏移会退化到行首,
    而掌阅的 f3 是**精确字节** (狐的故事 f3=11752 落在某个 <p> 中间)。
    字节扫描拿到的是每个标签 `<` 的真实位置, 零精度损失。
    """
    n = len(raw)
    roots: list[_Node] = []
    stack: list[_Node] = []
    i = 0
    while i < n:
        lt = raw.find(b"<", i)
        if lt < 0:
            break
        if raw.startswith(b"<!--", lt):
            j = raw.find(b"-->", lt)
            i = (j + 3) if j >= 0 else n
            continue
        if raw.startswith(b"<![CDATA[", lt):
            j = raw.find(b"]]>", lt)
            i = (j + 3) if j >= 0 else n
            continue
        if raw.startswith(b"<?", lt):
            j = raw.find(b"?>", lt)
            i = (j + 2) if j >= 0 else n
            continue
        if raw.startswith(b"<!", lt):
            j = raw.find(b">", lt)
            i = (j + 1) if j >= 0 else n
            continue
        if raw.startswith(b"</", lt):
            j = raw.find(b">", lt)
            if j < 0:
                break
            name = raw[lt + 2:j].strip().decode("utf-8", "replace")
            for k in range(len(stack) - 1, -1, -1):
                if stack[k].name == name:
                    while len(stack) > k:
                        stack.pop().end = j
                    break
            i = j + 1
            continue
        # 起始标签
        j = _find_tag_end(raw, lt)
        if j < 0:
            break
        inner = raw[lt + 1:j]
        m = re.match(rb"[^\s/>]+", inner)
        if not m:
            i = j + 1
            continue
        node = _Node(m.group(0).decode("utf-8", "replace"), lt)
        node.attrs = _parse_attrs(inner)
        node.end = j
        if stack:
            node.parent = stack[-1]
            stack[-1].children.append(node)
        else:
            roots.append(node)
        if not inner.rstrip().endswith(b"/"):
            stack.append(node)
        i = j + 1
    for node in stack:
        node.end = n
    return roots


def _find_body(roots: list[_Node]) -> _Node | None:
    """定位 <body> — Kavita 的 xpath 全部以 //body 为根。"""
    stack = list(roots)
    while stack:
        nd = stack.pop(0)
        if nd.name.lower() == "body":
            return nd
        stack = list(nd.children) + stack
    return None


def _walk(node: _Node, parent_xp: str, out: list[tuple[str, int, int]]) -> None:
    """按文档序吐出 (xpath, start_byte, end_byte)。

    xpath 格式对齐 Kavita BookScrollId: `//body/div[1]/p[4]`, 下标是
    **同名兄弟**里的序号 (不是兄弟总数)。
    """
    counter: dict[str, int] = {}
    for ch in node.children:
        counter[ch.name] = counter.get(ch.name, 0) + 1
        xp = f"{parent_xp}/{ch.name}[{counter[ch.name]}]"
        out.append((xp, ch.start, ch.end))
        _walk(ch, xp, out)


def _elem_byte_offsets(raw: bytes) -> tuple[str, list[tuple[str, int, int]]]:
    """返回 (解码文本, [(xpath, start_byte, end_byte)])。"""
    text = raw.decode("utf-8", "replace")
    roots = _scan_elements(raw)
    body = _find_body(roots)
    if body is None:
        return text, []
    out: list[tuple[str, int, int]] = [("//body", body.start, body.end)]
    _walk(body, "//body", out)
    return text, out


def _xpath_of(el) -> str:
    """保留给旧调用方 — 新实现走 _scan_elements 的字节扫描。"""
    raise NotImplementedError("改用 DocIndex (字节扫描) 而不是 lxml 元素对象")


class DocIndex:
    """一篇 spine 文档的 xpath ↔ 字节偏移索引。"""

    def __init__(self, raw: bytes):
        self.raw = raw
        self.text, self.index = _elem_byte_offsets(raw)
        self._by_xpath = {xp: (s, e) for xp, s, e in self.index}
        # (start, xpath) 排序, 供 offset → xpath 反查
        self._by_start = sorted(((s, xp) for xp, s, _ in self.index), key=lambda t: t[0])

    def offset_of_xpath(self, xpath: str) -> int:
        """xpath → 章内字节偏移。找不到返 0 (章首)。"""
        hit = self._by_xpath.get(xpath)
        if hit:
            return hit[0]
        # Kavita 偶尔存的是前缀 (存 //body/div[1] 而完整是 //body/div[1]/p[3])
        # → 取该前缀下**最早**出现的完整 xpath, 即刚进入那个元素的位置
        best: int | None = None
        for s, xp in self._by_start:
            if xp.startswith(xpath + "/"):
                if best is None or s < best:
                    best = s
        return best if best is not None else 0

    def xpath_of_offset(self, offset: int) -> str | None:
        """章内字节偏移 → xpath。取起点 <= offset 的最深元素。

        "最深" = 起点最靠后 (同起点时取后出现的, 即更内层)。
        """
        lo, hi = 0, len(self._by_start)
        while lo < hi:                       # 二分找最后一个 start <= offset
            mid = (lo + hi) // 2
            if self._by_start[mid][0] <= offset:
                lo = mid + 1
            else:
                hi = mid
        for s, xp in reversed(self._by_start[:lo]):
            if s <= offset:
                return xp
        return None

    def text_of_offset(self, offset: int) -> str:
        """偏移之前的正文。

        注意: offset 是**字节**偏移, raw 是 UTF-8, CJK 每字 3 字节。
        所以必须先按字节切 raw 再解码, 不能拿字节偏移去索引解码后的 str
        (狐的故事 body 8 万字节, 全是中文, 差 3 倍)。
        """
        off = max(0, min(int(offset), len(self.raw)))
        xp = self.xpath_of_offset(off)
        if xp:
            off = min(off, self._by_xpath.get(xp, (off, off))[0])
        return self.raw[:off].decode("utf-8", "replace")

    def element_at(self, offset: int) -> tuple[str, str] | None:
        """调试用: 返回 (xpath, 该元素开头 160 字)。按字节切片。"""
        xp = self.xpath_of_offset(offset)
        if not xp:
            return None
        s, e = self._by_xpath[xp]
        # e 是闭合标签的 '>' 位置, 故意含在内层以便看完整尾标签
        return xp, self.raw[s:min(e, s + 300)].decode("utf-8", "replace")


def build_doc_index(epub_path: str | Path, spine_index: int) -> DocIndex | None:
    """取某篇 spine 文档并建索引。"""
    p = Path(epub_path)
    try:
        g = load_geometry(p)
    except (OSError, ValueError, KeyError):
        return None
    if spine_index < 0 or spine_index >= len(g.spine):
        return None
    with zipfile.ZipFile(p) as z:
        try:
            raw = z.read(g.spine[spine_index])
        except KeyError:
            return None
    return DocIndex(raw)


if __name__ == "__main__":
    import sys

    for arg in sys.argv[1:]:
        g = load_geometry(arg)
        print(f"{g.name}: spine={len(g.sizes)} total_bytes={g.total_bytes} "
              f"totalCount={g.total_count}")
        for ch in (0, 1, len(g.sizes) // 2, len(g.sizes) - 2):
            print(f"   ch={ch:4d} pct={g.percent(ch):.8f}")