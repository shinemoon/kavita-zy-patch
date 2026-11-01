"""掌阅笔记 (划线 / 书签 / 批注) <-> Kavita 注解  双向同步

APK 依据见 notes/05-notes-time-apk-v2.md。要点:

* 身份: `uniquecheck = <bookid32>_<positionstart><positionend>` (划线)
                  `<bookid32>_<markpostion>`          (书签)
  bookid32 = 云书 64hex book_id 的**前 32 位大写** (2026-11-01 由小写统一; 云端对大小写敏感,
             小写与大写是两个独立身份, App 笔记本会显示成两条同名书)
* 位置: `<chapterId+1>_0_0$##3-0-<chapterId+1>-<章内字节偏移>-0##$`
  与 readPosition 同一坐标系 (见 zpct.py)
* 上传: POST /cloud/storage2/safe/uploadData?isCheck=1  (gzip)
  实测 (2026-10-24): bookbase + booknotes 可以成功插入云端, **但 delList 里
  不能同时出现同一个 uniquecheck** (会被判 `600001 authority is error`)
* 删除: POST /cloud/storage2/safe/delMarkOrNote (gzip)
  `delItems:[{type, bookid, uniquecheks:[...]}]` —— 键名少一个 c 是服务端的
  **划线用 type=2** 实测 (type=1 无效; APK 里 getIdeaType() 给 1, 与服务端不一致)
* 下载: POST /cloud/storage2/safe/downloadDataV2 (gzip)
  实测 2026-10-24 全部返 `{"status":0,"msg":"downloadDataProcessNew finished!"}`
  **没有 res** —— 详见 doc §6, 目前按"服务端没给数据"上报, 不静默

Kavita 侧没有注解写接口 (POST /api/Annotation 404), 只能直写 sqlite。
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import html
import io
import json
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import kpages, mapping, zpct
from .config import (BRIDGE_ROOT, KAVITA_DB, KAVITA_USER_ID, STATE_DB,
                     ensure_dirs, load_zy_auth, zy_query_params)

NOTE_BASE = "https://icloud.zhangyue.com/cloud/storage2/safe"
EMPTY_VER_MD5 = "670b14728ad9902aecba32e22fa4f6bd"   # APK hg/e.f70151f
DEFAULT_COLOR = -36352


# --------------------------------------------------------------------------
# 身份 / 位置
# --------------------------------------------------------------------------

def kb32(zy_book_id: str) -> str:
    """云书 book_id (64hex) -> 笔记服务用的 32hex **大写**。

    2026-11-01 改: 原来用小写, 结果云端同一本书存在**两个身份**
    (小写 20 条笔记 / 大写 1 条笔记), App 的「笔记本」列表里显示成两条同名的书。
    掌阅自己的本地书身份 `mLocalBookFileMd5` = `core.getFileMD5(f).toUpperCase()`
    (of/a.java:1670), 进度上报也用大写, 所以统一大写。
    """
    return (zy_book_id or "").strip().split("_")[0][:32].upper()


def norm_key(key: str) -> str:
    """uniquecheck 归一: 前 32 位 bookid 大小写混用过, 位置段全是数字/符号, 整串大写即可。"""
    return (key or "").strip().upper()


def note_key(book32: str, pos_s: str, pos_e: str) -> str:
    return f"{book32}_{pos_s}{pos_e}"


def mark_key(book32: str, pos: str) -> str:
    return f"{book32}_{pos}"


def parse_chapter_offset(pos: str) -> tuple[int, int] | None:
    """掌阅 position -> (chapterId, 章内字节偏移)。"""
    d = zpct.parse_position(pos)
    if not d or d.get("chapter_id") is None:
        return None
    try:
        return int(d["chapter_id"]), int(d.get("f3") or 0)
    except (TypeError, ValueError):
        return None


_POS_RE = re.compile(r"(\d+_0_0\$##\d+-\d+-\d+-\d+-\d+##\$)")
_TAG_RE = re.compile(r"<[^>]+>")
_doc_text_cache: dict[tuple[str, int], bytes | None] = {}


def _chapter_raw(epub_path: str, chapter_id: int) -> bytes | None:
    """原书里第 chapterId 章的字节 (spine[chapterId+1])，带缓存。"""
    key = (epub_path, chapter_id)
    if key in _doc_text_cache:
        return _doc_text_cache[key]
    raw = None
    try:
        import zipfile as _zip
        idx = zpct.build_doc_index(epub_path, chapter_id + 1)
        if idx is not None:
            raw = idx.raw
    except Exception:
        raw = None
    _doc_text_cache[key] = raw
    return raw


def _utf8_char_len(raw: bytes, off: int) -> int:
    """`raw[off:]` 开头那个 UTF-8 字符占几个字节 (不在字符边界上 / 越界 → 0)。"""
    if raw is None or off < 0 or off >= len(raw):
        return 0
    b = raw[off]
    if b < 0x80:
        n = 1
    elif b < 0xC0:          # 续字节: off 落在字符中间
        return 0
    elif b < 0xE0:
        n = 2
    elif b < 0xF0:
        n = 3
    elif b < 0xF8:
        n = 4
    else:
        return 0
    if off + n > len(raw):
        return 0
    return n


def text_of_span(epub_path: str, pos_s: str, pos_e: str = "",
                 *, end_inclusive: bool = True) -> str:
    """掌阅位置区间 → 划线正文。

    `downloadDataV2` 拿不到正文时 (2026-10-24 起一直返 status:0 无 res)，
    用 `getUserDataVer` 的位置 + 原书字节自己切。原文即权威，比下载还准。

    **`positionend` 的口径 (2026-11-01 实测 22 条真实划线)**: App 写的是
    「**最后一个字的起始字节**」(闭区间)，不是"最后一个字之后"。按开区间切
    每条都会少最后一个字 (`不可` / `站了许` / `温` 这种截断)。所以默认
    `end_inclusive=True` → 切完再补一个字。

    例外: **我们自己 push 上去的划线** (`zy_note_link.source='kv'`) 当初就是按
    开区间写的，读回来必须 `end_inclusive=False`，否则会多出一个字。
    """
    a = parse_chapter_offset(pos_s)
    if a is None:
        return ""
    ch, start = a
    raw = _chapter_raw(epub_path, ch)
    if not raw:
        return ""
    b = parse_chapter_offset(pos_e) if pos_e else None
    end = b[1] if (b and b[0] == ch) else start
    lo = max(0, min(start, len(raw)))
    hi = max(lo, min(end, len(raw)))
    if end_inclusive and hi > lo:
        n = _utf8_char_len(raw, hi)
        if n:
            cand = raw[hi:hi + n].decode("utf-8", "ignore")
            # 补出来的若是标签开头 / 空白，就当作"选中范围恰好收在字尾"
            if cand and cand != "<" and not cand.isspace():
                hi = min(len(raw), hi + n)
    if hi <= lo:
        # 零长度选择 (掌阅允许只点一下留标) → 取所在段落开头一段做上下文
        try:
            idx = zpct.build_doc_index(epub_path, ch + 1)
            hit = idx.element_at(lo) if idx else None
        except Exception:
            hit = None
        if not hit:
            return ""
        seg = hit[1]
    else:
        seg = raw[lo:hi].decode("utf-8", "replace")
    txt = html.unescape(_TAG_RE.sub("", seg))
    txt = re.sub(r"<[^>]*$", "", txt)          # element_at 截断会留半截标签
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt[:200]



def _split_positions(uniquecheck: str) -> list[str]:
    """uniquecheck 里嵌的两段位置 (划线) / 一段 (书签)。"""
    return _POS_RE.findall(uniquecheck or "")


def positions_payload(book32: str, epub_path: str,
                      *, exclusive_keys: set[str] | None = None) -> dict:
    """`getUserDataVer` → downloadDataV2 形状的 res (正文从原书切)。

    云端 `downloadDataV2` 现在是坏通道 (只回 status/msg, 无 res)，但
    `getUserDataVer` 稳定给出：每条的 uniquecheck (含起止字节位置) +
    marktime + delList。正文由原书按字节切出来，与云端一致。
    唯一拿不到的是「批注正文」(remark)，留空。

    `exclusive_keys`: 这些 key 的 `positionend` 是**开区间** (我们自己 push 上去的
    划线, 见 `text_of_span` 的口径说明)，切正文时不补最后一个字。
    """
    v = data_ver(book32)
    excl = {norm_key(k) for k in (exclusive_keys or set())}
    notes, marks = [], []
    for it in (v.get("booknotesVerList") or []):
        uc = str(it.get("uniquecheck") or "")
        poses = _split_positions(uc)
        if len(poses) < 2:
            continue
        notes.append({"uniquecheck": uc,
                      "positionstart": poses[0], "positionend": poses[1],
                      "summary": text_of_span(epub_path, poses[0], poses[1],
                                              end_inclusive=norm_key(uc) not in excl),
                      "remark": "", "marktime": int(it.get("marktime") or 0)})
    for it in (v.get("bookmarksVerList") or []):
        uc = str(it.get("uniquecheck") or "")
        poses = _split_positions(uc)
        if not poses:
            continue
        marks.append({"uniquecheck": uc, "markpostion": poses[0],
                      "marktime": int(it.get("marktime") or 0)})
    return {"booknotes": notes, "bookmarks": marks,
            "delList": [str(x) for x in (v.get("delList") or [])],
            "from_positions": True}


def pos_to_global(geo: zpct.BookGeometry, pos: str) -> int | None:
    co = parse_chapter_offset(pos)
    if co is None:
        return None
    ch, off = co
    return geo.prefix_bytes(ch) + max(0, off)


def global_to_pos(geo: zpct.BookGeometry, g: int) -> str:
    ch, off = geo.locate(g)
    return zpct.build_position(ch, off)


# --------------------------------------------------------------------------
# 掌阅 HTTP
# --------------------------------------------------------------------------

def _call(path: str, body: dict | None = None, extra: dict | None = None,
          timeout: int = 30) -> dict:
    auth = load_zy_auth()
    qs = urllib.parse.urlencode(zy_query_params(auth, extra or {}))
    data = None
    if body is not None:
        data = gzip.compress(json.dumps(body, ensure_ascii=False).encode("utf-8"))
    # path 自己可能带查询串 (如 "/uploadData?isCheck=1"), 这时必须用 & 续接,
    # 否则拼出 "...?isCheck=1?usr=..." -> 服务端只看到 isCheck="1?usr=...",
    # usr/zysid 全丢 -> 一律回 600001 authority is error (2026-10-24 踩过)
    sep = "&" if "?" in path else "?"
    req = urllib.request.Request(f"{NOTE_BASE}{path}{sep}{qs}", data=data,
                                 method="POST" if data else "GET")
    if data:
        req.add_header("Content-Type", "application/octet-stream")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            raise RuntimeError(f"掌阅 {path} HTTP {e.code}: {raw[:200]!r}")
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8", "replace"))


def notebooks() -> list[dict]:
    """getUserNotebooks: 每本有笔记的书 + 计数 + 进度。"""
    d = _call("/getUserNotebooks")
    return d.get("res") or []


def data_ver(book32: str) -> dict:
    """getUserDataVer: 云端三组 {uniquecheck, marktime} 版本表。"""
    d = _call("/getUserDataVer", extra={"bookids": book32})
    want = kb32(book32)
    for row in (d.get("res") or []):
        if kb32(str(row.get("bookid", ""))) == want:
            return row
    return {}


def download(book32: str) -> dict:
    """downloadDataV2。返回 res 字典 (可能为空 = 服务端没数据)。"""
    body = {"usr": load_zy_auth()["usr"], "rgt": load_zy_auth()["zysid"],
            "versionlist": {book32: {
                "booknotesmd5": EMPTY_VER_MD5,
                "bookScaleNotesMd5": EMPTY_VER_MD5,
                "bookmarksmd5": EMPTY_VER_MD5,
                "booknotesVerList": [], "bookScaleNotesVerList": [],
                "bookmarksVerList": []}}}
    d = _call("/downloadDataV2", body)
    return d.get("res") or {}


def upload(book_payload: dict) -> dict:
    """uploadData?isCheck=1 —— 推一本书的全部增量。"""
    auth = load_zy_auth()
    return _call("/uploadData?isCheck=1",
                 {"usr": auth["usr"], "rgt": auth["zysid"], "books": [book_payload]})


def delete_items(book32: str, keys: Iterable[str], type_: int = 2) -> dict:
    """delMarkOrNote。划线 type=2 (实测), 书签 type=1。"""
    auth = load_zy_auth()
    items = [{"type": type_, "bookid": book32, "uniquecheks": list(keys)}]
    return _call("/delMarkOrNote", {"usr": auth["usr"], "rgt": auth["zysid"],
                                    "delItems": items})


# --------------------------------------------------------------------------
# 本地 link 表 (幂等 / 双向防回灌)
# --------------------------------------------------------------------------

def _conn() -> sqlite3.Connection:
    ensure_dirs()
    c = sqlite3.connect(STATE_DB)
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE IF NOT EXISTS zy_note_link (
            kind TEXT NOT NULL,                -- note | bookmark
            zy_book_id TEXT NOT NULL,          -- 32hex
            zy_key TEXT NOT NULL,              -- uniquecheck
            kavita_series_id INTEGER NOT NULL,
            kavita_chapter_id INTEGER NOT NULL,
            kavita_annotation_id INTEGER,      -- AppUserAnnotation.Id / AppUserBookmark.Id
            marktime INTEGER DEFAULT 0,
            source TEXT DEFAULT 'zy',          -- zy | kv
            sig TEXT DEFAULT '',               -- 内容指纹 (summary+remark+位置), 变了才重推
            updated_utc TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (kind, zy_book_id, zy_key)
        );
        CREATE TABLE IF NOT EXISTS zy_time_queue (
            zy_book_id TEXT NOT NULL,          -- 32hex (书身份 = 文件内容 MD5)
            day TEXT NOT NULL,                 -- 本地日 yyyy-MM-dd
            kavita_series_id INTEGER NOT NULL,
            bitmap TEXT NOT NULL,              -- base64(180B 分钟位图), 与 APK mData 同构
            minutes INTEGER DEFAULT 0,
            read_percent REAL DEFAULT 0,
            tries INTEGER DEFAULT 0,
            first_added TEXT DEFAULT (datetime('now')),
            last_try TEXT,
            PRIMARY KEY (zy_book_id, day)
        );
        CREATE TABLE IF NOT EXISTS zy_time_link (
            day TEXT NOT NULL,
            zy_book_id TEXT NOT NULL,
            kavita_series_id INTEGER NOT NULL,
            minutes INTEGER DEFAULT 0,
            mode TEXT DEFAULT '',              -- exact | alloc
            session_id INTEGER,
            updated_utc TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (day, zy_book_id)
        );
    """)
    # 迁移: 老库没有 sig 列 (2026-10-25 加, 供"内容变了才重推")
    cols = {r[1] for r in c.execute("PRAGMA table_info(zy_note_link)")}
    if "sig" not in cols:
        try:
            c.execute("ALTER TABLE zy_note_link ADD COLUMN sig TEXT DEFAULT ''")
            c.commit()
        except sqlite3.OperationalError:
            pass
    return c


def link_get(kind: str, book32: str, key: str) -> sqlite3.Row | None:
    c = _conn()
    r = c.execute("SELECT * FROM zy_note_link WHERE kind=? AND zy_book_id=? AND zy_key=?",
                  (kind, book32, key)).fetchone()
    c.close()
    return r


def link_put(kind: str, book32: str, key: str, *, series_id: int, chapter_id: int,
             annotation_id: int | None, marktime: int = 0, source: str = "zy",
             sig: str = "") -> None:
    c = _conn()
    c.execute("""
        INSERT INTO zy_note_link (kind, zy_book_id, zy_key, kavita_series_id,
                                  kavita_chapter_id, kavita_annotation_id, marktime,
                                  source, sig, updated_utc)
        VALUES (?,?,?,?,?,?,?,?,?,datetime('now'))
        ON CONFLICT(kind, zy_book_id, zy_key) DO UPDATE SET
            kavita_series_id=excluded.kavita_series_id,
            kavita_chapter_id=excluded.kavita_chapter_id,
            kavita_annotation_id=excluded.kavita_annotation_id,
            marktime=excluded.marktime, source=excluded.source, sig=excluded.sig,
            updated_utc=datetime('now')
    """, (kind, book32, key, int(series_id), int(chapter_id),
          annotation_id, int(marktime or 0), source, sig or ""))
    c.commit()
    c.close()


def link_delete(kind: str, book32: str, key: str) -> None:
    c = _conn()
    c.execute("DELETE FROM zy_note_link WHERE kind=? AND zy_book_id=? AND zy_key=?",
              (kind, book32, key))
    c.commit()
    c.close()


def links_for_series(series_id: int, kind: str | None = None) -> list[sqlite3.Row]:
    c = _conn()
    if kind:
        rows = c.execute("SELECT * FROM zy_note_link WHERE kavita_series_id=? AND kind=?",
                         (series_id, kind)).fetchall()
    else:
        rows = c.execute("SELECT * FROM zy_note_link WHERE kavita_series_id=?",
                         (series_id,)).fetchall()
    c.close()
    return rows


# --------------------------------------------------------------------------
# 几何 / 页面
# --------------------------------------------------------------------------

def _orig_epub(link: mapping.BookLink) -> str:
    """算掌阅坐标要用**原书** (bridge/books), 不是库里的切片版。"""
    from .config import find_zy_epub
    p = find_zy_epub(link.zy_book_name, link.zy_book_id)
    if p:
        return p
    if link.epub_path and Path(link.epub_path).exists():
        return link.epub_path
    return ""


_geo_cache: dict[str, zpct.BookGeometry] = {}


def geometry(link: mapping.BookLink) -> zpct.BookGeometry | None:
    p = _orig_epub(link)
    if not p:
        return None
    if p not in _geo_cache:
        try:
            _geo_cache[p] = zpct.load_geometry(p, link.zy_book_name)
        except Exception:
            return None
    return _geo_cache[p]


def _page_file_raw(edition: str, page_files: list[str], page: int) -> bytes | None:
    if not edition or page < 0 or page >= len(page_files):
        return None
    try:
        with zipfile.ZipFile(edition) as z:
            return z.read(page_files[page])
    except (OSError, KeyError, zipfile.BadZipFile):
        return None


def _xpath_in_page(raw: bytes, text: str) -> str | None:
    """在页 XHTML 里按**文本**定位, 返回相对 xpath (//body/...)。"""
    if not raw or not text:
        return None
    needle = text.strip().encode("utf-8")
    if len(needle) < 4:
        return None
    idx = -1
    for cut in (needle, needle[: max(6, len(needle) // 2)]):
        idx = raw.find(cut)
        if idx >= 0:
            break
    if idx < 0:
        return None
    di = zpct.DocIndex(raw)
    return di.xpath_of_offset(idx)


def _doc_raw(epub: str, spine_index: int) -> bytes | None:
    geo = geometry_by_path(epub)
    if geo is None or not (0 <= spine_index < len(geo.spine)):
        return None
    try:
        with zipfile.ZipFile(epub) as z:
            return z.read(geo.spine[spine_index])
    except (OSError, KeyError, zipfile.BadZipFile):
        return None


def _needle_from_raw(raw: bytes, offset: int, n: int = 12) -> str:
    """从原书文档的字节位置取一小段正文当锚 (给页内 xpath 反查用)。

    短划线 (1-2 字, 如单字「暌」) 没法直接当检索锚 (`_xpath_in_page` 要求 ≥4 字节);
    结构 xpath 又可能因为 DocIndex 与 Kavita 的 HTML 归一化差异而偏几段。这里从
    **原书**同一位置往后取十几字正文 —— 内容页里必有一份一样的文本。
    """
    try:
        chunk = raw[max(0, int(offset)): int(offset) + 800].decode("utf-8", "replace")
    except Exception:                       # noqa: BLE001
        return ""
    chunk = re.sub(r"<[^>]*>", "", chunk)
    chunk = re.sub(r"\s+", "", chunk)
    return chunk[:n]


def _locate(geo, epub: str, sc: dict | None, g: int, summary: str) -> tuple[int, str, bytes, int]:
    """全书字节 -> (Kavita 页号, 页内 xpath, 该页原始字节, 页内偏移)。

    **页号是 0 基** —— Kavita 的 `book-page?page=N` 与 `AppUserAnnotation.PageNumber`
    用的是同一条序号 (spine / 切片页下标)。早先这里返回 1 基, 高亮整体错一页。
    xpath 是**页内相对路径** (`//body/div[1]/p[19]`), 不能带 `id("pNNNN.xhtml")` 前缀 ——
    Kavita 逐页渲染, 页文档里根本没有那个 id, 解析失败就整条不高亮。
    """
    if sc:
        ed = kpages.edition_path(sc)
        ch, off = geo.locate(g)
        anchor = _needle_from_raw(_doc_raw(epub, ch + 1) or b"", off)
        page, rel = locate_note(sc, ed, g, summary, sc["page_files"], anchor)
        raw = _page_file_raw(ed, sc["page_files"], page) or b""
        inner = max(0, g - sc["pages"][page]["s"])
        return page, rel, raw, inner
    ch, off = geo.locate(g)
    spine = ch + 1
    raw = _doc_raw(epub, spine) or b""
    rel = (_xpath_in_page(raw, summary) if summary else None) \
        or (zpct.DocIndex(raw).xpath_of_offset(off) if raw else None) or "//body"
    return spine, rel, raw, off


def _kv_xpath(geo, page: int, rel: str, sliced: bool) -> str:
    """把页内相对 xpath 变成 Kavita 能解析的形式。

    - **切片版** (kpages): Kavita 把每页包成 `<div class=" ">` (没有 id 属性),
      所以只能用页内相对路径 (`//body/div[1]/p[19]`) —— 带 `id(...)` 前缀反而解析不到。
    - **原书直接进库**: Kavita 的包裹 div 带 `id="<文件名>"`, 它自己的注解也存
      `id("chapter04.xhtml")/p[3]/span[1]` 这种, 所以这边必须带文件名前缀。
    """
    if sliced:
        return rel
    name = Path(geo.spine[min(page, len(geo.spine) - 1)]).name
    return f'id("{name}"){rel[1:] if rel.startswith("/") else rel}'


def _end_xpath(geo, epub: str, sc: dict | None, note: dict, g_start: int,
               page: int, xp: str) -> str:
    """划线结束位置的页内 xpath —— Kavita 靠 XPath + EndingXPath 划高亮范围。

    实测 (2026-11-01): 只给起点时, 跨段的选择整条不高亮 (Kavita 在单个节点里找不到
    完整 SelectedText 就跳过); 把 EndingXPath 指到结束段落, 两段会各画一段高亮。
    跨页的选择退回起点 (Kavita 只按 PageNumber 那一页注入, 跨页本来就画不出来)。
    """
    try:
        g_end = pos_to_global(geo, note.get("positionend") or "")
        if g_end is None or g_end <= g_start:
            return xp
        summary = (note.get("summary") or "").strip()
        tail = summary[-8:] if len(summary) >= 8 else ""
        epage, erel, _raw, _inner = _locate(geo, epub, sc, g_end, tail)
        return erel if epage == page else xp
    except Exception:                       # noqa: BLE001 — 失败就用起点, 不影响写入
        return xp


def _chapter_id_for(link: mapping.BookLink) -> int:
    if link.kavita_chapter_id and link.kavita_chapter_id > 0:
        return int(link.kavita_chapter_id)
    from . import kavita_db
    chs = kavita_db.list_chapters(link.kavita_series_id)
    return int(chs[0]["ChId"]) if chs else 0


def locate_note(sc: dict, edition: str, g: int, summary: str,
                page_files: list[str], anchor: str = "") -> tuple[int, str]:
    """字节位置 -> (页号 0 基, 页内 xpath)。

    先用字节位置定页; 若该页找不到 summary 文本, 再试相邻页 —— 原书与设备那份
    可能有字节差 (剑来本地 41.9MB vs 云端 30.7MB), 文本才是最终锚。
    `anchor` 是原文该位置的正文片段, 用于短划线 (summary 太短) 兜底。

    页号 0 基: 与 Kavita `book-page?page=N` / `PageNumber` 同一套序号 (page_files 下标)。
    """
    page = kpages.page_of_bytes(sc, g)
    cands = [page]
    for d in (1, -1, 2, -2):
        if 0 <= page + d < len(page_files):
            cands.append(page + d)
    for needle in (summary, anchor):
        if not needle:
            continue
        for p in cands:
            raw = _page_file_raw(edition, page_files, p)
            if not raw:
                continue
            xp = _xpath_in_page(raw, needle)
            if xp:
                return p, xp
    p = page
    raw = _page_file_raw(edition, page_files, p) or b""
    di = zpct.DocIndex(raw)
    inner = max(0, g - sc["pages"][p]["s"])
    return p, (di.xpath_of_offset(inner) or "//body")


# --------------------------------------------------------------------------
# Kavita 注解读写
# --------------------------------------------------------------------------

def _kv_read_conn() -> sqlite3.Connection:
    import tempfile
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    src = sqlite3.connect(f"file:{KAVITA_DB}?mode=ro", uri=True)
    dst = sqlite3.connect(tmp)
    src.backup(dst)
    src.close()
    dst.row_factory = sqlite3.Row
    return dst


def _series_notes_for(link: mapping.BookLink, loader) -> list[dict]:
    """取本次要同步的注解/书签, **按卷过滤**。

    `kavita_annotations/bookmarks(series)` 返回整个系列的; 多卷系列 (路邊的藤井 3 卷)
    如果不过滤, 卷02 的一条书签会被当成三卷各一条推到掌阅的三个 bookid 上。
    """
    rows = loader(link.kavita_series_id)
    chid = int(getattr(link, "kavita_chapter_id", 0) or 0)
    if chid <= 0:
        return rows
    return [r for r in rows if int(r.get("ChapterId") or 0) == chid]


def kavita_annotations(series_id: int) -> list[dict]:
    c = _kv_read_conn()
    rows = c.execute("""SELECT * FROM AppUserAnnotation
                        WHERE AppUserId=? AND SeriesId=? ORDER BY Id""",
                     (KAVITA_USER_ID, series_id)).fetchall()
    c.close()
    return [dict(r) for r in rows]


def kavita_bookmarks(series_id: int) -> list[dict]:
    c = _kv_read_conn()
    rows = c.execute("""SELECT * FROM AppUserBookmark
                        WHERE AppUserId=? AND SeriesId=? ORDER BY Id""",
                     (KAVITA_USER_ID, series_id)).fetchall()
    c.close()
    return [dict(r) for r in rows]


def _now_pair() -> tuple[str, str]:
    now = datetime.now()
    utc = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%d %H:%M:%S.%f"), utc.strftime("%Y-%m-%d %H:%M:%S.%f")


def kavita_upsert_note(*, series_id: int, chapter_id: int, page: int,
                       xpath: str, end_xpath: str, text: str, context: str,
                       comment: str, created: str | None = None,
                       ann_id: int | None = None) -> int:
    """写一条划线 (annotation)。ann_id 给了就 UPDATE, 否则 INSERT。"""
    local, utc = _now_pair()
    created = created or local
    created_utc = utc if not created else created
    d = sqlite3.connect(KAVITA_DB)
    cur = d.cursor()
    vol = cur.execute("""SELECT c.VolumeId, v.SeriesId, s.LibraryId, c.Title
                         FROM Chapter c JOIN Volume v ON v.Id=c.VolumeId
                         JOIN Series s ON s.Id=v.SeriesId WHERE c.Id=?""",
                      (chapter_id,)).fetchone()
    vol_id, ser_id, lib_id, ch_title = (vol or (0, series_id, 1, ""))
    if ann_id:
        cur.execute("""UPDATE AppUserAnnotation SET XPath=?, EndingXPath=?,
                       SelectedText=?, Context=?, CommentPlainText=?, CommentHtml=?,
                       PageNumber=?, ChapterTitle=?, LastModified=?, LastModifiedUtc=?
                       WHERE Id=?""",
                    (xpath, end_xpath, text, context, comment, "",
                     page, ch_title or "", local, utc, ann_id))
        new_id = ann_id
    else:
        cur.execute("""INSERT INTO AppUserAnnotation
            (AppUserId, ChapterId, ChapterTitle, Comment, CommentHtml,
             CommentPlainText, ContainsSpoiler, Context, Created, CreatedUtc,
             EndingXPath, HighlightCount, LastModified, LastModifiedUtc,
             LibraryId, Likes, PageNumber, SelectedSlotIndex, SelectedText,
             SeriesId, VolumeId, XPath)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (KAVITA_USER_ID, chapter_id, ch_title or "", "{}", "",
                     comment, 0, context, created, created_utc, end_xpath,
                     1, local, utc, lib_id or 1, "[]", page, 0, text,
                     ser_id or series_id, vol_id or series_id, xpath))
        new_id = int(cur.lastrowid)
    d.commit()
    d.close()
    return new_id


def kavita_refresh_note_text(ann_id: int, text: str) -> bool:
    """旧口径切出来的划线正文少最后一个字 → 就地补齐 SelectedText。

    只在「库里文本是本次文本的严格前缀，且只短 1-3 字」时改 —— 否则可能是
    用户自己在 Kavita 侧改过 / 两边本来就不同, 不动。返回是否改动。
    """
    if not ann_id or not text:
        return False
    d = sqlite3.connect(KAVITA_DB, timeout=15)
    try:
        row = d.execute("SELECT SelectedText FROM AppUserAnnotation WHERE Id=?",
                        (ann_id,)).fetchone()
        cur = (row[0] or "").strip() if row else ""
        if not cur or cur == text or not text.startswith(cur):
            return False
        if not 0 < len(text) - len(cur) <= 3:
            return False
        d.execute("UPDATE AppUserAnnotation SET SelectedText=? WHERE Id=?", (text, ann_id))
        d.commit()
        return True
    finally:
        d.close()


def kavita_upsert_bookmark(*, series_id: int, chapter_id: int, page: int,
                           xpath: str, bm_id: int | None = None) -> int:
    local, utc = _now_pair()
    d = sqlite3.connect(KAVITA_DB)
    cur = d.cursor()
    vol = cur.execute("""SELECT c.VolumeId, v.SeriesId, c.Title
                         FROM Chapter c JOIN Volume v ON v.Id=c.VolumeId
                         WHERE c.Id=?""", (chapter_id,)).fetchone()
    vol_id, ser_id, ch_title = (vol or (0, series_id, ""))
    if bm_id:
        cur.execute("""UPDATE AppUserBookmark SET Page=?, XPath=?, ChapterTitle=?,
                       LastModified=?, LastModifiedUtc=? WHERE Id=?""",
                    (page, xpath, ch_title or "", local, utc, bm_id))
        new_id = bm_id
    else:
        cur.execute("""INSERT INTO AppUserBookmark
            (AppUserId, ChapterId, ChapterTitle, Created, CreatedUtc, FileName,
             ImageOffset, LastModified, LastModifiedUtc, Page, SeriesId, VolumeId, XPath)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (KAVITA_USER_ID, chapter_id, ch_title or "", local, utc, "",
                     0, local, utc, page, ser_id or series_id, vol_id or series_id, xpath))
        new_id = int(cur.lastrowid)
    d.commit()
    d.close()
    return new_id


def kavita_delete_annotation(ann_id: int) -> None:
    d = sqlite3.connect(KAVITA_DB)
    d.execute("DELETE FROM AppUserAnnotation WHERE Id=?", (ann_id,))
    d.commit()
    d.close()


def kavita_delete_bookmark(bm_id: int) -> None:
    d = sqlite3.connect(KAVITA_DB)
    d.execute("DELETE FROM AppUserBookmark WHERE Id=?", (bm_id,))
    d.commit()
    d.close()


# --------------------------------------------------------------------------
# 掌阅 -> Kavita
# --------------------------------------------------------------------------

def _ctx_of(raw: bytes, di: zpct.DocIndex, off: int, span: int = 400) -> str:
    """取所在段落的上下文 (字节切片后解码)。"""
    try:
        txt = raw[max(0, off - span): off + span].decode("utf-8", "replace")
        return txt.strip()
    except Exception:
        return ""


def pull(series_ids: list[int] | None = None, *, dry_run: bool = False) -> dict:
    """掌阅 -> Kavita: 拉划线/书签, 写进 Kavita 注解表。"""
    report: dict[str, Any] = {"books": [], "added": 0, "updated": 0, "retitled": 0,
                              "deleted": 0, "skipped": 0, "empty": [], "errors": []}
    links = [l for l in mapping.all_links() if not series_ids or l.kavita_series_id in series_ids]
    nb_index = {}
    try:
        for row in notebooks():
            # 同一本书云端可能同时有大小写两个身份 (2026-11-01 之前的历史),
            # 归一后**大写优先** (掌阅自己的本地书身份就是大写)
            bid = kb32(str(row.get("bookid", "")))
            if bid and (bid not in nb_index
                        or str(row.get("bookid", ""))[:32].isupper()):
                nb_index[bid] = row
    except Exception as e:                       # 网络问题不该炸整个 pull
        report["errors"].append(f"getUserNotebooks: {e}")

    for link in links:
        book32 = kb32(link.zy_book_id)
        if not dry_run:
            try:                       # 云端可能又被 App 写了小写身份 -> 折回大写
                merged = consolidate_case(link)
                if merged.get("merged") or merged.get("merged_marks") or merged.get("error"):
                    report.setdefault("case_merged", []).append(
                        {"series": link.kavita_series_id, **merged})
            except Exception as e:     # noqa: BLE001
                report["errors"].append(f"consolidate {book32}: {e}")
        geo = geometry(link)
        epub = _orig_epub(link)
        if geo is None or not epub:
            report["books"].append({"series": link.kavita_series_id, "skip": "no_geometry"})
            continue
        idx_row = nb_index.get(book32)
        if idx_row is None:
            report["books"].append({"series": link.kavita_series_id, "skip": "not_in_index"})
            continue
        if not (idx_row.get("notenum") or idx_row.get("marknum")):
            report["books"].append({"series": link.kavita_series_id, "skip": "empty_index"})
            continue
        try:
            res = download(book32)
        except Exception as e:
            report["errors"].append(f"download {book32}: {e}")
            continue
        notes = res.get("booknotes") or []
        marks = res.get("bookmarks") or []
        dels = {norm_key(x) for x in (res.get("delList") or [])}
        if not res:
            # downloadDataV2 坏通道兜底: 用 getUserDataVer 的位置 + 原书切正文
            try:
                own = {norm_key(r["zy_key"]) for r in links_for_series(link.kavita_series_id, "note")
                       if (r["source"] or "") == "kv" and r["zy_key"]}
                res = positions_payload(book32, epub, exclusive_keys=own)
            except Exception as e:
                report["errors"].append(f"positions {book32}: {e}")
                res = {}
            notes = res.get("booknotes") or []
            marks = res.get("bookmarks") or []
            dels = {norm_key(x) for x in (res.get("delList") or [])}
            if notes or marks:
                report.setdefault("via_positions", []).append(link.kavita_series_id)
            else:
                report["empty"].append({"series": link.kavita_series_id,
                                        "cloud_notes": idx_row.get("notenum"),
                                        "cloud_marks": idx_row.get("marknum")})
                continue

        # 库里是"切片版" → 走 sidecar 页映射; 原书直接进库 (图片书) → 页 = spine 下标
        sc = kpages.load(link.kavita_series_id)
        sliced = bool(sc)
        chapter_id = _chapter_id_for(link)
        edition = kpages.edition_path(sc) if sc else epub
        page_files = (sc.get("page_files") or []) if sc else []
        page_count = (len(sc["pages"]) if sc else len(geo.spine))
        report.setdefault("map_mode", {})[link.kavita_series_id] = (
            "slice" if sliced else "spine")
        # --- 划线
        for n in notes:
            key = norm_key(n.get("uniquecheck"))
            if not key or key in dels:
                continue
            g = pos_to_global(geo, n.get("positionstart", ""))
            if g is None:
                report["skipped"] += 1
                continue
            page, rel, raw, inner = _locate(geo, epub, sc, g, (n.get("summary") or "").strip())
            xp = _kv_xpath(geo, page, rel, sliced)     # 切片版用相对路径, 原书带文件名 id
            ex = _kv_xpath(geo, page, _end_xpath(geo, epub, sc, n, g, page, rel), sliced)
            existing = link_get("note", book32, key)
            if existing and int(existing["marktime"] or 0) == int(n.get("marktime") or 0):
                # 正文口径修过一次 (2026-11-01: 少最后一个字), 已同步过的就补文本
                if not dry_run and kavita_refresh_note_text(
                        int(existing["kavita_annotation_id"] or 0),
                        (n.get("summary") or "").strip()):
                    report["retitled"] += 1
                else:
                    report["skipped"] += 1
                continue
            if dry_run:
                report["added" if not existing else "updated"] += 1
                continue
            ann_id = kavita_upsert_note(
                series_id=link.kavita_series_id, chapter_id=chapter_id, page=page,
                xpath=xp, end_xpath=ex, text=(n.get("summary") or "").strip(),
                context=_ctx_of(raw, zpct.DocIndex(raw), inner),
                comment=(n.get("remark") or "").strip(),
                ann_id=int(existing["kavita_annotation_id"]) if existing and existing["kavita_annotation_id"] else None)
            link_put("note", book32, key, series_id=link.kavita_series_id,
                     chapter_id=chapter_id, annotation_id=ann_id,
                     marktime=int(n.get("marktime") or 0), source="zy")
            report["added" if not existing else "updated"] += 1

        # --- 书签
        for m in marks:
            key = norm_key(m.get("uniquecheck"))
            if not key or key in dels:
                continue
            g = pos_to_global(geo, m.get("markpostion", ""))
            if g is None:
                report["skipped"] += 1
                continue
            page, rel, _raw, _inner = _locate(geo, epub, sc, g, "")
            xp = _kv_xpath(geo, page, rel, sliced)     # 同划线: 切片版相对, 原书带文件名 id
            existing = link_get("bookmark", book32, key)
            if existing and int(existing["marktime"] or 0) == int(m.get("marktime") or 0):
                report["skipped"] += 1
                continue
            if dry_run:
                report["added" if not existing else "updated"] += 1
                continue
            bm_id = kavita_upsert_bookmark(
                series_id=link.kavita_series_id, chapter_id=chapter_id, page=page,
                xpath=xp,
                bm_id=int(existing["kavita_annotation_id"]) if existing and existing["kavita_annotation_id"] else None)
            link_put("bookmark", book32, key, series_id=link.kavita_series_id,
                     chapter_id=chapter_id, annotation_id=bm_id,
                     marktime=int(m.get("marktime") or 0), source="zy")
            report["added" if not existing else "updated"] += 1

        # --- 云端删除传播
        for kind in ("note", "bookmark"):
            for row in links_for_series(link.kavita_series_id, kind):
                if row["zy_key"] not in dels:
                    continue
                if dry_run:
                    report["deleted"] += 1
                    continue
                if kind == "note":
                    kavita_delete_annotation(int(row["kavita_annotation_id"] or 0))
                else:
                    kavita_delete_bookmark(int(row["kavita_annotation_id"] or 0))
                link_delete(kind, book32, row["zy_key"])
                report["deleted"] += 1

        report["books"].append({"series": link.kavita_series_id,
                                "notes": len(notes), "bookmarks": len(marks)})
    return report


# --------------------------------------------------------------------------
# Kavita -> 掌阅
# --------------------------------------------------------------------------

def _find_in_book(epub_path: str, text: str) -> int | None:
    """在**原书**里按文本找全局字节偏移 (按 spine 顺序扫)。"""
    if not text:
        return None
    needle = text.strip().encode("utf-8")
    if len(needle) < 4:
        return None
    cands = [needle, needle[: max(8, len(needle) // 2)]]
    try:
        with zipfile.ZipFile(epub_path) as z:
            geo = geometry_by_path(epub_path)
            if geo is None:
                return None
            cum = 0
            for i, name in enumerate(geo.spine):
                raw = z.read(name)
                for c in cands:
                    idx = raw.find(c)
                    if idx >= 0:
                        return cum + idx
                cum += len(raw)
    except (OSError, KeyError, zipfile.BadZipFile):
        return None
    return None


_geo_path_cache: dict[str, zpct.BookGeometry] = {}


def geometry_by_path(path: str) -> zpct.BookGeometry | None:
    if path not in _geo_path_cache:
        try:
            _geo_path_cache[path] = zpct.load_geometry(path)
        except Exception:
            return None
    return _geo_path_cache[path]


# --------------------------------------------------------------------------
# 上传侧辅助 (照抄 APK: 云端版本表 / 条目字段 / 内容指纹)
# --------------------------------------------------------------------------

def cloud_versions(book32: str) -> dict[str, dict]:
    """`getUserDataVer?bookids=` -> 云端版本表 (APK hg/m.p 的输入)。

    返回 {'note': {uniquecheck: marktime}, 'mark': {...}, 'del': set()}
    取不到 (网络/风控) 一律当空 —— 退化成"全量推", 由 sig 保证幂等。
    """
    try:
        row = data_ver(book32)
    except Exception:
        return {"note": {}, "mark": {}, "del": set()}

    def _pick(name: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for it in (row.get(name) or []):
            if not isinstance(it, dict):
                continue
            k = it.get("uniquecheck") or it.get("uniquechek")
            if not k:
                continue
            try:
                out[norm_key(str(k))] = int(it.get("marktime") or 0)
            except (TypeError, ValueError):
                out[norm_key(str(k))] = 0
        return out

    return {"note": _pick("booknotesVerList"), "mark": _pick("bookmarksVerList"),
            "del": {norm_key(str(x)) for x in (row.get("delList") or [])}}


def _sig(kind: str, key: str, text: str, remark: str, meta: str = "") -> str:
    """内容指纹: 位置 + 正文 + 批注 (+ 展示用章名)。变了才重推。

    APK 靠本地 DB 的 marktime, 我们没本地副本, 所以自己算指纹; meta 进指纹是为了
    "章名解析规则变了" 也能触发一次重推 (否则旧条目永远停在 chapter0152 这种文件名)。
    """
    h = hashlib.sha1()
    for part in (kind, key, text or "", remark or "", meta or ""):
        h.update(part.encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()


def _chapter_label(geo: zpct.BookGeometry, ch: int) -> str:
    """chapterId -> 展示用章名。优先目录 ("剑来14：江清月近人 第十章 北俱芦洲无奇怪"),
    其次文件名。ch+1 是位置所在篇, ch 是"已读完的上一篇" (封面等没有目录名的篇,
    只能退回文件名)。"""
    toc = getattr(geo, "toc", None) or {}
    for idx in (ch + 1, ch):
        if 0 <= idx < len(geo.spine):
            lab = toc.get(idx)
            if lab:
                return str(lab)
    for idx in (ch + 1, ch):
        if 0 <= idx < len(geo.spine):
            return Path(geo.spine[idx]).stem
    return ""


def _idea_fields(epub: str, geo: zpct.BookGeometry, g: int) -> dict:
    """APK BookHighLight 的 idea 字段: chapterId / chaptername / paragraphId /
    paragraphOffset / notesType。缺了只影响掌阅端"想法"卡片展示, 不影响划线本身。"""
    out: dict[str, Any] = {}
    try:
        ch, off = geo.locate(g)
    except Exception:
        return out
    out["chapterId"] = int(ch)          # 0 基, 与 position 的 f2 同源
    out["notesType"] = 2                # APK isPrivate(): 2 = 仅自己可见
    # 位置落在 spine[ch+1] 里 (percent = Σsizes[0..ch] + f3, f3 是"下一篇"内偏移),
    # 所以章名要取 ch+1; 目录名优先, 没有目录才退回文件名 (以前的 chapter0152 就是这个)。
    out["chaptername"] = _chapter_label(geo, int(ch))
    raw = _doc_raw(epub, int(ch) + 1)
    if not raw:
        return out
    try:
        di = zpct.DocIndex(raw)
        xp = di.xpath_of_offset(int(off)) or ""
        m = re.search(r"/[a-zA-Z]+\[(\d+)\]$", xp)
        if m:
            out["paragraphId"] = float(m.group(1))
            start = di.offset_of_xpath(xp)
            out["paragraphOffset"] = int(max(0, int(off) - int(start)))
    except Exception:
        pass
    return out


def _page_start_bytes(link: mapping.BookLink, geo: zpct.BookGeometry, page: int) -> int | None:
    """Kavita 页号 -> 原书全局字节偏移 (书签定位用)。

    切片版走 sidecar (页 == 切片页); 原书直接进库的 (图片书) 页号 = spine 下标。
    """
    sc = kpages.load(link.kavita_series_id)
    try:
        if sc:
            return int(kpages.bytes_of_page(sc, int(page)))
        idx = min(max(int(page) - 1, 0), len(geo.spine) - 1)
        return int(geo.prefix_bytes(idx))
    except Exception:
        return None


def push(series_ids: list[int] | None = None, *, dry_run: bool = False) -> dict:
    """Kavita -> 掌阅: 划线 / 书签 增量推。照抄 APK hg/m.p() 的合并语义。

    与 APK 对齐的 6 条:
      1. 先 `getUserDataVer?bookids=` 拿云端版本表 (uniquecheck -> marktime)
      2. **云端 marktime >= 本地 marktime → 不推** (本地较新者胜)
      3. 只在 `code==0` 后写本地 link 表 (失败留着重试, 下次仍算"本地较新")
      4. booknotes 条目字段与 APK `BookHighLight.getJSONObject()` 一致
         (uniquecheck/style/color/summary/remark/positionstart/positionend/
          positionstartl/positionendl/marktime + idea: chapterId/chaptername/
          paragraphId/paragraphOffset/notesType)
      5. 书签走 APK `BookMark.getJSONObject()` 的 6 个键
         (uniquecheck/marksummary/markpostion/markpercent/markstyle/marktime)
      6. 删除走 delMarkOrNote (APK CLOUD_DEL), 且**只删本地登记为 kv 来源**的行 ——
         绝不动掌阅自己上传的 (旧版会把 source=zy 的行当"已删除"推回云端删掉)
    """
    report: dict[str, Any] = {"books": [], "pushed": 0, "pushed_marks": 0, "skipped": 0,
                              "deleted": 0, "errors": [], "cloud_skip": 0}
    links = [l for l in mapping.all_links() if not series_ids or l.kavita_series_id in series_ids]
    for link in links:
        epub = _orig_epub(link)
        geo = geometry(link)
        if not epub or geo is None:
            report["books"].append({"series": link.kavita_series_id, "skip": "no_geometry"})
            continue
        book32 = kb32(link.zy_book_id)
        total_bytes = max(1, geo.total_bytes)
        cloud = cloud_versions(book32)
        rows_known = links_for_series(link.kavita_series_id)
        known = {(r["kind"], r["zy_key"]): r for r in rows_known}
        # 按 Kavita 注解 id 反查来源: pull 回来的行, key 是云端原始位置;
        # push 重算位置时可能落在另一处同名文本 -> key 对不上 -> 被当成新条目重复回灌。
        byid = {(r["kind"], int(r["kavita_annotation_id"] or 0)): r for r in rows_known}
        idx_row = {}
        try:
            for row in notebooks():
                if kb32(str(row.get("bookid", ""))) == book32:
                    idx_row = row
                    break
        except Exception:
            pass

        out_notes: list[dict] = []
        out_marks: list[dict] = []
        st: list[tuple[str, str, str, int, int | None]] = []   # kind,key,sig,marktime,ann_id
        ann_keys: set[str] = set()          # Kavita 侧仍然是活的 key (用于判删除)

        # --- 划线
        for a in _series_notes_for(link, kavita_annotations):
            text = (a.get("SelectedText") or "").strip()
            if not text:
                report["skipped"] += 1
                continue
            g = _find_in_book(epub, text)
            if g is None:
                report["skipped"] += 1
                continue
            ps = global_to_pos(geo, g)
            pe = global_to_pos(geo, g + len(text.encode("utf-8")))
            key = note_key(book32, ps, pe)
            ann_keys.add(key)
            if key in {it["uniquecheck"] for it in out_notes}:
                report["skipped"] += 1        # 同书同选区重复标注 -> 一条
                continue
            remark = (a.get("CommentPlainText") or "").strip()
            idea = _idea_fields(epub, geo, g)
            sig = _sig("note", key, text, remark, str(idea.get("chaptername") or ""))
            row = known.get(("note", key)) or byid.get(("note", int(a.get("Id") or 0)))
            if row is not None and row["source"] == "zy":
                ann_keys.add(row["zy_key"])   # 保留云端原始 key, 别误判成"Kavita 已删"
                report["skipped"] += 1        # 掌阅自己上传的, 不回灌
                continue
            if row is not None and (row["sig"] or "") == sig:
                report["skipped"] += 1        # 内容没变, 不重推
                continue
            # 重推沿用原 marktime: 掌阅端"笔记"按它排序, 改元信息不该让旧笔记跳到最前
            marktime = int(row["marktime"] or 0) if row is not None and row["marktime"] \
                else int(time.time() * 1000)
            ck = cloud["note"].get(key)
            # 严格大于: 相等 = 云端就是上次我们推的那版, 允许覆盖 (修章名等元信息)
            if ck is not None and row is not None and ck > int(row["marktime"] or 0):
                report["cloud_skip"] += 1     # 云端比本地新 (app 上改过)
                continue
            item = {"uniquecheck": key, "style": 0, "color": DEFAULT_COLOR,
                    "summary": text, "remark": remark,
                    "positionstart": ps, "positionend": pe,
                    "positionstartl": 0, "positionendl": 0,
                    "marktime": marktime}
            item.update(idea)
            out_notes.append(item)
            st.append(("note", key, sig, marktime, int(a.get("Id") or 0) or None))

        # --- 书签
        mark_keys: set[str] = set()
        for b in _series_notes_for(link, kavita_bookmarks):
            g = _page_start_bytes(link, geo, int(b.get("Page") or 0))
            if g is None:
                report["skipped"] += 1
                continue
            pos = global_to_pos(geo, g)
            key = mark_key(book32, pos)
            mark_keys.add(key)
            label = ""
            try:
                mch, _moff = geo.locate(g)
                label = _chapter_label(geo, int(mch))
            except Exception:
                label = (b.get("ChapterTitle") or "").strip()
            sig = _sig("mark", key, "", "", label)
            row = known.get(("bookmark", key)) or byid.get(("bookmark", int(b.get("Id") or 0)))
            if row is not None and row["source"] == "zy":
                mark_keys.add(row["zy_key"])
                report["skipped"] += 1
                continue
            if row is not None and (row["sig"] or "") == sig:
                report["skipped"] += 1
                continue
            marktime = int(row["marktime"] or 0) if row is not None and row["marktime"] \
                else int(time.time() * 1000)
            ck = cloud["mark"].get(key)
            if ck is not None and row is not None and ck > int(row["marktime"] or 0):
                report["cloud_skip"] += 1
                continue
            out_marks.append({"uniquecheck": key,
                              "marksummary": label or (b.get("ChapterTitle") or "").strip(),
                              "markpostion": pos,
                              "markpercent": round(g / total_bytes, 8),
                              "markstyle": 0, "marktime": marktime})
            st.append(("bookmark", key, sig, marktime, int(b.get("Id") or 0) or None))

        # --- 删除: 只处理 kv 来源 (Kavita 那边已经没有这条了)
        del_notes = [r["zy_key"] for r in links_for_series(link.kavita_series_id, "note")
                     if (r["source"] or "") == "kv" and r["zy_key"] not in ann_keys]
        del_marks = [r["zy_key"] for r in links_for_series(link.kavita_series_id, "bookmark")
                     if (r["source"] or "") == "kv" and r["zy_key"] not in mark_keys]

        if dry_run:
            report["pushed"] += len(out_notes)
            report["pushed_marks"] += len(out_marks)
            report["deleted"] += len(del_notes) + len(del_marks)
            report["books"].append({"series": link.kavita_series_id,
                                    "would_push": len(out_notes),
                                    "would_push_marks": len(out_marks),
                                    "would_delete": len(del_notes) + len(del_marks),
                                    "cloud_known": {"notes": len(cloud["note"]),
                                                    "marks": len(cloud["mark"])}})
            continue

        # --- 上传 (一本书一个 payload; 空数组按 APK 习惯省略)
        if out_notes or out_marks:
            payload: dict[str, Any] = {"bookbase": {
                "name": idx_row.get("name") or link.zy_book_name,
                "type": int(idx_row.get("type") or 5),
                "readpostion": idx_row.get("readpostion") or "",
                "readpercent": idx_row.get("readpercent") or 0,
                "bookid": book32,
                "updatetime": int(time.time() * 1000),
                "deviceName": "KavitaBridge",
                "marknum": int(idx_row.get("marknum") or 0) + len(out_marks),
                "notenum": int(idx_row.get("notenum") or 0) + len(out_notes)}}
            if out_marks:
                payload["bookmarks"] = out_marks
            if out_notes:
                payload["booknotes"] = out_notes
            try:
                res = upload(payload)
                if res.get("code") not in (0, None):
                    report["errors"].append(f"upload {book32}: {res}")
                else:
                    for kind, key, sig, mt, ann_id in st:
                        link_put(kind, book32, key, series_id=link.kavita_series_id,
                                 chapter_id=_chapter_id_for(link),
                                 annotation_id=ann_id, marktime=mt, source="kv", sig=sig)
                    report["pushed"] += len(out_notes)
                    report["pushed_marks"] += len(out_marks)
            except Exception as e:
                report["errors"].append(f"upload {book32}: {e}")
        # --- 删除 (delMarkOrNote: 划线 type=2 / 书签 type=1)
        for keys, kind, type_ in ((del_notes, "note", 2), (del_marks, "bookmark", 1)):
            if not keys:
                continue
            try:
                delete_items(book32, keys, type_=type_)
                for k in keys:
                    link_delete(kind, book32, k)
                report["deleted"] += len(keys)
            except Exception as e:
                report["errors"].append(f"delMarkOrNote {book32}: {e}")
        report["books"].append({"series": link.kavita_series_id,
                                "pushed": len(out_notes), "pushed_marks": len(out_marks),
                                "deleted": len(del_notes) + len(del_marks)})
    return report


# --------------------------------------------------------------------------
# 身份大小写融合 (2026-11-01)
# --------------------------------------------------------------------------

def _old_key(zy_book_id: str) -> str:
    """历史身份: 32hex **小写** (>=2026-11-01 之前的写法)。"""
    return (zy_book_id or "").strip().split("_")[0][:32].lower()


def consolidate_case(link: mapping.BookLink, *, dry_run: bool = False,
                     delete_old: bool = True) -> dict:
    """把小写身份上的笔记/书签并入大写身份, 再删掉小写那份。

    云端对 bookid 大小写敏感: 小写 `<md5>` 与大写 `<MD5>` 是**两个独立身份**,
    App 的「笔记本」列表会显示成两条同名书。App 有时仍会往小写写 (它自己的
    本地书 id 口径), 所以 pull 每次先融合一次 —— 否则用户会一直看到两条。
    幂等: 已在大写里的 key 跳过; 正文从原书按字节切。

    返回 {"merged": n, "marks": n, "deleted": n} / {"skip": ...}
    """
    new = kb32(link.zy_book_id)
    old = _old_key(link.zy_book_id)
    if not old or old == new:
        return {"skip": "no_twin"}
    src = positions_payload(old, _orig_epub(link)) if _orig_epub(link) else {}
    src_notes = src.get("booknotes") or []
    src_marks = src.get("bookmarks") or []
    if not src_notes and not src_marks:
        return {"skip": "empty_old"}
    dst = data_ver(new)
    have_n = {norm_key(str(i.get("uniquecheck"))) for i in (dst.get("booknotesVerList") or [])}
    have_m = {norm_key(str(i.get("uniquecheck"))) for i in (dst.get("bookmarksVerList") or [])}
    notes = [n for n in src_notes if norm_key(n["uniquecheck"]) not in have_n]
    marks = [m for m in src_marks if norm_key(m["uniquecheck"]) not in have_m]
    out = {"old_notes": len(src_notes), "old_marks": len(src_marks),
           "merged": len(notes), "merged_marks": len(marks)}
    if dry_run:
        out["dry_run"] = True
        return out
    if notes or marks:
        geo = geometry(link)
        epub = _orig_epub(link)
        payload_notes, payload_marks = [], []
        for n in notes:
            pos_s, pos_e = _split_positions(n["uniquecheck"])[:2]
            item = {"uniquecheck": norm_key(n["uniquecheck"]), "style": 0,
                    "color": DEFAULT_COLOR, "summary": (n.get("summary") or "").strip(),
                    "remark": (n.get("remark") or "").strip(),
                    "positionstart": pos_s, "positionend": pos_e,
                    "positionstartl": 0, "positionendl": 0,
                    "marktime": int(n.get("marktime") or 0)}
            g = pos_to_global(geo, pos_s) if geo else None
            if g is not None and epub:
                try:
                    item.update(_idea_fields(epub, geo, g))
                except Exception:                      # noqa: BLE001
                    pass
            payload_notes.append(item)
        for m in marks:
            pos = m.get("markpostion") or (_split_positions(m["uniquecheck"])[:1] or [""])[0]
            g = pos_to_global(geo, pos) if geo else None
            label, pct = "", 0
            if g is not None and geo is not None:
                try:
                    label = _chapter_label(geo, int(geo.locate(g)[0]))
                except Exception:                      # noqa: BLE001
                    label = ""
                pct = round(g / max(1, geo.total_bytes), 8)
            payload_marks.append({"uniquecheck": norm_key(m["uniquecheck"]),
                                  "marksummary": label, "markpostion": pos,
                                  "markpercent": pct, "markstyle": 0,
                                  "marktime": int(m.get("marktime") or 0)})
        payload: dict[str, Any] = {"bookbase": {
            "name": link.zy_book_name,
            "type": 5,
            "readpostion": "",
            "readpercent": 0,
            "bookid": new,
            "updatetime": int(time.time() * 1000),
            "deviceName": "KavitaBridge",
            "marknum": len(payload_marks), "notenum": len(payload_notes)}}
        if payload_marks:
            payload["bookmarks"] = payload_marks
        if payload_notes:
            payload["booknotes"] = payload_notes
        res = upload(payload)
        if res.get("code") not in (0, None):
            out["error"] = f"upload: {res}"
            return out
    if delete_old:
        for keys, type_ in ((src_notes, 2), (src_marks, 1)):
            if not keys:
                continue
            try:
                delete_items(old, [str(k["uniquecheck"]).lower() for k in keys], type_=type_)
                out["deleted"] = out.get("deleted", 0) + len(keys)
            except Exception as e:                     # noqa: BLE001
                out.setdefault("error", []).append(f"del: {e}")
    return out


def repair(series_ids: list[int] | None = None, *, dry_run: bool = False) -> dict:
    """修历史遗留的注解/书签坐标 (2026-11-01 之前 pull 写错的两种形态)。

    老 pull 写进去的两处错, 导致 Kavita 阅读器里**一条高亮都不画**:
      1. XPath 带 `id("kp/pNNNN.xhtml")` 前缀 —— Kavita 逐页渲染, 页文档里没有那个 id
      2. PageNumber 用 1 基 —— Kavita 的 page 序号 0 基, 高亮整体错开一页
      3. EndingXPath = XPath —— 跨段选择在单节点里凑不齐, 整条被跳过

    这里按 `zy_note_link` 里登记的 uniquecheck (内含起止字节位置) 重新算一遍坐标,
    直接 UPDATE 库里的行。幂等, 可反复跑。
    """
    report: dict[str, Any] = {"fixed_notes": 0, "fixed_marks": 0, "skipped": 0,
                              "books": [], "errors": []}
    links = [l for l in mapping.all_links() if not series_ids or l.kavita_series_id in series_ids]
    conn = None if dry_run else sqlite3.connect(KAVITA_DB, timeout=30)
    for link in links:
        epub = _orig_epub(link)
        geo = geometry(link)
        if not epub or geo is None:
            report["books"].append({"series": link.kavita_series_id, "skip": "no_geometry"})
            continue
        sc = kpages.load(link.kavita_series_id)
        book32 = kb32(link.zy_book_id)
        done = {"note": 0, "bookmark": 0}
        for row in links_for_series(link.kavita_series_id):
            poses = _split_positions(row["zy_key"] or "")
            if row["kind"] == "note":
                if len(poses) < 2 or not row["kavita_annotation_id"]:
                    report["skipped"] += 1
                    continue
                summary = text_of_span(epub, poses[0], poses[1],
                                       end_inclusive=(row["source"] or "") != "kv").strip()
                g = pos_to_global(geo, poses[0])
                if g is None:
                    report["skipped"] += 1
                    continue
                page, rel, _raw, _inner = _locate(geo, epub, sc, g, summary)
                xp = _kv_xpath(geo, page, rel, bool(sc))
                ex = _kv_xpath(geo, page, _end_xpath(
                    geo, epub, sc, {"positionend": poses[1], "summary": summary},
                    g, page, rel), bool(sc))
                if not dry_run:
                    conn.execute(
                        "UPDATE AppUserAnnotation SET PageNumber=?, XPath=?, EndingXPath=?"
                        " WHERE Id=?", (page, xp, ex, row["kavita_annotation_id"]))
                done["note"] += 1
            else:
                if not poses or not row["kavita_annotation_id"]:
                    report["skipped"] += 1
                    continue
                g = pos_to_global(geo, poses[0])
                if g is None:
                    report["skipped"] += 1
                    continue
                page, rel, _raw, _inner = _locate(geo, epub, sc, g, "")
                if not dry_run:
                    conn.execute(
                        "UPDATE AppUserBookmark SET Page=?, XPath=? WHERE Id=?",
                        (page, _kv_xpath(geo, page, rel, bool(sc)),
                         row["kavita_annotation_id"]))
                done["bookmark"] += 1
        report["fixed_notes"] += done["note"]
        report["fixed_marks"] += done["bookmark"]
        report["books"].append({"series": link.kavita_series_id,
                               "notes": done["note"], "bookmarks": done["bookmark"]})
    if conn is not None:
        conn.commit()
        conn.close()
    return report


def sync(series_ids: list[int] | None = None, *, dry_run: bool = False) -> dict:
    """先 pull 掌阅 (掌阅权威), 再 push Kavita 独有项。"""
    p = pull(series_ids, dry_run=dry_run)
    q = push(series_ids, dry_run=dry_run)
    return {"pull": p, "push": q}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "sync"
    dry = "--dry-run" in sys.argv
    fn = {"pull": pull, "push": push, "sync": sync, "repair": repair}[cmd]
    print(json.dumps(fn(dry_run=dry), ensure_ascii=False, indent=2))
