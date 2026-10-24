"""zy_notes / zy_time 的离线自检 (不联网, 不写 Kavita)。

    python3 tests/test_notes_time.py
"""
import sys
from datetime import date, datetime

sys.path.insert(0, "/vol2/1001/zhangyue-bridge/sync")

from sync import zy_notes, zy_time, zpct, mapping, kpages

FAIL = 0


def check(name, cond, extra=""):
    global FAIL
    print(("PASS " if cond else "FAIL ") + name + (" " + str(extra) if extra and not cond else ""))
    if not cond:
        FAIL += 1


# --- 身份 / 位置 -----------------------------------------------------------
check("kb32 截 32 位", zy_notes.kb32("70776A64C32C1C065DAB241F3E9F133A55C7D8") ==
      "70776a64c32c1c065dab241f3e9f133a")

ps = "107_0_0$##3-0-107-66812-0##$"
pe = "107_0_0$##3-0-107-66860-0##$"
key = zy_notes.note_key(zy_notes.kb32("A" * 64), ps, pe)
check("note_key 结构", key == ("a" * 32) + "_" + ps + pe)
check("parse_position", zy_notes.parse_chapter_offset(ps) == (106, 66812), zy_notes.parse_chapter_offset(ps))
check("build_position 往返", zpct.build_position(106, 66812) == ps, zpct.build_position(106, 66812))

# --- 位图 ---------------------------------------------------------------
bm = zy_time.bitmap_from_minutes([0, 1, 7, 8, 1439])
check("bitmap 180 字节", len(__import__("base64").b64decode(bm)) == 180)
check("bitmap 往返", zy_time.minutes_from_bitmap(bm) == [0, 1, 7, 8, 1439])
check("bit_index 20:30", zy_time.bit_index(20, 30, 0) == 1229)
check("bit_index 秒进位", zy_time.bit_index(20, 30, 15) == 1230)


def _bm_span(h1, m1, h2, m2):
    a = datetime(2026, 10, 24, h1, m1)
    b = datetime(2026, 10, 24, h2, m2)
    return len(zy_time.minutes_from_bitmap(zy_time.bitmap_from_span(a, b)))


check("span 30 分钟", _bm_span(20, 0, 20, 30) == 30, _bm_span(20, 0, 20, 30))
check("span 跨小时", _bm_span(21, 50, 22, 5) == 15, _bm_span(21, 50, 22, 5))

# --- 几何 / 页映射 (剑来真实数据) ------------------------------------------
link = mapping.get(27)
if link:
    geo = zy_notes.geometry(link)
    check("剑来 geometry", geo is not None and len(geo.spine) == 590)
    sc = kpages.load(27)
    check("剑来 sidecar", bool(sc) and len(sc["pages"]) == 3000)
    # 已知云端划线: chapterId 106 (spine 107), f3 66812
    g = zy_notes.pos_to_global(geo, ps)
    back = zy_notes.global_to_pos(geo, g)
    check("字节↔位置 往返", back == ps, back)
    page, rel, raw, inner = zy_notes._locate(geo, zy_notes._orig_epub(link), sc, g, "哈哈，家乡便是那坛老酒了")
    check("定位到页", page == 568, page)          # 0-based 567 -> 1-based 568
    check("xpath 命中段落", rel.startswith("//body/div"), rel)
    check("页内含原文", "哈哈" in raw.decode("utf-8", "replace"))
    # 反向: Kavita 注解文本 -> 位置
    g2 = zy_notes._find_in_book(zy_notes._orig_epub(link), "哈哈，家乡便是那坛老酒了")
    check("文本反查同一位置", g2 == g, (g2, g))
else:
    print("SKIP 剑来映射不存在")

# --- 分摊守恒 (纯算术, 照抄 pull 里的公式) --------------------------------
def alloc_book(minutes, days):
    day_total = sum(days.values()) or 1
    out = {d: int(round(minutes * v / day_total)) for d, v in days.items()}
    diff = minutes - sum(out.values())
    if diff:
        top = max(out, key=lambda k: out[k])
        out[top] += diff
    return out


days = {"2026-10-19": 9, "2026-10-21": 11, "2026-10-22": 31, "2026-10-23": 61, "2026-10-24": 9}
a = alloc_book(30, days)
b = alloc_book(14, days)
check("分摊 守恒 剑来", sum(a.values()) == 30, a)
check("分摊 守恒 狐", sum(b.values()) == 14, b)
check("分摊 不超过天总量", all(a[d] <= days[d] for d in days), a)

# --- 阅读时长: 逐书逐日读回 (read_report/book, 2026-10-24 打通) -------------
check("book_key 大写 + _local",
      zy_time.book_key("70776A64C32C1C065DAB241F3E9F133AFFFFFFFF") ==
      "70776A64C32C1C065DAB241F3E9F133A_local", zy_time.book_key("70776A64C32C1C065DAB241F3E9F133AFFFFFFFF"))

_fake_body = {"daybyday_times": [
    {"read_month": "2026-10", "read_time": "26", "detail": [
        {"read_day": "24", "read_minutes": 16},
        {"read_day": "1", "read_minutes": 11},
        {"read_day": "00", "read_minutes": 99},        # 脏数据 -> 丢
    ]},
    {"read_month": "bad", "detail": [{"read_day": "2", "read_minutes": 5}]},  # 月格式错 -> 丢
]}
_orig_book_report = zy_time.book_report
zy_time.book_report = lambda key: _fake_body
_days = zy_time.book_days("X_local")
zy_time.book_report = _orig_book_report
check("book_days 解析 + 补零", _days.get("2026-10-24") == 16 and _days.get("2026-10-01") == 11, _days)
check("book_days 丢脏数据", len(_days) == 2, _days)

# --- APK 式推送队列 (zy_time_queue) ---------------------------------------
import base64
import tempfile
from pathlib import Path

_union_b64, _union_n = zy_time._bitmap_union(None, [0, 5])
check("位图并集 计数", _union_n == 2, _union_n)
_again, _again_n = zy_time._bitmap_union(_union_b64, [5, 6])
check("位图并集 幂等/追加", _again_n == 3 and zy_time.minutes_from_bitmap(_again) == [0, 5, 6],
      zy_time.minutes_from_bitmap(_again))
check("位图并集 坏 base64 重置", zy_time._bitmap_union("!!!", [1])[1] == 1)

# 用临时 state 库, 不碰真库
_real_state = zy_notes.STATE_DB
tmpdir = tempfile.mkdtemp(prefix="zytest-")
zy_notes.STATE_DB = str(Path(tmpdir) / "state.db")

_today = date.today()
_yday = _today.replace(day=_today.day - 1) if _today.day > 1 else _today
_r = zy_time.enqueue(_today.replace(year=_today.year + 1), series_ids=[26])
check("入队 未来日期拒绝", bool(_r["errors"]) and _r["queued"] == 0, _r)

# 直接塞队列行 (绕开 Kavita 会话), 校验 flush 的分组/字段/删行
bm24 = zy_time.bitmap_from_minutes([600, 601, 602])
bm23 = zy_time.bitmap_from_minutes([300])
zy_time._queue_put("6818bfd9c92c8576a0e21ad13cd28cc2", _today, 26, bm24, 3, 0.49)
zy_time._queue_put("6818bfd9c92c8576a0e21ad13cd28cc2", _yday, 26, bm23, 1, 0.48)
check("队列 2 行", len(zy_time.queue_info()) == 2, zy_time.queue_info())

_dry = zy_time.flush(dry_run=True)
_p = _dry.get("payload") or {}
check("flush 按日分组", sorted(_p) == sorted([_today.strftime("%Y%m%d"), _yday.strftime("%Y%m%d")]),
      sorted(_p))
_one = (_p.get(_today.strftime("%Y%m%d")) or [{}])[0]
check("flush 字段形态", _one.get("bid") == 0 and _one.get("res") == "R"
      and _one.get("local_bid", "").endswith("_local")
      and _one.get("local_bid", "").split("_")[0].isupper(), _one)

_posted = {}
_orig_post = zy_time._post_report


def _fake_ok(data):
    _posted["data"] = data
    return {"code": 0, "body": {"ts": "1"}}


zy_time._post_report = _fake_ok
_ok = zy_time.flush()
check("flush 成功清空队列", _ok["cleared"] == 2 and not zy_time.queue_info(), _ok)
check("flush 一次请求带两天", sorted(_posted["data"]) == sorted(_p), sorted(_posted["data"]))


def _fake_bad(data):
    return {"code": 400001, "msg": "nope"}


zy_time._queue_put("6818bfd9c92c8576a0e21ad13cd28cc2", _today, 26, bm24, 3, 0.49)
zy_time._post_report = _fake_bad
_bad = zy_time.flush()
check("flush 失败留队 + tries", _bad["kept"] == 1 and len(zy_time.queue_info()) == 1
      and zy_time.queue_info()[0]["tries"] == 1, _bad)


def _fake_raise(data):
    raise OSError("network down")


zy_time._post_report = _fake_raise
_exc = zy_time.flush()
check("flush 异常留队", _exc["kept"] == 1 and len(zy_time.queue_info()) == 1, _exc)

# 30 天保留期
_old_day = _today.replace(year=_today.year) if False else date(2000, 1, 1)
zy_time._queue_put("deadbeef" * 4, _old_day, 26, bm23, 1, 0.1)
check("过期行被清", zy_time._queue_drop_old(_today) >= 1
      and all(r["day"] != _old_day.isoformat() for r in zy_time.queue_info()))

zy_time._post_report = _orig_post
zy_notes.STATE_DB = _real_state
print("清理临时库:", tmpdir)

print("\nFAIL =", FAIL)
sys.exit(1 if FAIL else 0)
