"""阅读时长 双向同步: Kavita <-> 掌阅

APK 依据见 notes/05-notes-time-apk-v2.md §2。要点:

**书身份 (跨设备一致)** —— 实测 2026-10-24:
    商店书: bid = 数字 bookid (全局唯一)
    自传书: bid = 0, 书身份 = 文件内容 MD5(32hex) + "_local" (大写)
    所以同一本文件在手机/平板上同一个桶, 多设备时长天然合并。
    **大小写敏感**: 小写会落到另一个空桶 (踩过)。

**读 (掌阅 -> Kavita) 精确到每天每书**:
    GET webboot.zhangyue.com/usercenter/user/read_report/book?book_id=<MD5>_local
    body.daybyday_times[].detail[] = {read_day, read_minutes}   <- 每天每书
    另有 read / read_progress / read_days_cnt / book_info(名字/封面)

**写 (Kavita -> 掌阅)**:
    POST https://uc.ireader.com/postal/reading_time/report   (gzip)
    {"usr":..., "data":"<json 字符串>", "timestamp":..., "sign":"..."}
    data = {"20261024":[{"bid":<int>,"local_bid":"<32hex>_local",
                         "read_percent":0.27,"res":"R","d":"<base64 180B>"}]}
    d = 1440 分钟位图, 位下标 = hour*60+minute-1, **字节内 LSB 优先**
    sign 服务端不校验
    请求模型完全照抄 APK (od/a.java): 本地队列 = 每 书×每天 一行 (180B 位图),
    按日分组一次上传, 30 天内失败可重传, 只有 code==0 才删行。见 "Kavita -> 掌阅" 段。
    **服务端归账日 = 收到请求那天**: data 的日期 key 与 body.timestamp 都不影响归属
    (2026-10-24 六次实测: 20261016/17/18/20/22/23 全部落到上传当天, 20261025 静默丢弃)。
    所以队列的价值是"同一天内重试 + 协议保真"; 跨天补录会被并入收到日 —— 服务端行为,
    客户端无从绕过 (除非能签 sign, 那是 native RSA)。详见 notes/05 §2.5。
    服务端按分钟求并集 -> 同一天重复推幂等。

Kavita 侧: AppUserReadingSession + AppUserReadingSessionActivityData(SeriesId)。
没有写 API, 直写 sqlite。
"""
from __future__ import annotations

import base64
import gzip
import json
import sqlite3
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from . import kavita_db, mapping
from .config import KAVITA_DB, KAVITA_USER_ID, load_zy_auth, zy_query_params
from .zy_notes import _conn as _state_conn, kb32

REPORT_URL = "https://webboot.zhangyue.com/usercenter/user/read_report/statistics"
BOOK_URL = "https://webboot.zhangyue.com/usercenter/user/read_report/book"
UPLOAD_URL = "https://uc.ireader.com/postal/reading_time/report"
BITMAP_BYTES = 180


# --------------------------------------------------------------------------
# 位图
# --------------------------------------------------------------------------

def bit_index(hour: int, minute: int, second: int = 0) -> int:
    """APK od/a.w(): hour*60+minute+(sec>0?1:0)-1"""
    return hour * 60 + minute + (1 if second > 0 else 0) - 1


def bitmap_from_minutes(minutes: Iterable[int]) -> str:
    buf = bytearray(BITMAP_BYTES)
    for m in minutes:
        if 0 <= m < BITMAP_BYTES * 8:
            buf[m >> 3] |= 1 << (m & 7)          # LSB 优先
    return base64.b64encode(bytes(buf)).decode()


def minutes_from_bitmap(b64: str) -> list[int]:
    raw = base64.b64decode(b64 or "")
    out = []
    for i, b in enumerate(raw):
        for bit in range(8):
            if b & (1 << bit):
                out.append(i * 8 + bit)
    return out


def bitmap_from_span(start: datetime, end: datetime) -> str:
    """[start, end) 之间的分钟 -> 位图。"""
    minutes = set()
    cur = start
    while cur < end:
        minutes.add(bit_index(cur.hour, cur.minute, cur.second))
        cur += timedelta(minutes=1)
    return bitmap_from_minutes(sorted(minutes))


# --------------------------------------------------------------------------
# 掌阅 read_report (读)
# --------------------------------------------------------------------------

def _get(url: str, extra: dict) -> dict:
    auth = load_zy_auth()
    qs = urllib.parse.urlencode(zy_query_params(auth, extra))
    with urllib.request.urlopen(f"{url}?{qs}", timeout=30) as r:
        raw = r.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8", "replace"))


def week_report(start_date: str, end_date: str) -> dict:
    d = _get(REPORT_URL, {"date_type": "week",
                          "start_date": start_date, "end_date": end_date})
    return d.get("body") or {}


def week_bounds(day: date) -> tuple[date, date]:
    mon = day - timedelta(days=day.weekday())
    return mon, mon + timedelta(days=6)


def account_days(start: date, end: date) -> dict[str, int]:
    """{yyyy-mm-dd: 分钟} — 逐周拉。"""
    out: dict[str, int] = {}
    cur = start - timedelta(days=start.weekday())
    while cur <= end:
        mon, sun = cur, cur + timedelta(days=6)
        try:
            body = week_report(mon.isoformat(), sun.isoformat())
        except Exception:
            cur += timedelta(days=7)
            continue
        for item in body.get("reading_distribution") or []:
            k, v = item.get("key"), item.get("value")
            if k and isinstance(v, int):
                out[k] = out.get(k, 0) + v
        cur += timedelta(days=7)
    return {k: v for k, v in out.items() if start.isoformat() <= k <= end.isoformat()}


def week_books(start_date: str, end_date: str) -> list[dict]:
    body = week_report(start_date, end_date)
    return body.get("reading_longest_books") or []


# --------------------------------------------------------------------------
# 按书读回 (read_report/book) —— 精确到"每天每书"
# --------------------------------------------------------------------------
#
# 掌阅服务端按 (usr, 书, 日) 存阅读分钟。书身份 = local_bid:
#   bid != 0 (商店书)  -> 数字 bookid
#   bid == 0 (自传书) -> 文件内容 MD5(32hex) [+ "_local"], **跨设备一致**
# 所以 read_report/book?book_id=<md5>_local 能精确拿到该书每天的分钟数,
# 不需要按天总量去分摊 (旧实现只能周级每书, 是探测不到位, 不是接口缺失)。

def book_key(book32: str) -> str:
    """掌阅 read_report/book 用的书 key (自传书 = MD5_local, **大写**, 大小写不同会落到不同桶)。"""
    return f"{kb32(book32).upper()}_local"


def book_report(key: str) -> dict:
    return _get(BOOK_URL, {"book_id": key}).get("body") or {}


def book_days(key: str) -> dict[str, int]:
    """{ 'YYYY-MM-DD': minutes } —— 该书逐日阅读分钟。"""
    body = book_report(key)
    out: dict[str, int] = {}
    for month in body.get("daybyday_times") or []:
        mon = month.get("read_month") or ""
        if len(mon) != 7:
            continue
        for d in month.get("detail") or []:
            try:
                day = int(d.get("read_day") or 0)
            except (TypeError, ValueError):
                continue
            if not day:
                continue
            out[f"{mon}-{day:02d}"] = int(d.get("read_minutes") or 0)
    return out


def book_day_bitmap_unavailable(key: str) -> bool:
    """占位: read_report/book 只给分钟数, 不给"哪一分钟"。"""
    return True


# --------------------------------------------------------------------------
# Kavita 会话读写
# --------------------------------------------------------------------------

def sessions_minutes(series_id: int, day: date,
                     chapter_id: int | None = None) -> list[tuple[datetime, datetime]]:
    """该系列 (可选: 该章/卷) 某天的会话区间 (本地时间)。"""
    import tempfile
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    src = sqlite3.connect(f"file:{KAVITA_DB}?mode=ro", uri=True)
    dst = sqlite3.connect(tmp)
    src.backup(dst)
    src.close()
    dst.row_factory = sqlite3.Row
    sql = """
        SELECT s.StartTime, s.EndTime FROM AppUserReadingSession s
        JOIN AppUserReadingSessionActivityData a ON a.AppUserReadingSessionId = s.Id
        WHERE a.SeriesId=? AND s.StartTime LIKE ? AND s.EndTime IS NOT NULL
          AND COALESCE(s.IsGenerated, 0) = 0      -- 排除本工具造的会话, 否则 pull→push 回声
    """
    args: list = [series_id, day.isoformat() + "%"]
    if chapter_id:                                # 多卷系列只取该卷的会话
        sql += "  AND a.ChapterId=?"
        args.append(int(chapter_id))
    sql += " GROUP BY s.Id"
    rows = dst.execute(sql, args).fetchall()
    dst.close()
    out = []
    for r in rows:
        try:
            a = datetime.fromisoformat(str(r["StartTime"])[:19])
            b = datetime.fromisoformat(str(r["EndTime"])[:19])
        except ValueError:
            continue
        out.append((a, b))
    return out


def pct_for_series(series_id: int) -> float:
    try:
        for p in kavita_db.iter_progress():
            if p.series_id == series_id:
                return round(p.percent / 100.0, 6) if p.percent > 1 else round(p.percent, 6)
    except Exception:
        pass
    return 0.0


def write_session(*, series_id: int, chapter_id: int, day: date, minutes: int,
                  start_hour: int = 20) -> int:
    """造一条 Kavita 会话 (IsGenerated=1) + ActivityData(SeriesId)。"""
    start = datetime.combine(day, datetime.min.time()).replace(hour=start_hour)
    end = start + timedelta(minutes=max(1, minutes))
    local = lambda d: d.strftime("%Y-%m-%d %H:%M:%S.%f")
    utc = lambda d: (d - datetime.now().astimezone().utcoffset()).strftime("%Y-%m-%d %H:%M:%S.%f")
    d = sqlite3.connect(KAVITA_DB)
    cur = d.cursor()
    cur.execute("""INSERT INTO AppUserReadingSession
        (StartTime, StartTimeUtc, EndTime, EndTimeUtc, IsActive, Created, CreatedUtc,
         LastModified, LastModifiedUtc, AppUserId, IsGenerated)
        VALUES (?,?,?,?,0,?,?,?,?,?,1)""",
                (local(start), utc(start), local(end), utc(end),
                 local(start), utc(start), local(start), utc(start), KAVITA_USER_ID))
    sid = int(cur.lastrowid)
    info = json.dumps({"ClientType": 3, "Source": "iReader", "Generated": True},
                      ensure_ascii=False)
    cur.execute("""INSERT INTO AppUserReadingSessionActivityData
        (AppUserReadingSessionId, ChapterId, ClientInfo, DeviceIds, EndPage,
         EndTime, EndTimeUtc, Format, LibraryId, PagesRead, SeriesId, StartPage,
         StartTime, StartTimeUtc, TotalPages, TotalWords, VolumeId, WordsRead)
        SELECT ?, ?, ?, '[]', 0, ?, ?, 3, s.LibraryId, 0, v.SeriesId, 0,
               ?, ?, c.Pages, 0, c.VolumeId, 0
        FROM Chapter c JOIN Volume v ON v.Id=c.VolumeId JOIN Series s ON s.Id=v.SeriesId
        WHERE c.Id=?""",
                (sid, chapter_id, info, local(end), utc(end),
                 local(start), utc(start), chapter_id))
    d.commit()
    d.close()
    return sid


def _state_get(day: date, book32: str):
    c = _state_conn()
    r = c.execute("SELECT * FROM zy_time_link WHERE day=? AND zy_book_id=?",
                  (day.isoformat(), book32)).fetchone()
    c.close()
    return r


def _state_put(day: date, book32: str, series_id: int, minutes: int,
               mode: str, session_id: int | None) -> None:
    c = _state_conn()
    c.execute("""INSERT INTO zy_time_link (day, zy_book_id, kavita_series_id, minutes,
                                           mode, session_id, updated_utc)
                 VALUES (?,?,?,?,?,?,datetime('now'))
                 ON CONFLICT(day, zy_book_id) DO UPDATE SET
                   minutes=excluded.minutes, kavita_series_id=excluded.kavita_series_id,
                   mode=excluded.mode, session_id=excluded.session_id,
                   updated_utc=datetime('now')""",
              (day.isoformat(), book32, series_id, minutes, mode, session_id))
    c.commit()
    c.close()


# --------------------------------------------------------------------------
# 掌阅 -> Kavita
# --------------------------------------------------------------------------

def _chapter_id_of(link: mapping.BookLink) -> int:
    if link.kavita_chapter_id and link.kavita_chapter_id > 0:
        return int(link.kavita_chapter_id)
    chs = kavita_db.list_chapters(link.kavita_series_id) or [{"ChId": 0}]
    return int(chs[0]["ChId"])


def _delete_session(session_id: int | None) -> None:
    """删掉我们自己造的那条 Kavita 会话 (只删 IsGenerated=1 的)。"""
    if not session_id:
        return
    d = sqlite3.connect(KAVITA_DB)
    cur = d.cursor()
    cur.execute("DELETE FROM AppUserReadingSessionActivityData WHERE AppUserReadingSessionId=?",
                (int(session_id),))
    cur.execute("DELETE FROM AppUserReadingSession WHERE Id=? AND IsGenerated=1",
                (int(session_id),))
    d.commit()
    d.close()


def _put_day(link: mapping.BookLink, day: date, minutes: int, mode: str,
             report: dict[str, Any], dry_run: bool) -> None:
    prev = _state_get(day, kb32(link.zy_book_id))
    if prev and int(prev["minutes"] or 0) == minutes and (prev["mode"] or "") == mode:
        report["skipped"] = report.get("skipped", 0) + 1
        return
    report["written"] += 1
    report["mode"][mode] = report["mode"].get(mode, 0) + 1
    if dry_run:
        return
    if prev and prev["session_id"]:          # 分钟数变了 -> 换掉旧会话, 不叠加
        _delete_session(int(prev["session_id"]))
    sid = write_session(series_id=link.kavita_series_id,
                        chapter_id=_chapter_id_of(link), day=day, minutes=minutes)
    _state_put(day, kb32(link.zy_book_id), link.kavita_series_id, minutes, mode, sid)


def pull(start: date, end: date, *, dry_run: bool = False,
         series_ids: list[int] | None = None) -> dict:
    """掌阅 -> Kavita 阅读时长。

    **主路径 (精确)**: 逐书 `read_report/book?book_id=<md5>_local` → 每天每书分钟,
    mode="book" (服务端原始数据, 不是估算)。
    **兜底 (估算)**: 该书在该区间取不到逐日数据时, 退回周级 `reading_longest_books`
    按天总量比例分摊, mode="alloc"; 周内只有一本则 mode="exact"。
    """
    report: dict[str, Any] = {"days": {}, "books": [], "written": 0, "skipped": 0,
                              "mode": {}, "errors": [], "exact_books": 0}
    links = [l for l in mapping.all_links() if not series_ids or l.kavita_series_id in series_ids]

    # ---- 1) 逐书逐日 (精确) ----
    need_fallback: list[mapping.BookLink] = []
    for link in links:
        key = book_key(link.zy_book_id)
        try:
            days = book_days(key)
        except Exception as e:                       # 网络/风控 -> 记一笔, 走兜底
            report["errors"].append(f"book {key}: {e}")
            need_fallback.append(link)
            continue
        rows = {d: m for d, m in days.items()
                if start.isoformat() <= d <= end.isoformat() and m > 0}
        if not rows:
            need_fallback.append(link)
            continue
        report["exact_books"] += 1
        total = 0
        for day_s, minutes in sorted(rows.items()):
            _put_day(link, date.fromisoformat(day_s), minutes, "book", report, dry_run)
            total += minutes
        report["books"].append({"series": link.kavita_series_id,
                                "name": link.zy_book_name,
                                "minutes": total, "mode": "book",
                                "days": len(rows)})

    # ---- 2) 兜底: 周级 → 按天分摊 (只处理取不到逐日数据的书) ----
    if need_fallback:
        return _pull_alloc(need_fallback, start, end, report, dry_run)
    try:
        report["days"] = account_days(start, end)      # 账号总量, 仅供参考/核对
    except Exception as e:
        report["errors"].append(f"account days: {e}")
    return report


def _pull_alloc(links: list[mapping.BookLink], start: date, end: date,
                report: dict[str, Any], dry_run: bool) -> dict:
    by_name = {l.zy_book_name: l for l in links}
    by_id32 = {kb32(l.zy_book_id): l for l in links}

    cur = start - timedelta(days=start.weekday())
    while cur <= end:
        mon, sun = cur, cur + timedelta(days=6)
        try:
            body = week_report(mon.isoformat(), sun.isoformat())
        except Exception as e:
            report["errors"].append(f"week {mon}: {e}")
            cur += timedelta(days=7)
            continue
        days = {i.get("key"): int(i.get("value") or 0)
                for i in (body.get("reading_distribution") or []) if i.get("key")}
        days = {k: v for k, v in days.items()
                if start.isoformat() <= k <= end.isoformat() and v > 0}
        books = []
        for b in (body.get("reading_longest_books") or []):
            link = by_id32.get(kb32(b.get("book_id") or "")) or by_name.get(b.get("book_name") or "")
            if not link or not b.get("reading_time"):
                continue
            books.append((link, int(b["reading_time"]), b.get("book_name")))

        for day_s, minutes in sorted(days.items()):
            report["days"][day_s] = minutes
        if not books or not days:
            cur += timedelta(days=7)
            continue

        total_book_min = sum(m for _, m, _ in books)
        day_total = sum(days.values()) or 1
        single = len(books) == 1
        report.setdefault("unattributed_minutes", 0)
        report["unattributed_minutes"] += max(0, day_total - total_book_min)
        for link, minutes, name in books:
            alloc: dict[str, int] = {}
            if single:
                alloc = dict(days)
            else:
                # 该书的**周内分钟数**按天总量比例摊到每天 (sum 守恒, 不放大)
                for day_s, dm in days.items():
                    alloc[day_s] = int(round(minutes * dm / day_total))
                diff = minutes - sum(alloc.values())
                if diff and alloc:
                    top = max(alloc, key=lambda k: alloc[k])
                    alloc[top] += diff
            for day_s, m in alloc.items():
                if m <= 0:
                    continue
                mode = "exact" if single else "alloc"
                _put_day(link, date.fromisoformat(day_s), m, mode, report, dry_run)
            report["books"].append({"series": link.kavita_series_id, "name": name,
                                    "week_minutes": minutes,
                                    "mode": "exact" if single else "alloc"})
        cur += timedelta(days=7)
    return report


# --------------------------------------------------------------------------
# Kavita -> 掌阅: 照抄 APK 的推送模型 (od/a.java)
# --------------------------------------------------------------------------
#
# APK 的模型 (apk/jadx/sources/od/a.java, notes/05 §2.5):
#   * 本地表 ReadTimeInfo = 每 **书 × 每天** 一行
#       mFormatDate = yyyyMMdd (本地日), mData = base64(180 字节分钟位图)
#     读到新分钟就按位并集累加 (j0() 数位), 不是每次插新行
#   * I() 取该书本全部行 → 按 mFormatDate 分组 → data = {"20261017":[...], "20261018":[...]}
#     **一次请求可以带多个日期** —— 离线多天累积后一起重传
#   * 30 天 (U = 2592000000ms) 没上传成功的行直接丢弃
#   * 只有 code==0 才删本地行 (od/a.java:250-262); 失败留着重传
#   * 触发: 翻页 / 息屏 / 关书 / 切前后台 → q0()
#   * timestamp 是"现在" (account/d.a: System.currentTimeMillis()), 日期只由 data 的 key 表达
#
# 我们照抄:
#   zy_time_queue(zy_book_id, day, bitmap, read_percent, tries)   ← ReadTimeInfo
#   enqueue(day)   把 Kavita 会话的分钟并进队列行 (同一天多次 → 位图并集)
#   flush()        按日分组一次 POST, 成功才删行 (失败留队, tries+1)
#   backfill(a,b)  离线多天补录 (= APK 重连后首次上传)
#
# 服务端实测 (notes/05 §2.5, 2026-10-24 六次): 日期 key 与 body.timestamp **都不影响归账日**,
# 一律记到"收到请求那天"; 未来日期静默丢弃。所以队列的价值是
#   ① 同一天内的失败重试  ② 协议保真 (请求形态与 APK 一致)
# 跨天补录会被服务端并入收到日 —— 服务端行为, 客户端无从绕过 (除非能签 sign, 那是 native RSA)。

QUEUE_RETENTION_DAYS = 30          # APK od/a.java: U = 2592000000L
RES_READ = "R"                     # APK Q(1) = "R" (书籍); 听书 L / 大咖 C / 漫画 CT / TTS T


def _minutes_bits(link: mapping.BookLink, day: date) -> set[int]:
    """该映射某天的会话分钟 -> 位图位下标集合。"""
    spans = sessions_minutes(link.kavita_series_id, day,
                             link.kavita_chapter_id if link.kavita_chapter_id > 0 else None)
    bits: set[int] = set()
    for a, b in spans:
        cur = a
        while cur < b:
            bits.add(bit_index(cur.hour, cur.minute, cur.second))
            cur += timedelta(minutes=1)
    return bits


def _pct_for_day(link: mapping.BookLink, day: date) -> float:
    """当天最后一次阅读时的进度 (APK 是"那天的 read_percent", 不是当下的)。

    取当日最后一条会话的 EndPage/TotalPages; 拿不到就退回当前系列进度。
    """
    import tempfile
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    src = sqlite3.connect(f"file:{KAVITA_DB}?mode=ro", uri=True)
    dst = sqlite3.connect(tmp)
    src.backup(dst)
    src.close()
    try:
        sql = """SELECT a.EndPage, a.TotalPages
                   FROM AppUserReadingSession s
                   JOIN AppUserReadingSessionActivityData a
                        ON a.AppUserReadingSessionId = s.Id
                  WHERE a.SeriesId=? AND s.StartTime LIKE ?
                    AND COALESCE(a.TotalPages,0) > 0 AND COALESCE(a.EndPage,0) > 0"""
        args: list = [link.kavita_series_id, day.isoformat() + "%"]
        if link.kavita_chapter_id and link.kavita_chapter_id > 0:
            sql += " AND a.ChapterId=?"
            args.append(int(link.kavita_chapter_id))
        sql += " ORDER BY s.EndTime DESC LIMIT 1"
        row = dst.execute(sql, args).fetchone()
    finally:
        dst.close()
    if row:
        end, total = int(row[0] or 0), int(row[1] or 0)
        if total > 0 and 0 <= end <= total:
            return round(end / total, 6)
    return pct_for_series(link.kavita_series_id)


def _bitmap_union(b64: str | None, minutes: Iterable[int]) -> tuple[str, int]:
    """位图并集 (APK j0 的累加语义), 返回 (base64, 置位数)。"""
    try:
        buf = bytearray(base64.b64decode(b64)) if b64 else bytearray(BITMAP_BYTES)
    except Exception:
        buf = bytearray(BITMAP_BYTES)
    if len(buf) != BITMAP_BYTES:
        buf = bytearray(BITMAP_BYTES)
    for m in minutes:
        if 0 <= m < BITMAP_BYTES * 8:
            buf[m >> 3] |= 1 << (m & 7)
    return base64.b64encode(bytes(buf)).decode(), sum(bin(b).count("1") for b in buf)


def queue_info(series_ids: list[int] | None = None) -> list[dict]:
    c = _state_conn()
    rows = c.execute("SELECT * FROM zy_time_queue ORDER BY day, zy_book_id").fetchall()
    c.close()
    out = []
    for r in rows:
        if series_ids and r["kavita_series_id"] not in series_ids:
            continue
        out.append({"book": r["zy_book_id"], "day": r["day"],
                    "series": r["kavita_series_id"], "minutes": r["minutes"],
                    "tries": r["tries"], "last_try": r["last_try"]})
    return out


def _queue_put(book32: str, day: date, series_id: int, bitmap: str, minutes: int,
               read_percent: float) -> None:
    c = _state_conn()
    c.execute("""INSERT INTO zy_time_queue
                   (zy_book_id, day, kavita_series_id, bitmap, minutes, read_percent)
                 VALUES (?,?,?,?,?,?)
                 ON CONFLICT(zy_book_id, day) DO UPDATE SET
                   bitmap=excluded.bitmap, minutes=excluded.minutes,
                   read_percent=excluded.read_percent,
                   kavita_series_id=excluded.kavita_series_id""",
              (book32, day.isoformat(), series_id, bitmap, minutes, read_percent))
    c.commit()
    c.close()


def _queue_del(book32: str, day: str) -> None:
    c = _state_conn()
    c.execute("DELETE FROM zy_time_queue WHERE zy_book_id=? AND day=?", (book32, day))
    c.commit()
    c.close()


def _queue_mark_try(book32: str, day: str) -> None:
    c = _state_conn()
    c.execute("""UPDATE zy_time_queue
                    SET tries = tries + 1, last_try = datetime('now')
                  WHERE zy_book_id=? AND day=?""", (book32, day))
    c.commit()
    c.close()


def _queue_drop_old(today: date | None = None) -> int:
    """丢 >30 天的旧行 (APK I() 里的 fixedTimeStamp - U)。"""
    today = today or date.today()
    cut = (today - timedelta(days=QUEUE_RETENTION_DAYS)).isoformat()
    c = _state_conn()
    cur = c.execute("DELETE FROM zy_time_queue WHERE day < ?", (cut,))
    n = cur.rowcount or 0
    c.commit()
    c.close()
    return n


def enqueue(day: date, *, series_ids: list[int] | None = None,
            dry_run: bool = False) -> dict:
    """把 Kavita 某天的会话分钟并进队列 (对应 APK j0 累加, 幂等)。

    同一天重复调用只是位图并集, 不会翻倍。
    """
    report: dict[str, Any] = {"day": day.isoformat(), "books": [], "queued": 0,
                              "skipped": 0, "errors": []}
    if day > date.today():
        report["errors"].append(f"{day} 是未来日期, 掌阅会静默丢弃, 拒绝入队")
        return report
    links = [l for l in mapping.all_links() if not series_ids or l.kavita_series_id in series_ids]
    c = _state_conn()
    rows = {(r["zy_book_id"], r["day"]): r
            for r in c.execute("SELECT * FROM zy_time_queue").fetchall()}
    c.close()
    for link in links:
        bits = _minutes_bits(link, day)
        if not bits:
            continue
        book32 = kb32(link.zy_book_id)
        prev = rows.get((book32, day.isoformat()))
        bitmap, count = _bitmap_union(prev["bitmap"] if prev else None, sorted(bits))
        if prev and int(prev["minutes"] or 0) == count:
            report["skipped"] += 1
            continue
        pct = _pct_for_day(link, day)
        if not dry_run:
            _queue_put(book32, day, link.kavita_series_id, bitmap, count, pct)
        report["queued"] += 1
        report["books"].append({"series": link.kavita_series_id,
                                "name": link.zy_book_name,
                                "minutes": count,
                                "added": count - int(prev["minutes"] or 0) if prev else count})
    return report


def _post_report(data: dict) -> dict:
    """reading_time/report 单次 POST (APK q0() 的形态: gzip body, usr+data+timestamp+sign)。

    data = {"20261024": [{"bid","local_bid","read_percent","res","d"}, ...]}
    签名 (sign) 由 native RSA 生成, 我们留空 —— 服务端不校验。
    """
    payload = {"usr": load_zy_auth()["usr"],
               "data": json.dumps(data, ensure_ascii=False),
               "timestamp": str(int(time.time() * 1000)),
               "sign": ""}
    auth = load_zy_auth()
    qs = urllib.parse.urlencode(zy_query_params(auth, {}))
    body = gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    req = urllib.request.Request(f"{UPLOAD_URL}?{qs}", data=body, method="POST")
    req.add_header("Content-Type", "application/octet-stream")
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8", "replace"))


def flush(*, dry_run: bool = False) -> dict:
    """把队列按日分组一次 POST (APK I() + q0()), 成功才删行。"""
    report: dict[str, Any] = {"days": [], "entries": 0, "cleared": 0,
                              "kept": 0, "errors": []}
    _queue_drop_old()
    c = _state_conn()
    rows = c.execute("SELECT * FROM zy_time_queue ORDER BY day").fetchall()
    c.close()
    if not rows:
        return report
    groups: dict[str, list] = {}
    for r in rows:
        try:
            day = date.fromisoformat(r["day"])
        except ValueError:
            _queue_del(r["zy_book_id"], r["day"])
            continue
        if day > date.today():                     # 未来日期: 服务端丢, 别留着占位
            _queue_del(r["zy_book_id"], r["day"])
            continue
        groups.setdefault(day.strftime("%Y%m%d"), []).append(r)
    if not groups:
        return report
    report["days"] = sorted(groups)
    report["entries"] = sum(len(v) for v in groups.values())
    data = {k: [{"bid": 0,
                 "local_bid": book_key(r["zy_book_id"]),
                 "read_percent": float(r["read_percent"] or 0.0),
                 "res": RES_READ,
                 "d": r["bitmap"]} for r in v]
            for k, v in groups.items()}
    if dry_run:
        report["payload"] = data
        return report
    try:
        res = _post_report(data)
    except Exception as e:                          # 网络/风控: 全部留队
        report["errors"].append(f"reading_time/report: {e}")
        for v in groups.values():
            for r in v:
                _queue_mark_try(r["zy_book_id"], r["day"])
        report["kept"] = report["entries"]
        return report
    if res.get("code") not in (0, None):
        report["errors"].append(f"reading_time/report: {res}")
        for v in groups.values():
            for r in v:
                _queue_mark_try(r["zy_book_id"], r["day"])
        report["kept"] = report["entries"]
        return report
    for v in groups.values():                       # 只有 code==0 才删 (APK 同规则)
        for r in v:
            _queue_del(r["zy_book_id"], r["day"])
            _state_put(date.fromisoformat(r["day"]), r["zy_book_id"],
                       r["kavita_series_id"], int(r["minutes"] or 0), "push", None)
            report["cleared"] += 1
    report["resp"] = res.get("body")
    return report


def push(day: date | None = None, *, dry_run: bool = False,
         series_ids: list[int] | None = None) -> dict:
    """入队某天 + flush (APK 的"读的时候顺手传")。

    返回结构兼容旧调用方: day / books / written / skipped / errors (+ queued / flush)。
    历史日期不再被 today-only 闸门拒绝 —— APK 就是这么发的 (30 天内都传);
    服务端按收到日归账, 见 notes/05 §2.5。
    """
    day = day or date.today()
    enq = enqueue(day, series_ids=series_ids, dry_run=dry_run)
    report: dict[str, Any] = {"day": day.isoformat(), "books": enq["books"],
                              "queued": enq["queued"], "skipped": enq["skipped"],
                              "written": 0, "errors": list(enq["errors"])}
    if dry_run:                       # 只看: 今天能入队多少 + 队列里还压着什么
        report["pending"] = queue_info(series_ids)
        return report
    res = flush()
    report["flush"] = res
    report["written"] = int(res.get("cleared") or 0)
    report["errors"].extend(res.get("errors") or [])
    return report


def backfill(start: date, end: date, *, dry_run: bool = False,
             series_ids: list[int] | None = None) -> dict:
    """离线多天补录: 逐日入队 → 一次 flush (等价 APK 重连后首次上传)。

    注意: 服务端实测按"收到日"归账, 历史 key 会被并入今天 (notes/05 §2.5)。
    """
    out: dict[str, Any] = {"range": [start.isoformat(), end.isoformat()],
                           "days": [], "queued": 0, "skipped": 0,
                           "written": 0, "errors": []}
    cur = start
    while cur <= end:
        enq = enqueue(cur, series_ids=series_ids, dry_run=dry_run)
        out["days"].append({"day": cur.isoformat(), "queued": enq["queued"],
                            "skipped": enq["skipped"]})
        out["queued"] += enq["queued"]
        out["skipped"] += enq["skipped"]
        out["errors"].extend(enq["errors"])
        cur += timedelta(days=1)
    if dry_run:
        out["pending"] = queue_info(series_ids)
        return out
    res = flush()
    out["flush"] = res
    out["written"] = int(res.get("cleared") or 0)
    out["errors"].extend(res.get("errors") or [])
    return out


def sync(day: date, *, dry_run: bool = False) -> dict:
    return {"push": push(day, dry_run=dry_run), "pull": pull(day, day, dry_run=dry_run)}
