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
    **只能写"当天"**: data 的日期 key 和 body.timestamp 都不决定归属,
    实测传 20261020/20261021 都落到上传当天 -> 历史回填协议层不可能。
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
from typing import Iterable

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
# Kavita -> 掌阅
# --------------------------------------------------------------------------

def push(day: date, *, dry_run: bool = False,
         series_ids: list[int] | None = None) -> dict:
    """把 Kavita 当天的会话时长写进掌阅 (分钟位图)。

    **只能写当天** (2026-10-24 实测): 服务端按"收到请求的那一天"归日,
    data 里的日期 key 与 body.timestamp 都不影响归属 —— 传 20261020/20261021
    的数据全都落到上传当天。所以历史回填在协议层就不可能, 必须当天推。

    服务端按分钟位图求并集 → 同一天重复推同一批分钟不会翻倍 (幂等),
    可以按小时/按需增量重推。
    """
    report: dict[str, Any] = {"day": day.isoformat(), "books": [], "written": 0,
                              "skipped": 0, "errors": []}
    if day != date.today():
        report["errors"].append(
            f"skip {day}: 掌阅只接受上传当天 (服务端按收到日归日), 见 notes/05 §2.4")
        return report
    links = [l for l in mapping.all_links() if not series_ids or l.kavita_series_id in series_ids]
    items = []
    for link in links:
        spans = sessions_minutes(link.kavita_series_id, day,
                                 link.kavita_chapter_id if link.kavita_chapter_id > 0 else None)
        if not spans:
            continue
        minutes = set()
        for a, b in spans:
            cur = a
            while cur < b:
                minutes.add(bit_index(cur.hour, cur.minute, cur.second))
                cur += timedelta(minutes=1)
        if not minutes:
            continue
        prev = _state_get(day, kb32(link.zy_book_id))
        if prev and int(prev["minutes"] or 0) == len(minutes):
            report["skipped"] += 1
            continue
        items.append((link, minutes))
        report["books"].append({"series": link.kavita_series_id, "minutes": len(minutes),
                                "name": link.zy_book_name})

    if dry_run or not items:
        report["written"] = len(items)
        return report

    data: dict[str, list] = {}
    for link, minutes in items:
        data.setdefault(day.strftime("%Y%m%d"), []).append({
            "bid": 0,                       # 云端数字 bookid 未知, 靠 local_bid (内容 MD5)
            "local_bid": book_key(link.zy_book_id),   # 大写 MD5_local, 大小写不同会分桶
            "read_percent": pct_for_series(link.kavita_series_id),
            "res": "R",
            "d": bitmap_from_minutes(sorted(minutes)),
        })
    payload = {"usr": load_zy_auth()["usr"],
               "data": json.dumps(data, ensure_ascii=False),
               "timestamp": str(int(time.time() * 1000)),
               "sign": ""}
    auth = load_zy_auth()
    qs = urllib.parse.urlencode(zy_query_params(auth, {}))
    body = gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    req = urllib.request.Request(f"{UPLOAD_URL}?{qs}", data=body, method="POST")
    req.add_header("Content-Type", "application/octet-stream")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        res = json.loads(raw.decode("utf-8", "replace"))
        if res.get("code") not in (0, None):
            report["errors"].append(f"reading_time/report: {res}")
        else:
            for link, minutes in items:
                _state_put(day, kb32(link.zy_book_id), link.kavita_series_id,
                           len(minutes), "push", None)
                report["written"] += 1
    except Exception as e:
        report["errors"].append(f"reading_time/report: {e}")
    return report


def sync(day: date, *, dry_run: bool = False) -> dict:
    return {"push": push(day, dry_run=dry_run), "pull": pull(day, day, dry_run=dry_run)}
