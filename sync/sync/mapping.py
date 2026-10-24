"""桥接映射: Kavita Series/Chapter ID ↔ 掌阅 book_id/chapter_id

存到 state/sync.sqlite. 每行:
    kavita_series_id (int, pk)
    zy_book_id       (text)
    zy_book_name     (text)
    zy_total_chapters (int, 可空)
    zy_chapter_offset (int, default 0)  # Kavita 第 idx 章 = 掌阅第 idx + offset 章
    kavita_chapter_count (int)
    note (text)
    created_utc, updated_utc

chapter_id 偏移: 掌阅 API 中 chapterId 是 app 内部的字符串 id,
我们先把每本书的 chapterId 用 getProgress 现有 payload 的 chapterId 作锚定,
后续按 SortOrder/Kavita 章号推算。

当前策略:
    1. 首次 sync 该书时, 把 chapterId 锚定到 Kavita 已读了的那一章。
    2. upload_progress 时, 章节百分比=实读比例, chapterId 用锚定值。
    3. 整本读完 → totalReads=1 + readComplete=1 + chapterId 留最后已知
"""
from __future__ import annotations
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .config import STATE_DB, ensure_dirs


SERIES_LEVEL = -1               # book_link.kavita_chapter_id = -1 → 整本/系列级映射


@dataclass
class BookLink:
    kavita_series_id: int
    zy_book_id: str
    zy_book_name: str
    zy_anchor_chapter_id: str | None
    kavita_chapter_count: int
    kavita_chapter_id: int = SERIES_LEVEL   # Kavita Chapter.Id; -1 = 整本/系列级
    epub_path: str = ""          # 本地 epub 绝对路径 — 算 readPercent 的 spine 几何
    note: str = ""
    updated_utc: str = ""

    @property
    def series_level(self) -> bool:
        return self.kavita_chapter_id == SERIES_LEVEL


def _conn():
    ensure_dirs()
    c = sqlite3.connect(STATE_DB)
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE IF NOT EXISTS book_link (
            kavita_series_id INTEGER NOT NULL,
            kavita_chapter_id INTEGER NOT NULL DEFAULT -1,
            zy_book_id TEXT NOT NULL,
            zy_book_name TEXT NOT NULL,
            zy_anchor_chapter_id TEXT,
            kavita_chapter_count INTEGER,
            epub_path TEXT,
            note TEXT,
            created_utc TEXT DEFAULT (datetime('now')),
            updated_utc TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (kavita_series_id, kavita_chapter_id)
        );
        CREATE TABLE IF NOT EXISTS sync_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_utc TEXT DEFAULT (datetime('now'))
        );
    """)
    _migrate(c)
    return c


def _migrate(c: sqlite3.Connection) -> None:
    """给老库补列/换主键。CREATE TABLE IF NOT EXISTS 不会改已存在的表。"""
    info = list(c.execute("PRAGMA table_info(book_link)"))
    have = {r["name"] for r in info}
    pk = [r["name"] for r in sorted(info, key=lambda r: r["pk"]) if r["pk"]]
    # v1: kavita_series_id 单列主键 (一个系列只能挂一本掌阅书, 多卷系列会串)
    # v2: (kavita_series_id, kavita_chapter_id) 复合主键, chapter_id=-1 表示整本
    if pk == ["kavita_series_id"]:
        c.executescript("""
            ALTER TABLE book_link RENAME TO book_link_v1;
            CREATE TABLE book_link (
                kavita_series_id INTEGER NOT NULL,
                kavita_chapter_id INTEGER NOT NULL DEFAULT -1,
                zy_book_id TEXT NOT NULL,
                zy_book_name TEXT NOT NULL,
                zy_anchor_chapter_id TEXT,
                kavita_chapter_count INTEGER,
                epub_path TEXT,
                note TEXT,
                created_utc TEXT DEFAULT (datetime('now')),
                updated_utc TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (kavita_series_id, kavita_chapter_id)
            );
            INSERT INTO book_link (kavita_series_id, kavita_chapter_id, zy_book_id,
                                   zy_book_name, zy_anchor_chapter_id,
                                   kavita_chapter_count, epub_path, note,
                                   created_utc, updated_utc)
            SELECT kavita_series_id, -1, zy_book_id, zy_book_name,
                   zy_anchor_chapter_id, kavita_chapter_count, epub_path, note,
                   created_utc, updated_utc
            FROM book_link_v1;
            DROP TABLE book_link_v1;
        """)
        c.commit()
        info = list(c.execute("PRAGMA table_info(book_link)"))
        have = {r["name"] for r in info}
    for col, decl in (("epub_path", "TEXT"),
                      ("kavita_chapter_id", "INTEGER NOT NULL DEFAULT -1")):
        if col not in have:
            c.execute(f"ALTER TABLE book_link ADD COLUMN {col} {decl}")
            c.commit()


def link(kavita_series_id: int, zy_book_id: str, zy_book_name: str,
         *, zy_anchor_chapter_id: str | None = None,
         kavita_chapter_count: int = 0, epub_path: str = "",
         note: str = "", kavita_chapter_id: int = SERIES_LEVEL) -> BookLink:
    """建/更新一条映射。

    kavita_chapter_id = -1  → 整本 (系列级, 一本掌阅书 = 整个 Kavita 系列)
    kavita_chapter_id = 36  → 卷级 (一本掌阅书 = 该系列里的第 36 号 chapter/卷)
    """
    c = _conn()
    c.execute("""
        INSERT INTO book_link (kavita_series_id, kavita_chapter_id, zy_book_id,
                               zy_book_name, zy_anchor_chapter_id,
                               kavita_chapter_count, epub_path, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(kavita_series_id, kavita_chapter_id) DO UPDATE SET
            zy_book_id=excluded.zy_book_id,
            zy_book_name=excluded.zy_book_name,
            zy_anchor_chapter_id=COALESCE(excluded.zy_anchor_chapter_id, book_link.zy_anchor_chapter_id),
            kavita_chapter_count=excluded.kavita_chapter_count,
            epub_path=COALESCE(NULLIF(excluded.epub_path,''), book_link.epub_path),
            note=excluded.note,
            updated_utc=datetime('now')
    """, (kavita_series_id, int(kavita_chapter_id), zy_book_id, zy_book_name,
          zy_anchor_chapter_id, kavita_chapter_count, epub_path, note))
    c.commit()
    c.close()
    return get(kavita_series_id, kavita_chapter_id)  # type: ignore


def _row_to_link(r) -> BookLink:
    return BookLink(
        kavita_series_id=r["kavita_series_id"], zy_book_id=r["zy_book_id"],
        zy_book_name=r["zy_book_name"],
        zy_anchor_chapter_id=r["zy_anchor_chapter_id"],
        kavita_chapter_count=r["kavita_chapter_count"] or 0,
        kavita_chapter_id=(r["kavita_chapter_id"]
                           if r["kavita_chapter_id"] is not None else SERIES_LEVEL),
        epub_path=r["epub_path"] or "",
        note=r["note"] or "",
        updated_utc=r["updated_utc"] or "",
    )


def get(kavita_series_id: int,
        kavita_chapter_id: int = SERIES_LEVEL) -> BookLink | None:
    c = _conn()
    r = c.execute("SELECT * FROM book_link WHERE kavita_series_id=? AND kavita_chapter_id=?",
                  (kavita_series_id, int(kavita_chapter_id))).fetchone()
    c.close()
    return _row_to_link(r) if r else None


def resolve(kavita_series_id: int, kavita_chapter_id: int | None = None) -> BookLink | None:
    """按 (系列, 章) 找映射: 卷级精确匹配优先, 没有则退回系列级 (-1)。"""
    if kavita_chapter_id is not None:
        l = get(kavita_series_id, kavita_chapter_id)
        if l:
            return l
    return get(kavita_series_id, SERIES_LEVEL)


def links_for_series(kavita_series_id: int) -> list[BookLink]:
    c = _conn()
    rs = c.execute("SELECT * FROM book_link WHERE kavita_series_id=? ORDER BY kavita_chapter_id",
                   (kavita_series_id,)).fetchall()
    c.close()
    return [_row_to_link(r) for r in rs]


def all_links() -> list[BookLink]:
    c = _conn()
    rs = c.execute("SELECT * FROM book_link ORDER BY kavita_series_id, kavita_chapter_id").fetchall()
    c.close()
    return [_row_to_link(r) for r in rs]


def update_anchor(kavita_series_id: int, anchor: str,
                  kavita_chapter_id: int = SERIES_LEVEL) -> None:
    c = _conn()
    c.execute("""UPDATE book_link SET zy_anchor_chapter_id=?, updated_utc=datetime('now')
                 WHERE kavita_series_id=? AND kavita_chapter_id=?""",
              (anchor, kavita_series_id, int(kavita_chapter_id)))
    c.commit()
    c.close()


def delete(kavita_series_id: int, kavita_chapter_id: int = SERIES_LEVEL) -> None:
    c = _conn()
    c.execute("DELETE FROM book_link WHERE kavita_series_id=? AND kavita_chapter_id=?",
              (kavita_series_id, int(kavita_chapter_id)))
    c.commit()
    c.close()


def state_set(key: str, value: str) -> None:
    c = _conn()
    c.execute("""INSERT INTO sync_state (key, value) VALUES (?, ?)
                 ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_utc=datetime('now')""",
              (key, value))
    c.commit()
    c.close()


def state_get(key: str) -> str | None:
    c = _conn()
    r = c.execute("SELECT value FROM sync_state WHERE key=?", (key,)).fetchone()
    c.close()
    return r["value"] if r else None
