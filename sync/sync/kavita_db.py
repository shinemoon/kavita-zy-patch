"""Kavita 本地 db 进度读写

直接 sqlite 走, 不走 REST (0.9.0.2 REST 端点不全, 进度 controller 404)。
DB 在 /vol1/1001/APPS/kavita/kavita.db, WAL mode, 必须用 .backup() 拷。

字段语义:
    PagesRead     int  本章已读页数 (0-N, 全章读完 = PagesRead==Chapter.Pages)
    TotalReads    int  0 或 1, 1 = 本章读完一次
    BookScrollId  text 历史 xpath 定位, 新版 Kavita 用 BookScrollId 或 页码
    LastModifiedUtc text UTC 时间戳, 用于决定哪边更新

唯一约束: PK (AppUserId+ChapterId)
"""
from __future__ import annotations
import sqlite3
import shutil
import tempfile
import os
from dataclasses import dataclass
from typing import Iterator

from .config import KAVITA_DB, KAVITA_USER_ID


def _open() -> sqlite3.Connection:
    """开 db 读 (WAL 已用 .backup 走)."""
    # 临时拷一份 (VACUUM INTO 也行, .backup 更稳)
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    src = sqlite3.connect(f"file:{KAVITA_DB}?mode=ro", uri=True)
    dst = sqlite3.connect(tmp)
    src.backup(dst)
    src.close()
    dst.row_factory = sqlite3.Row
    return dst


@dataclass
class Progress:
    series_id: int
    volume_id: int
    chapter_id: int
    chapter_title: str
    pages_read: int
    total_pages: int
    total_reads: int
    last_modified: str
    chapter_number: str = ""
    sort_order: float = 0.0
    book_scroll_id: str = ""      # xpath 定位 (如 //body/div[1]/p[15]) — 文档内精确位置

    @property
    def percent(self) -> float:
        if not self.total_pages:
            return 0.0
        return min(100.0, self.pages_read * 100.0 / self.total_pages)

    @property
    def finished(self) -> bool:
        return self.total_reads >= 1


def iter_progress(user_id: int = KAVITA_USER_ID) -> Iterator[Progress]:
    """所有当前 user 在 kavita 的进度记录。"""
    db = _open()
    sql = """
        SELECT ap.SeriesId, ap.VolumeId, ap.ChapterId, ap.PagesRead, ap.TotalReads,
               ap.LastModifiedUtc, ap.BookScrollId, c.Title ChTitle, c.Pages ChPages,
               c.Number ChNum, c.SortOrder ChSort,
               s.Name SerName, v.Number VolNum
        FROM AppUserProgresses ap
        LEFT JOIN Chapter c ON c.Id = ap.ChapterId
        LEFT JOIN Volume v  ON v.Id  = ap.VolumeId
        LEFT JOIN Series s  ON s.Id  = ap.SeriesId
        WHERE ap.AppUserId = ?
        ORDER BY ap.SeriesId, ap.VolumeId, c.SortOrder
    """
    for r in db.execute(sql, (user_id,)):
        yield Progress(
            series_id=r["SeriesId"], volume_id=r["VolumeId"], chapter_id=r["ChapterId"],
            chapter_title=r["ChTitle"] or "",
            pages_read=r["PagesRead"] or 0,
            total_pages=r["ChPages"] or 0,
            total_reads=r["TotalReads"] or 0,
            last_modified=r["LastModifiedUtc"] or "",
            chapter_number=r["ChNum"] or "",
            sort_order=r["ChSort"] or 0.0,
            book_scroll_id=r["BookScrollId"] or "",
        )
    db.close()


def list_chapters(series_id: int) -> list[dict]:
    """列一本书的全部章节, 通过 Volume 关联 Series."""
    db = _open()
    sql = """
        SELECT c.Id ChId, c.Title ChTitle, c.Number ChNum, c.SortOrder ChSort,
               c.Pages ChPages, c.VolumeId VolId, v.Number VolNum
        FROM Chapter c
        LEFT JOIN Volume v ON v.Id = c.VolumeId
        WHERE v.SeriesId = ?
        ORDER BY COALESCE(v.Number,0), COALESCE(c.SortOrder, c.Id)
    """
    rows = [dict(r) for r in db.execute(sql, (series_id,))]
    db.close()
    return rows


def series_name(series_id: int) -> str:
    db = _open()
    r = db.execute("SELECT Name FROM Series WHERE Id=?", (series_id,)).fetchone()
    db.close()
    return (r["Name"] if r else "") or ""


def series_files(series_id: int) -> list[str]:
    """一个系列下所有书的文件路径 (多卷系列会有多条)。"""
    db = _open()
    rows = db.execute("""
        SELECT f.FilePath FROM MangaFile f
        JOIN Chapter c ON c.Id = f.ChapterId
        JOIN Volume v ON v.Id = c.VolumeId
        WHERE v.SeriesId = ?
        ORDER BY f.FilePath
    """, (series_id,)).fetchall()
    db.close()
    return [r["FilePath"] for r in rows]


def series_for_file(name: str) -> int | None:
    """按文件名反查 Kavita 系列 (不扫库, 只看当前 db)。

    name 可以是完整文件名 (狐的故事.epub) 或去掉扩展名的 stem。

    两条路: 先按 MangaFile 反查 (join 到 Volume); 查不到再按 Series.Name
    兜底 —— 换库/重扫之后 MangaFile 表里会留旧行 (ChapterId 指向已删的
    Chapter), join 会把它们滤掉, 那时 Name 匹配才是可靠的。
    """
    if not name:
        return None
    stem = name[:-5] if name.lower().endswith(".epub") else name
    db = _open()
    r = db.execute("""
        SELECT v.SeriesId FROM MangaFile f
        JOIN Chapter c ON c.Id = f.ChapterId
        JOIN Volume v ON v.Id = c.VolumeId
        WHERE f.FileName IN (?, ?, ?) OR f.FilePath LIKE ?
        LIMIT 1
    """, (name, stem, stem + ".epub", f"%/{name}")).fetchone()
    if r:
        db.close()
        return int(r["SeriesId"])
    r = db.execute("SELECT Id FROM Series WHERE Name = ? OR Name = ? LIMIT 1",
                   (stem, name)).fetchone()
    db.close()
    return int(r["Id"]) if r else None


def chapter_for_file(series_id: int, name: str) -> dict | None:
    """按文件名反查该系列下对应的 chapter/卷 (卷级映射用)。

    多卷系列 (一个系列 = 多本掌阅书) 必须知道"这本 epub 是哪一卷",
    否则进度只能落到 series 级, 三卷共用一个进度格。
    """
    if not name:
        return None
    stem = name[:-5] if name.lower().endswith(".epub") else name
    db = _open()
    r = db.execute("""
        SELECT c.Id ChId, c.Title ChTitle, c.Pages ChPages,
               c.VolumeId VolId, v.Number VolNum
        FROM MangaFile f
        JOIN Chapter c ON c.Id = f.ChapterId
        JOIN Volume v ON v.Id = c.VolumeId
        WHERE v.SeriesId = ?
          AND (f.FileName IN (?, ?, ?) OR f.FilePath LIKE ?)
        LIMIT 1
    """, (series_id, name, stem, stem + ".epub", f"%/{name}")).fetchone()
    db.close()
    return dict(r) if r else None


def get_progress_for_chapter(chapter_id: int, user_id: int = KAVITA_USER_ID):
    """读某章已有进度, 返回 (pages_read, total_reads) 或 None。

    pull 的防回退守卫依赖它 —— 只读副本, 不碰主库。
    """
    db = _open()
    r = db.execute("""
        SELECT ap.PagesRead, ap.TotalReads
        FROM AppUserProgresses ap
        WHERE ap.AppUserId=? AND ap.ChapterId=? LIMIT 1
    """, (user_id, chapter_id)).fetchone()
    db.close()
    if r is None:
        return None
    return (r["PagesRead"] or 0, r["TotalReads"] or 0)


def get_progress_full(chapter_id: int, user_id: int = KAVITA_USER_ID) -> dict | None:
    """读某章进度全量 (含 BookScrollId)。

    BookScrollId = 文档内精确位置 (xpath), 跟 PagesRead (文档级下标) 配套。
    pull 的"只前进不后退"比较要用两者合成的绝对字节位置。
    """
    db = _open()
    r = db.execute("""
        SELECT ap.PagesRead, ap.TotalReads, ap.BookScrollId
        FROM AppUserProgresses ap
        WHERE ap.AppUserId=? AND ap.ChapterId=? LIMIT 1
    """, (user_id, chapter_id)).fetchone()
    db.close()
    if r is None:
        return None
    return {
        "pages_read": r["PagesRead"] or 0,
        "total_reads": r["TotalReads"] or 0,
        "book_scroll_id": r["BookScrollId"] or "",
    }


def upsert_progress(*, chapter_id: int, pages_read: int,
                    total_reads: int = 0, user_id: int = KAVITA_USER_ID,
                    book_scroll_id: str | None = None) -> None:
    """写一条新进度. PK 是 Id 单列, 无 UNIQUE 约束 — 用 SELECT/UPDATE/INSERT.

    `book_scroll_id` 传 None = 不动 BookScrollId (保留 Kavita 原生定位);
    传 "" = 清空; 传 xpath = 写进去。
    """
    dst = sqlite3.connect(KAVITA_DB)
    cur = dst.cursor()
    row = cur.execute("""
        SELECT ap.Id FROM AppUserProgresses ap
        JOIN Chapter c ON c.Id = ap.ChapterId
        WHERE ap.AppUserId=? AND ap.ChapterId=? LIMIT 1
    """, (user_id, chapter_id)).fetchone()
    if row:
        if book_scroll_id is None:
            cur.execute("""
                UPDATE AppUserProgresses
                SET PagesRead=?, TotalReads=?,
                    LastModified=datetime('now'), LastModifiedUtc=datetime('now')
                WHERE Id=?
            """, (pages_read, total_reads, row[0]))
        else:
            cur.execute("""
                UPDATE AppUserProgresses
                SET PagesRead=?, TotalReads=?, BookScrollId=?,
                    LastModified=datetime('now'), LastModifiedUtc=datetime('now')
                WHERE Id=?
            """, (pages_read, total_reads, book_scroll_id, row[0]))
    else:
        cur.execute("""
            INSERT INTO AppUserProgresses (AppUserId, ChapterId, PagesRead, TotalReads,
                                           BookScrollId, Created, CreatedUtc,
                                           LastModified, LastModifiedUtc,
                                           LibraryId, SeriesId, VolumeId)
            SELECT ?, c.Id, ?, ?, ?, datetime('now'), datetime('now'),
                   datetime('now'), datetime('now'),
                   s.LibraryId, v.SeriesId, c.VolumeId
            FROM Chapter c
            JOIN Volume v ON v.Id = c.VolumeId
            JOIN Series s ON s.Id = v.SeriesId
            WHERE c.Id = ?
        """, (user_id, pages_read, total_reads, book_scroll_id or None, chapter_id))
    dst.commit()
    dst.close()


if __name__ == "__main__":
    for p in iter_progress():
        print(p.series_id, p.volume_id, p.chapter_id, p.pages_read, "/", p.total_pages,
              "read=" + str(p.total_reads), "mod=" + p.last_modified, p.chapter_title[:30])
