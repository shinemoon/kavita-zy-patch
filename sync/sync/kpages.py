"""把掌阅 epub 重排成 Kavita 能"逐页对齐"的版本。

## 为什么必须重排 (2026-10-23 从 Kavita 前端包 + API 实证)

Kavita 的 EPUB 阅读器模型 (chunk-7R3IRPAA.js):

    setupBookReader(): this.maxPages.set(e.pages)      // chapter.pages
    loadPage():        getBookPage(chapterId, pageNum) // 后端返回 spine[pageNum] 的正文
    saveProgress():    saveProgress(..., pageNum, xpath)

也就是说 **1 个 spine 文档 = 1 页**, 书架百分比 = PagesRead / Chapter.Pages
(前端 chunk-WRIJIJRF.js: pagesRead/pages*100)。文档内位置只能用
BookScrollId (xpath) 表达, 不进百分比。

掌阅那边 readPercent = 全 spine 的**字节比例** (见 zpct.py)。两边量纲不同:

    狐的故事  spine 9 篇
      0 cover 433B / 1 制作信息 1233B / 2 简介 2978B / 3 简介 2781B
      4 目录 1341B  (前 5 篇合计 2.68% 字节)
      5..8 四章正文 75425 / 80464 / 69630 / 92866 B

    掌阅 48.96%  ≈ 第四章开头
    Kavita round(0.4896*9)=4 → 打开第 5 篇 = 目录页  ← 用户看到的 bug
    显示 4/9 = 44.4%

只改写库逻辑救不了: 页数只有 9, 前 5 篇只占 2.7% 字节却占 55% 的页。
唯一办法是让 **页 === 等字节切片**: 把整本书按字节均匀切成 N 页, 每页
大小相同 → PagesRead/N 就等于字节比例 → 两套系统的进度指标同构。

本模块做这件事, 并落一份 sidecar 映射 (原书全局字节区间 ↔ 新页号),
pull/push 都靠它换算。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

# ---- 参数 ----------------------------------------------------------------

# 目标每页字节数 / 页数上下限。页数上下限决定百分比粒度 (1/N)。
#   狐的故事 327KB → ceil(327151/4096)=80 → 抬到 100 页 (每页 ~3.3KB, 粒度 1%)
#   剑来     42MB   → ceil(41907094/4096)=10231 → 压到 MAX_PAGES
#
# MAX_PAGES 决定 Kavita 侧**显示**粒度和"翻页要读多久才动一格":
#   600  页 → 每页 70KB ≈ 2.3 万字 ≈ 一小时才动 0.17%   (2026-10-23 用户实测
#             "读了一晚上 pct 没动", 就是这个粒度太粗)
#   3000 页 → 每页 14KB ≈ 4700 字 ≈ 两屏, 一格 0.033%
# 掌阅侧的精度不靠这个数 —— push 会用 BookScrollId 算页内偏移 (见
# `fraction_of_scroll_id`), 页内任何位置都能反映出去。
TARGET_PAGE_BYTES = int(os.environ.get("ZY_KPAGES_TARGET_BYTES") or 4096)
MIN_PAGES = 100
MAX_PAGES = int(os.environ.get("ZY_KPAGES_MAX_PAGES") or 3000)

PAGE_SUBDIR = "kp"          # 新页放在 <opf_dir>/kp/ 下
PAGE_PREFIX = "p"

_MIMETYPE = b"application/epub+zip"


# ---- 字节级扫描 -----------------------------------------------------------

_TAG_START = re.compile(rb"<([A-Za-z][-A-Za-z0-9:_.]*)")
_TAG_NAME = re.compile(rb"([A-Za-z][-A-Za-z0-9:_.]*)")
_TAG_ATTR = re.compile(rb"""([A-Za-z_:][-A-Za-z0-9_:.]*)\s*=\s*(?:"([^"]*)"|'([^']*)')""")


def _tag_end(raw: bytes, i: int) -> int:
    """从 '<' 起找标签真正的 '>' (跳过引号内的)。返回 '>' 后一位。"""
    n = len(raw)
    j = i + 1
    quote = 0
    while j < n:
        c = raw[j]
        if quote:
            if c == quote:
                quote = 0
        elif c in (0x22, 0x27):
            quote = c
        elif c == 0x3E:  # '>'
            return j + 1
        j += 1
    return n


@dataclass
class Token:
    off: int          # 文件内字节偏移
    kind: str         # open / close / self
    name: str
    raw: bytes        # 原始标签字节 (open/self 用; close 只留 name)


# HTML 语义修正表: 源书里 <p> 常常不闭合, 纯 XML 栈会一路堆到章尾。
_VOID = {"br", "img", "hr", "meta", "link", "input", "area", "base", "col",
         "embed", "param", "source", "track", "wbr"}
_BLOCK_CLOSES_P = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol",
                   "li", "table", "blockquote", "section", "article", "header",
                   "footer", "pre", "figure", "aside", "nav"}
_SELF_NESTING = {"li", "tr", "td", "th", "dt", "dd", "option"}


def _auto_close(stack: list[tuple[str, bytes]], name: str) -> None:
    if name in _BLOCK_CLOSES_P and stack and stack[-1][0] == "p":
        stack.pop()
    if name in _SELF_NESTING:
        for k in range(len(stack) - 1, -1, -1):
            if stack[k][0] == name:
                del stack[k:]
                break



def scan_tokens(raw: bytes, start: int, end: int) -> list[Token]:
    """扫 [start, end) 区间内的标签, 产出 token 列表 (text 不产出)。"""
    toks: list[Token] = []
    i = start
    n = min(end, len(raw))
    while i < n:
        k = raw.find(b"<", i)
        if k < 0 or k >= n:
            break
        if raw.startswith(b"<!--", k):
            e = raw.find(b"-->", k + 4)
            i = (e + 3) if e >= 0 else n
            continue
        if raw.startswith(b"<![CDATA[", k):
            e = raw.find(b"]]>", k + 9)
            i = (e + 3) if e >= 0 else n
            continue
        if raw.startswith(b"<?", k) or raw.startswith(b"<!", k):
            e = raw.find(b">", k + 2)
            i = (e + 1) if e >= 0 else n
            continue
        if raw.startswith(b"</", k):
            m = _TAG_NAME.match(raw, k + 2)
            if not m:
                e = raw.find(b">", k + 2)
                i = (e + 1) if e >= 0 else n
                continue
            e = _tag_end(raw, k)
            toks.append(Token(k, "close", m.group(1).decode("ascii", "replace").lower(), b""))
            i = e
            continue
        m = _TAG_START.match(raw, k)
        if not m:
            i = k + 1
            continue
        e = _tag_end(raw, k)
        seg = raw[k:e]
        selfclose = seg.rstrip().endswith(b"/>")
        name = m.group(1).decode("ascii", "replace").lower()
        toks.append(Token(k, "self" if selfclose else "open", name, seg))
        if name == "script" and not selfclose:
            end_s = raw.lower().find(b"</script", e)
            i = end_s if end_s >= 0 else n
            continue
        i = e
    return toks


def _find_body(raw: bytes) -> tuple[int, int, bytes]:
    """返回 (body 开标签结束偏移, </body> 起始偏移, body 开标签原始字节)。"""
    low = raw.lower()
    m = re.search(rb"<body\b", low)
    if not m:
        # 没有 body 标签: 退化成整个文件
        return 0, len(raw), b"<body>"
    bstart = m.start()
    bend = _tag_end(raw, bstart)
    cstart = low.find(b"</body", bend)
    if cstart < 0:
        cstart = len(raw)
    return bend, cstart, raw[bstart:bend]


@dataclass
class Doc:
    idx: int
    item_id: str
    href: str             # opf 里的 href (相对 opf)
    path: str             # zip 内路径
    gs: int               # 全局起始字节
    size: int
    raw: bytes = b""
    content_start: int = 0    # body 内容起点 (文件内偏移)
    content_end: int = 0      # body 内容终点
    body_tag: bytes = b"<body>"
    tokens: list[Token] = field(default_factory=list)
    ids: dict[str, int] = field(default_factory=dict)   # id -> 文件内偏移

    @property
    def ge(self) -> int:
        return self.gs + self.size

    @property
    def rel(self) -> str:
        return self.path.rsplit("/", 1)[0] + "/" if "/" in self.path else ""

    def stack_at(self, off: int) -> list[tuple[str, bytes]]:
        """off 处仍然 open 的元素栈 [(name, raw_open_tag)]。

        按 **HTML 语义** 维护 (源文件大量 <p> 没闭合, 纯 XML 栈会越积越深,
        跨页就吐出 </p></p>... 这种非法的收尾)。规则见 _STACK 常量表。
        """
        out: list[tuple[str, bytes]] = []
        for t in self.tokens:
            if t.off >= off:
                break
            if t.kind == "self":
                continue
            if t.kind == "open":
                if t.name in _VOID:
                    continue
                _auto_close(out, t.name)
                out.append((t.name, t.raw))
            elif t.kind == "close":
                for k in range(len(out) - 1, -1, -1):
                    if out[k][0] == t.name:
                        del out[k:]
                        break
        return out

    def snap(self, off: int, lower: int) -> int:
        """把 off 吸到最近的标签边界 (优先向前, 不能 <= lower)。"""
        off = max(self.content_start, min(off, self.content_end))
        best = None
        nxt = None
        for t in self.tokens:
            if t.off <= lower:
                continue
            if t.off <= off:
                best = t.off
            elif nxt is None:
                nxt = t.off
                break
        if best is not None:
            return best
        if nxt is not None:
            return nxt
        return self.content_end


# ---- epub 结构 ------------------------------------------------------------

@dataclass
class Epub:
    src: Path
    opf_path: str
    opf_raw: str
    opf_dir: str
    docs: list[Doc]
    names: list[str]
    # opf 里 <item> 列表 (id, href, media-type, 其余属性原文)
    items: list[dict]

    @property
    def total_bytes(self) -> int:
        return sum(d.size for d in self.docs)


def _opf_href_base(opf_path: str) -> str:
    return opf_path.rsplit("/", 1)[0] + "/" if "/" in opf_path else ""


def _norm_join(base: str, href: str) -> str:
    from posixpath import normpath
    return normpath(base + href)


def load_epub(path: str | Path) -> Epub:
    p = Path(path)
    with zipfile.ZipFile(p) as z:
        names = z.namelist()
        opf_path = next((n for n in names if n.lower().endswith(".opf")), None)
        if not opf_path:
            raise ValueError(f"{p}: 没有 opf")
        opf_raw = z.read(opf_path).decode("utf-8", "replace")
        base = _opf_href_base(opf_path)

        items: list[dict] = []
        for m in re.finditer(r"<item\b[^>]*>", opf_raw):
            tag = m.group(0)
            attrs = {k.decode(): (v1 or v2).decode("utf-8", "replace")
                     for k, v1, v2 in _TAG_ATTR.findall(tag.encode())}
            if "href" not in attrs:
                continue
            items.append({"id": attrs.get("id", ""), "href": attrs["href"],
                          "media_type": attrs.get("media-type", ""), "attrs": attrs,
                          "raw": tag})
        by_id = {it["id"]: it for it in items}

        spine_attr = re.search(r"<spine\b[^>]*>(.*?)</spine>", opf_raw, re.S)
        if not spine_attr:
            raise ValueError(f"{p}: 没有 spine")
        idrefs = re.findall(r'<itemref\b[^>]*idref="([^"]+)"', spine_attr.group(1))

        docs: list[Doc] = []
        gs = 0
        for i, idref in enumerate(idrefs):
            it = by_id.get(idref)
            if not it:
                continue
            zpath = _norm_join(base, it["href"])
            if zpath not in names:
                continue
            raw = z.read(zpath)
            cs, ce, btag = _find_body(raw)
            d = Doc(idx=len(docs), item_id=idref, href=it["href"], path=zpath,
                    gs=gs, size=len(raw), raw=raw,
                    content_start=cs, content_end=ce, body_tag=btag)
            d.tokens = scan_tokens(raw, cs, ce)
            for m in re.finditer(rb'\bid="([^"]+)"', raw):
                d.ids[m.group(1).decode("utf-8", "replace")] = m.start()
            docs.append(d)
            gs += len(raw)

    return Epub(src=p, opf_path=opf_path, opf_raw=opf_raw, opf_dir=base,
                docs=docs, names=names, items=items)


# ---- 切页 -----------------------------------------------------------------

_IMG_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp")


def image_book_reason(epub: Epub) -> str | None:
    """图片型 epub (一页一张图, 如掌阅 moe 排版) 不能按字节重排。

    这类书的 spine 文档只有几百字节 (一个 <img> 包裹), 正文全在图里。
    按字节切会把 <img> 标签切断, 产出破图。Kavita 本来就是一图一页,
    粒度已是天然最优 → 直接返回原因, 调用方跳过重排。
    """
    docs = epub.docs
    if not docs:
        return None
    imgs = [n for n in epub.names if n.lower().endswith(_IMG_EXT)]
    if len(imgs) < len(docs) * 0.8:
        return None
    avg = epub.total_bytes // len(docs)
    if avg >= 2000:
        return None
    return (f"图片型 epub（{len(docs)} 页 / {len(imgs)} 张图，正文平均 {avg} 字节），"
            f"Kavita 一图一页已是最细粒度，不需也不该按字节重排")


def plan_pages(epub: Epub, target_bytes: int = TARGET_PAGE_BYTES,
               min_pages: int = MIN_PAGES, max_pages: int = MAX_PAGES) -> list[tuple[int, int]]:
    """按全局字节均匀切页。返回 [(全局起点, 全局终点)]。"""
    why = image_book_reason(epub)
    if why:
        raise ValueError(why)
    T = epub.total_bytes
    if T <= 0:
        raise ValueError("空书")
    n = max(min_pages, min(max_pages, (T + target_bytes - 1) // target_bytes))
    if n <= 1:
        return [(0, T)]

    edges = [round(k * T / n) for k in range(n + 1)]
    edges[0], edges[-1] = 0, T

    def doc_at(g: int) -> Doc:
        for d in epub.docs:
            if d.gs <= g < d.ge:
                return d
        return epub.docs[-1]

    pages: list[tuple[int, int]] = []
    prev = 0
    for k in range(n):
        s, e = edges[k], edges[k + 1]
        if k > 0:
            d = doc_at(s)
            s = d.gs + d.snap(s - d.gs, max(prev - d.gs, d.content_start - d.gs))
        if k < n - 1:
            d = doc_at(e)
            e = d.gs + d.snap(e - d.gs, max(s - d.gs + 1, d.content_start - d.gs))
        if e <= s:                      # 退化: 挤到下一页开头
            e = s
        pages.append((s, e))
        prev = s
    # 合并空页
    out = []
    for s, e in pages:
        if e - s <= 0 and out:
            out[-1] = (out[-1][0], max(out[-1][1], e))
            continue
        out.append((s, e))
    if out:
        out[-1] = (out[-1][0], T)
    return out


# ---- 渲染新页 -------------------------------------------------------------

_ATTR_URL = re.compile(r"""(?P<name>\b(?:href|src|xlink:href))\s*=\s*(?P<q>["'])(?P<url>[^"']*)(?P=q)""")
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_ID_ATTR = re.compile(rb'\bid\s*=\s*"[^"]*"')


def _strip_id(tag: bytes) -> bytes:
    return _ID_ATTR.sub(b"", tag)


def _rel_from_page_dir(target_path: str) -> str:
    from posixpath import relpath
    return relpath(target_path, PAGE_SUBDIR)


class _Render:
    def __init__(self, epub: Epub, pages: list[tuple[int, int]]):
        self.epub = epub
        self.pages = pages
        self.page_dir = epub.opf_dir + PAGE_SUBDIR + "/"
        # 原文档 -> 它起点所在的页号 (TOC / 跨文档链接用)
        self.doc_page: dict[str, int] = {d.path: self.page_of(d.gs) for d in epub.docs}
        # id -> 页号
        self.id_page: dict[tuple[str, str], int] = {}
        for d in epub.docs:
            for k, off in d.ids.items():
                self.id_page[(d.path, k)] = self.page_of(d.gs + off)

    def _doc_at(self, g: int) -> Doc | None:
        for d in self.epub.docs:
            if d.gs <= g < d.ge:
                return d
        return None

    def page_of(self, g: int) -> int:
        for i, (s, e) in enumerate(self.pages):
            if s <= g < e:
                return i
        return max(0, len(self.pages) - 1)

    def page_name(self, i: int) -> str:
        return f"{PAGE_PREFIX}{i + 1:04d}.xhtml"

    # ---- URL 重写 ----
    def _rewrite_url(self, url: str, doc: Doc) -> str:
        if not url or url.startswith("#"):
            if url.startswith("#") and len(url) > 1:
                p = self.id_page.get((doc.path, url[1:]))
                if p is not None:
                    return f"{self.page_name(p)}#{url[1:]}"
            return url
        if _SCHEME.match(url):
            return url
        path, _, frag = url.partition("#")
        raw_path = path
        path = path.strip()
        if not path:
            return url
        target = _norm_join(doc.rel, path)
        if target in self.doc_page:
            name = self.page_name(self.doc_page[target])
            if frag and self.id_page.get((target, frag)) == self.doc_page[target]:
                return f"{name}#{frag}"
            return name
        if target in self.epub.names:
            # 非正文档 (css / 图片 / 字体) → 重算相对路径
            from posixpath import relpath
            return relpath(target, self.page_dir.rstrip("/")) + (("#" + frag) if frag else "")
        return url      # 解析不出来就原样保留, 不要制造坏链接

    def _rewrite_fragment(self, text: str, doc: Doc) -> str:
        def sub(m):
            return '%s=%s%s%s' % (m.group("name"), m.group("q"),
                                  self._rewrite_url(m.group("url"), doc), m.group("q"))
        return _ATTR_URL.sub(sub, text)

    # ---- 一页 ----
    def render(self, i: int) -> tuple[str, dict]:
        s, e = self.pages[i]
        segs: list[tuple[Doc, int, int]] = []
        for d in self.epub.docs:
            if d.ge <= s or d.gs >= e:
                continue
            a = max(s, d.gs) - d.gs
            b = min(e, d.ge) - d.gs
            a = max(a, d.content_start)
            b = min(b, d.content_end)
            if b <= a:
                continue
            segs.append((d, a, b))
        if not segs:
            first = self._doc_at(s) or self.epub.docs[0]
            segs = [(first, first.content_start, first.content_start)]

        heads: list[str] = []
        for d, _a, _b in segs:
            heads.extend(self._head_parts(d))
        heads = _dedupe(heads)
        title = self._title(segs[0][0])
        body_attr = _body_attrs(segs[0][0].body_tag)

        parts: list[str] = []
        for d, a, b in segs:
            st_a = d.stack_at(a)
            st_b = d.stack_at(b)
            prefix = "".join(_strip_id(t).decode("utf-8", "replace") for _n, t in st_a)
            extra = st_b[len(st_a):] if len(st_b) > len(st_a) else []
            suffix = "".join("</%s>" % n for n, _t in reversed(extra))
            body = d.raw[a:b].decode("utf-8", "replace")
            body = self._rewrite_fragment(body, d)
            parts.append(_normalize_fragment(prefix + body + suffix))

        html = (
            "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"
            "<!DOCTYPE html>\n"
            "<html xmlns=\"http://www.w3.org/1999/xhtml\" xml:lang=\"zh-CN\">\n"
            "<head>\n<meta charset=\"utf-8\"/>\n<title>%s</title>\n%s\n</head>\n"
            "<body%s>\n%s\n</body>\n</html>\n"
            % (title, "\n".join(heads), body_attr, "\n".join(parts))
        )
        meta = {"i": i, "s": s, "e": e,
                "docs": [{"path": d.path, "a": a, "b": b} for d, a, b in segs]}
        return html, meta

    def _head_parts(self, d: Doc) -> list[str]:
        raw = d.raw.decode("utf-8", "replace")
        m = re.search(r"<head\b[^>]*>(.*?)</head>", raw, re.S | re.I)
        if not m:
            return []
        out = []
        for mm in re.finditer(r"<link\b[^>]*>", m.group(1), re.I):
            out.append(self._rewrite_fragment(mm.group(0), d))
        for mm in re.finditer(r"<style\b[^>]*>.*?</style>", m.group(1), re.S | re.I):
            out.append(mm.group(0))
        return out

    def _title(self, d: Doc) -> str:
        raw = d.raw.decode("utf-8", "replace")
        m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.S | re.I)
        return (m.group(1).strip() if m else "") or ""


def _normalize_fragment(text: str) -> str:
    """把 (可能没闭合的) 片段过一遍容错解析器, 输出合法 XHTML。

    源书是"能读但 XML 不合法"的 HTML (`<p>` 大量不闭合)。Kavita 自己解析
    时是容错的, 但我们切页后必须保证每页自洽 —— 否则 epub 校验/别的阅读器
    会直接拒绝整章。lxml.html 按浏览器语义补闭合, 再按 XML 序列化。
    """
    try:
        from lxml import etree as LE
        from lxml import html as LH
        frag = LH.fragment_fromstring(text, create_parent="div")
        out: list[str] = []
        if frag.text and frag.text.strip():
            out.append(frag.text)
        for child in frag:
            out.append(LE.tostring(child, encoding="unicode", method="xml"))
        return "".join(out) or text
    except Exception:
        return text


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    out = []
    for s in items:
        k = re.sub(r"\s+", " ", s).strip()
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
    return out


def _body_attrs(tag: bytes) -> str:
    t = tag.decode("utf-8", "replace")
    m = re.match(r"<body\b([^>]*)>", t, re.I | re.S)
    return m.group(1).rstrip("/").rstrip() if m else ""


# ---- 组装 epub ------------------------------------------------------------

def build_edition(src: str | Path, dst: str | Path, *,
                  target_bytes: int = TARGET_PAGE_BYTES,
                  min_pages: int = MIN_PAGES, max_pages: int = MAX_PAGES,
                  progress=None) -> dict:
    """生成 Kavita 版 epub + 返回 sidecar 映射。"""
    epub = load_epub(src)
    pages = plan_pages(epub, target_bytes, min_pages, max_pages)
    rend = _Render(epub, pages)

    npages = len(pages)
    page_items = [{"id": f"kp{i + 1:04d}", "href": f"{PAGE_SUBDIR}/{rend.page_name(i)}"}
                  for i in range(npages)]
    page_html: list[str] = []
    page_meta: list[dict] = []
    for i in range(npages):
        html, meta = rend.render(i)
        page_html.append(html)
        page_meta.append(meta)
        if progress:
            progress(i, npages)

    # ---- opf ----
    spine_paths = {d.path for d in epub.docs}
    kept_items = [it for it in epub.items
                  if _norm_join(epub.opf_dir, it["href"]) not in spine_paths]
    lines = ["<manifest>"]
    for it in kept_items:
        lines.append("    " + it["raw"])
    for it in page_items:
        lines.append(f'    <item id="{it["id"]}" href="{it["href"]}" '
                     f'media-type="application/xhtml+xml"/>')
    lines.append("  </manifest>")

    new_opf = re.sub(r"<manifest\b[^>]*>.*?</manifest>",
                     "\n  ".join(lines), epub.opf_raw, count=1, flags=re.S)
    spine_xml = ["<spine" + (re.search(r"<spine\b([^>]*)>", epub.opf_raw).group(1)
                             if re.search(r"<spine\b([^>]*)>", epub.opf_raw) else "") + ">"]
    for it in page_items:
        spine_xml.append(f'    <itemref idref="{it["id"]}"/>')
    spine_xml.append("  </spine>")
    new_opf = re.sub(r"<spine\b[^>]*>.*?</spine>", "\n  ".join(spine_xml),
                     new_opf, count=1, flags=re.S)

    def fix_guide(m):
        from posixpath import relpath
        href = m.group(2)
        doc_path = _norm_join(epub.opf_dir, href.split("#")[0])
        p = rend.doc_page.get(doc_path)
        if p is None:
            return m.group(0)
        # guide 的 href 以 **opf 所在目录** 为基准 (toc/guide 都是),
        # 所以要把 kp/ 前缀一起带上, 否则 Kavita 解析 manifest 时对不上。
        base = epub.opf_dir.rstrip("/") or "."
        rel = relpath(rend.page_dir.rstrip("/") + "/" + rend.page_name(p), base)
        return '%s"%s"' % (m.group(1), rel)

    new_opf = re.sub(r'(<reference\b[^>]*href=)"([^"]+)"', fix_guide, new_opf)

    # ---- ncx / nav ----
    ncx_path = next((n for n in epub.names if n.lower().endswith(".ncx")), None)
    nav_item = next((it for it in epub.items
                     if it["attrs"].get("properties", "").find("nav") >= 0), None)

    def rewrite_link_doc(text: str, base_dir: str) -> str:
        from posixpath import relpath
        here = base_dir.rstrip("/")

        def sub(m):
            url = m.group("url")
            path, _, frag = url.partition("#")
            if _SCHEME.match(url) or not path:
                return m.group(0)
            target = _norm_join(base_dir, path)
            p = rend.doc_page.get(target)
            if p is None:
                # 源文件本身就是我们生成的重排版时, 链接已经指向 pNNNN.xhtml。
                # 这种情况也要重算相对路径 —— 否则 base_dir 一换 (toc 在
                # OEBPS/ vs OEBPS/ncx/) 就会留下 "p0001.xhtml" 这种相对
                # 于 opf 根的错误 src, Kavita (VersOne.Epub) 直接抛
                # "content source not found in EPUB manifest", 整章打不开。
                m2 = re.search(r"(?:^|/)" + re.escape(PAGE_PREFIX) + r"(\d{4})\.xhtml$",
                               path)
                if m2:
                    idx = int(m2.group(1)) - 1
                    if 0 <= idx < npages:
                        p = idx
            if p is None:
                return m.group(0)
            href = relpath(rend.page_dir.rstrip("/") + "/" + rend.page_name(p), here)
            return '%s=%s%s%s' % (m.group("name"), m.group("q"), href, m.group("q"))
        return _ATTR_URL.sub(sub, text)

    def dir_of(zpath: str) -> str:
        return zpath.rsplit("/", 1)[0] + "/" if "/" in zpath else ""

    new_ncx = None
    if ncx_path:
        with zipfile.ZipFile(src) as z:
            new_ncx = rewrite_link_doc(z.read(ncx_path).decode("utf-8", "replace"),
                                       dir_of(ncx_path))
    new_nav = None
    nav_path = None
    if nav_item:
        nav_path = _norm_join(epub.opf_dir, nav_item["href"])
        with zipfile.ZipFile(src) as z:
            if nav_path in z.namelist():
                new_nav = rewrite_link_doc(z.read(nav_path).decode("utf-8", "replace"),
                                           dir_of(nav_path))

    # 源书声明 EPUB3 却没有 nav (掌阅个人云的书常见, 例如 路邊的藤井) →
    # Kavita 的 VersOne.Epub 抛 Epub3NavException, 整本"解析不出信息"直接不进库。
    # 切片版的 spine 是我们自己生成的, 不依赖 nav; 只要源书有 NCX, 把 package
    # 声明降成 2.0 就能过。改的是**输出**, 原书字节一个不动 (几何要保真)。
    downgraded = False
    if not nav_item and ncx_path:
        m = re.search(r'(<package\b[^>]*?\bversion\s*=\s*")([^"]+)(")', new_opf)
        if m and m.group(2).strip().startswith("3"):
            new_opf = new_opf[:m.start(2)] + "2.0" + new_opf[m.end(2):]
            downgraded = True

    # ---- 写盘 ----
    dst = Path(dst)
    tmp = dst.with_suffix(".tmp")
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        zout.writestr(zipfile.ZipInfo("mimetype"), _MIMETYPE, zipfile.ZIP_STORED)
        for n in zin.namelist():
            if n == "mimetype" or n in spine_paths or n == epub.opf_path:
                continue
            if ncx_path and n == ncx_path:
                zout.writestr(n, (new_ncx or zin.read(n)).encode("utf-8"))
                continue
            if nav_path and n == nav_path and new_nav is not None:
                zout.writestr(n, new_nav.encode("utf-8"))
                continue
            zout.writestr(n, zin.read(n))
        for i, html in enumerate(page_html):
            zout.writestr(rend.page_dir + rend.page_name(i), html.encode("utf-8"))
        zout.writestr(epub.opf_path, new_opf.encode("utf-8"))
    problems = validate_edition(tmp)
    if problems:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("edition 自检失败: " + "; ".join(problems[:6]))
    shutil.move(str(tmp), str(dst))

    sidecar = {
        "version": 1,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "src": str(Path(src).resolve()),
        "edition": str(dst.resolve()),
        "src_total_bytes": epub.total_bytes,
        "catalog_count": _src_catalog_count(src),
        "total_pages": npages,
        "target_page_bytes": target_bytes,
        "opf_version_downgraded": downgraded,
        "pages": [{"i": i, "s": s, "e": e} for i, (s, e) in enumerate(pages)],
        "docs": [{"idx": d.idx, "path": d.path, "gs": d.gs, "size": d.size,
                  "page": rend.doc_page.get(d.path, 0)} for d in epub.docs],
        "page_files": [rend.page_dir + rend.page_name(i) for i in range(npages)],
        "page_dir": rend.page_dir,
    }
    return sidecar


# ---- sidecar 读写 ---------------------------------------------------------

def sidecar_path(series_id: int | str, state_dir: Path | None = None) -> Path:
    base = state_dir or Path("/vol2/1001/zhangyue-bridge/state/kpages")
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{series_id}.json"


def save(sidecar: dict, series_id: int | str, state_dir: Path | None = None) -> Path:
    p = sidecar_path(series_id, state_dir)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(sidecar, ensure_ascii=False, indent=1))
    os.replace(tmp, p)
    try:
        os.chmod(p, 0o644)      # root 侧进程写出来的 sidecar, CLI(claud) 也要读
    except OSError:
        pass
    return p


def load(series_id: int | str, state_dir: Path | None = None) -> dict | None:
    p = sidecar_path(series_id, state_dir)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


# ---- 换算 -----------------------------------------------------------------

def page_of_bytes(sc: dict, g: int) -> int:
    """全局字节位置 → 页号。"""
    pages = sc["pages"]
    lo, hi = 0, len(pages) - 1
    if g <= pages[0]["s"]:
        return 0
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if pages[mid]["s"] <= g:
            lo = mid
        else:
            hi = mid - 1
    return lo


def bytes_of_page(sc: dict, p: int) -> int:
    p = max(0, min(p, len(sc["pages"]) - 1))
    return sc["pages"][p]["s"]


def pct_of_page(sc: dict, p: int) -> float:
    T = sc["src_total_bytes"]
    return (bytes_of_page(sc, p) / T) if T else 0.0


def page_of_pct(sc: dict, pct01: float) -> int:
    T = sc["src_total_bytes"]
    # round 而不是 int: 页起点字节再除回来会有 float 误差 (7*step -> 6.999...),
    # 直接截断会掉到上一页, 于是 pct→页→pct→页 在两个页之间来回跳。
    g = int(round(max(0.0, min(1.0, pct01)) * T))
    return page_of_bytes(sc, g)


# ---- 两套系统的进度口径 (唯一权威定义) -------------------------------------
#
# 事实 (2026-10-23 从 Kavita 前端 bundle + REST 实测反查):
#
#   掌阅 readPercent = 全书字节位置 / 全书字节数          (0.0 - 1.0)
#   Kavita 书架百分比 = PagesRead / Chapter.Pages
#   Kavita 续读页     = spine[PagesRead]                  ← 百分比和位置同源
#   Kavita 保存进度   = 当前 pageNum; 停在最后一页时写 pageNum+1 (= Pages)
#
# 我们的重排版把每页切成等字节片, 所以:
#
#   pct  → PagesRead = 页号(含该字节的那一页, 0 基)     ← 位置精确
#   PagesRead → pct   = 该页起点字节 / 全书字节          ← 位置精确
#
# 两者互为逆运算 (误差 < 1 页 = 1/N)。**不要**用 round(pct*N) —— 那会把
# 页号抬到下一 页, 打开后位置比实际读者位置超前最多 1 页。
# 显示上 Kavita 会少 0~1 个百分点 (它显示"读完了几页"), 掌阅显示字节比例,
# 这是两个系统定义不同, 不是 bug。

def pages_read_for_pct(sc: dict, pct01: float, *, finished: bool = False) -> int:
    """掌阅 pct → Kavita PagesRead。读完时返回 N (= 100%), 否则页号 0..N-1。"""
    n = len(sc["pages"])
    if n <= 0:
        return 0
    if finished or pct01 >= 1.0:
        return n
    return max(0, min(page_of_pct(sc, pct01), n - 1))


def pct_for_pages_read(sc: dict, pages_read: int) -> float:
    """Kavita PagesRead → 掌阅 pct。PagesRead == N 表示读完。"""
    T = sc["src_total_bytes"]
    n = len(sc["pages"])
    if n <= 0 or T <= 0:
        return 0.0
    if pages_read >= n:
        return 1.0
    return pages_read_pct_exact(sc, max(0, pages_read))


def pages_read_pct_exact(sc: dict, p: int) -> float:
    """页起点字节的精确字节比例 (跟掌阅自己算 readPercent 的口径一致)。"""
    T = sc["src_total_bytes"]
    if T <= 0:
        return 0.0
    return bytes_of_page(sc, p) / T


def scroll_id_for_pct(sc: dict, pct01: float) -> str:
    """掌阅 pct → Kavita 页内 xpath (BookScrollId)。

    页粒度 = 1/N (狐 N=100 → 1%), 但 Kavita 的 BookScrollId 能在**页内**
    再定位到元素。掌阅自己的 f3 是字节偏移, 换算到当前页内偏移后取"起点
    不晚于该偏移的最深元素", 续读就会落在同一段落, 而不是页首。

    安全: Kavita 前端 scrollTo() 找不到元素只 console.warn, 不抛错 —
    所以写错了最多是不滚动, 不会像坏 NCX 那样整章打不开。这里仍在写之前
    做一次反解自检。
    """
    from . import zpct
    n = len(sc["pages"])
    T = sc["src_total_bytes"]
    if n <= 0 or T <= 0:
        return ""
    g = int(round(max(0.0, min(1.0, pct01)) * T))
    p = page_of_bytes(sc, g)
    inner = max(0, g - sc["pages"][p]["s"])
    path = sc.get("library_path") or ""
    if not path or not Path(path).exists():
        return ""
    try:
        di = zpct.build_doc_index(path, p)
    except (OSError, zipfile.BadZipFile, KeyError):
        return ""
    if di is None:
        return ""
    xp = di.xpath_of_offset(inner)
    if not xp or xp == "//body":
        return ""
    if di.offset_of_xpath(xp) > inner:      # 自检: 反解必须不晚于目标偏移
        return ""
    return xp


def edition_path(sc: dict) -> str:
    """重排版 epub 的真实位置。

    sidecar 的 `edition` 字段是构建时的临时路径 (`*.kptmp.epub`), 换库后就
    失效了 —— 所以优先用 apply_edition 补写的 `library_path`, 再退回
    ZY_EPUB_DIR/<书名>.epub。
    """
    cands = [sc.get("library_path") or ""]
    ed = sc.get("edition") or ""
    if ed and not ed.endswith(".kptmp.epub"):
        cands.append(ed)
    name = sc.get("book_name") or ""
    if name:
        cands.append(str(Path("/vol2/1001/dav/Books/掌阅") / f"{name}.epub"))
    for c in cands:
        if c and Path(c).exists():
            return c
    return ""


def _xpath_steps(xpath: str) -> list[tuple[str, int]] | None:
    x = re.sub(r"^/+", "", (xpath or "").strip())
    if not x:
        return None
    out: list[tuple[str, int]] = []
    for part in x.split("/"):
        m = re.match(r"^([A-Za-z0-9_:.-]+)(?:\[(\d+)\])?$", part)
        if not m:
            return None
        out.append((m.group(1).lower(), int(m.group(2) or 1)))
    return out


def fraction_of_scroll_id(sc: dict, page_index: int, xpath: str) -> float | None:
    """Kavita 的 BookScrollId (如 `//body/div[2]/p[30]`) 在**该页内**的相对位置。

    这是页内精度的来源: Kavita 只把"读到第几页"写进 PagesRead, 页内位置只
    落在 BookScrollId 里。600 页的剑来每页 70KB ≈ 2.3 万字, 不还原页内偏移
    的话, 读一小时掌阅侧的 pct 一格都不动 (2026-10-23 用户实测)。

    算法: 纯字节扫描 (不依赖 lxml —— 由 systemd/root 起的服务读不到
    ~/.local 里的 lxml, 所以这里自己走标签) 找到元素在本页的起始字节,
    比例 = (元素字节 - body 起点) / (body 长度)。页是从原书字节片切出来的,
    所以字节比例就是原书字节比例, 误差只来自切页时补的外壳标签。

    返回 None = xpath 在这页里走不通 (读者换页后旧的 scrollId 可能失效)。
    """
    steps = _xpath_steps(xpath)
    if not steps:
        return None
    path = edition_path(sc)
    if not path:
        return None
    files = sc.get("page_files") or []
    if not (0 <= page_index < len(files)):
        return None
    try:
        with zipfile.ZipFile(path) as z:
            raw = z.read(files[page_index])
    except (OSError, KeyError, zipfile.BadZipFile):
        return None
    try:
        return _byte_fraction(raw, steps)
    except Exception:  # noqa: BLE001 — 解析不了就当没有页内信息, 退回页起点
        return None


# 空元素 (不用闭合标签)
_VOID_TAGS = {b"area", b"base", b"br", b"col", b"embed", b"hr", b"img", b"input",
              b"link", b"meta", b"param", b"source", b"track", b"wbr"}
_TAG_RE = re.compile(rb"<(/?)([A-Za-z][A-Za-z0-9:_-]*)")
_BODY_RE = re.compile(rb"<body[^>]*>")


def _byte_fraction(raw: bytes, steps: list[tuple[str, int]]) -> float | None:
    """xpath 元素在整页字节里的相对位置 (0-1), 纯字节扫描。"""
    want = [(t.encode() if isinstance(t, str) else t, i) for t, i in steps]
    if want and want[0][0] == b"html":
        want = want[1:]
    if not want:
        return None
    low = raw.lower()
    m = _BODY_RE.search(low)
    if not m:
        return None
    b0 = m.end()
    b1 = low.rfind(b"</body")
    if b1 < 0:
        b1 = len(raw)
    if b1 <= b0:
        return None
    off = _find_tag_offset(raw, want)
    if off is None or off < b0:
        return None
    return max(0.0, min(1.0, (off - b0) / (b1 - b0)))


def _find_tag_offset(raw: bytes, steps: list[tuple[bytes, int]]) -> int | None:
    """按 xpath 找元素起始字节。steps = [(tag, 第几个同名兄弟), ...]"""
    pos = 0
    n = len(raw)
    stack: list[dict] = [{"tag": b"#root", "occ": 1, "counts": {}}]
    while pos < n:
        lt = raw.find(b"<", pos)
        if lt < 0:
            break
        if raw.startswith(b"<!--", lt):
            end = raw.find(b"-->", lt + 4)
            pos = (end + 3) if end >= 0 else n
            continue
        if raw.startswith(b"<!", lt) or raw.startswith(b"<?", lt):
            gt = raw.find(b">", lt)
            pos = (gt + 1) if gt >= 0 else n
            continue
        m = _TAG_RE.match(raw, lt)
        if not m:
            pos = lt + 1
            continue
        tag = m.group(2).lower()
        gt = raw.find(b">", m.end())
        if gt < 0:
            break
        if m.group(1) == b"/":                      # 闭合标签: 弹栈
            for k in range(len(stack) - 1, 0, -1):
                if stack[k]["tag"] == tag:
                    del stack[k:]
                    break
            pos = gt + 1
            continue
        selfclose = raw[gt - 1:gt] == b"/"
        parent = stack[-1]
        occ = parent["counts"].get(tag, 0) + 1
        parent["counts"][tag] = occ
        cand = stack[1:] + [{"tag": tag, "occ": occ}]
        if cand and cand[0]["tag"] == b"html" and want_first_not_html(steps):
            cand = cand[1:]
        if len(cand) == len(steps) and all(
                cand[i]["tag"] == steps[i][0] and cand[i]["occ"] == steps[i][1]
                for i in range(len(steps))):
            return lt
        if tag not in _VOID_TAGS and not selfclose:
            stack.append({"tag": tag, "occ": occ, "counts": {}})
        pos = gt + 1
    return None


def want_first_not_html(steps: list[tuple[bytes, int]]) -> bool:
    return not (steps and steps[0][0] == b"html")


def bytes_of_scroll_id(sc: dict, page_index: int, xpath: str) -> int | None:
    """Kavita (PagesRead, BookScrollId) → 原书全局字节位置 (页内精确)。"""
    pages = sc.get("pages") or []
    if not pages:
        return None
    p = max(0, min(page_index, len(pages) - 1))
    pg = pages[p]
    frac = fraction_of_scroll_id(sc, p, xpath)
    if frac is None:
        return None
    return int(pg["s"] + frac * (pg["e"] - pg["s"]))


def doc_locator(sc: dict) -> list[tuple[int, int, str]]:
    """[(全局起点, 全局终点, zip 路径)] 按原书 spine 顺序。"""
    return [(d["gs"], d["gs"] + d["size"], d["path"]) for d in sc["docs"]]


def geometry_from_sidecar(sc: dict):
    """sidecar → zpct.BookGeometry (不用再读 epub 原文件)。

    push 侧要用掌阅的字节口径算 readPercent/readPosition, 而库里的 epub
    已经被换成了重排版 —— 原书几何只能从 sidecar 里取。

    catalog_count 是**原书**的目录章数 (掌阅 totalCount 的口径, 见
    notes/04-totalcount-semantics.md)。老 sidecar 没这一栏, 这里按 src
    现算一次并回写, 免得每轮 push 都重新解 zip。
    """
    from . import zpct
    n = int(sc.get("catalog_count") or 0)
    if n <= 0:
        n = _src_catalog_count(sc.get("src"))
        if n > 0:
            sc["catalog_count"] = n
            try:
                save(sc, sc.get("series_id") or 0)
            except Exception:
                pass                    # 只读场景写不进去就下次再算
    return zpct.BookGeometry(spine=[d["path"] for d in sc["docs"]],
                             sizes=[d["size"] for d in sc["docs"]],
                             name="", catalog=n)


def _src_catalog_count(src: str | Path | None) -> int:
    """原书目录章数; src 缺失/损坏返 0。"""
    if not src:
        return 0
    p = Path(str(src))
    if not p.is_file():
        return 0
    from . import zpct
    return zpct.catalog_count(p)


def is_edition(path: str | Path) -> bool:
    """判断一个 epub 是不是我们生成的重排版 (含 kp/pNNNN.xhtml)。"""
    try:
        with zipfile.ZipFile(path) as z:
            return any(re.search(r"/kp/p\d{4}\.xhtml$", n) for n in z.namelist())
    except (OSError, zipfile.BadZipFile):
        return False


def validate_edition(path: str | Path) -> list[str]:
    """自检: 站内所有链接 (manifest / spine / ncx / nav / 正文) 必须落到真实文件。

    2026-10-23 事故: 按字节切页这一版第一次构建时, NCX 的 base 目录算错,
    `<content src="p0001.xhtml"/>` 少了 `kp/` 前缀 → Kavita 用 VersOne.Epub
    解析目录时抛 "content source not found in EPUB manifest" → 整本书
    "继续阅读" 直接报错跳回详情页。文件看着都全, 只有链接是错的 —— 所以
    校验必须在**换库之前**跑, 而不是靠人肉打开试。
    """
    from posixpath import normpath
    from xml.etree import ElementTree as ET
    probs: list[str] = []
    try:
        z = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as e:
        return [f"打不开 zip: {e}"]
    with z:
        names = set(z.namelist())
        opfs = [n for n in names if n.lower().endswith(".opf")]
        if not opfs:
            return ["没有 opf"]
        opf = opfs[0]
        opf_dir = opf.rsplit("/", 1)[0] + "/" if "/" in opf else ""
        raw = z.read(opf).decode("utf-8", "replace")
        items = dict(re.findall(r'<item\b[^>]*\bid="([^"]+)"[^>]*\bhref="([^"]+)"', raw))
        items.update({k: v for v, k in re.findall(
            r'<item\b[^>]*\bhref="([^"]+)"[^>]*\bid="([^"]+)"', raw)})
        spine = re.findall(r'<itemref\b[^>]*\bidref="([^"]+)"', raw)
        for i in spine:
            href = items.get(i)
            if href is None:
                probs.append(f"spine idref={i} 不在 manifest")
            elif normpath(opf_dir + href) not in names:
                probs.append(f"spine {i} → {opf_dir + href} 不存在")
        for k, v in items.items():
            if re.match(r"^[a-z]+:", v):
                continue
            if normpath(opf_dir + v) not in names:
                probs.append(f"manifest {k} → {opf_dir + v} 不存在")
        # ncx / nav / 正文 里的内部链接
        link_re = re.compile(r'(?:href|src|xlink:href)\s*=\s*"([^"#:]+)(?:#[^"]*)?"')
        for n in names:
            low = n.lower()
            if not low.endswith((".xhtml", ".html", ".ncx", ".opf")):
                continue
            t = z.read(n).decode("utf-8", "replace")
            base = n.rsplit("/", 1)[0] + "/" if "/" in n else ""
            for m in link_re.finditer(t):
                tgt = m.group(1).strip()
                if not tgt or tgt.startswith(("data:", "mailto:")):
                    continue
                full = normpath(base + tgt)
                if full not in names:
                    probs.append(f"{n} 链接 → {full} 不存在")
    return probs

