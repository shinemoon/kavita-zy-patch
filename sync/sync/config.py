"""路径 / 凭据常量"""
from __future__ import annotations
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
BRIDGE_ROOT = Path("/vol2/1001/zhangyue-bridge")
STATE_DIR = BRIDGE_ROOT / "state"
STATE_DB = STATE_DIR / "sync.sqlite"
LOG_DIR = STATE_DIR / "logs"

KAVITA_DB = "/vol1/1001/APPS/kavita/kavita.db"

# 掌阅下载的 epub 落盘目录 (book_link.epub_path 缺省从这里按书名找)
# 注意: 这个目录 = Kavita 的 /books/掌阅, 里面的书可能已被替换成"重排版"
# (见 kpages.py)。原书留在 ZY_ORIG_DIR, 算掌阅 readPercent 要用原书的字节几何。
ZY_EPUB_DIR = Path("/vol2/1001/dav/Books/掌阅")
ZY_ORIG_DIR = BRIDGE_ROOT / "books"
KAVITA_USER_ID = 1  # claud
KAVITA_BASE_URL = os.environ.get("KAVITA_BASE_URL", "http://127.0.0.1:5000")

# ---------------------------------------------------------------------------
# pull 的页号口径 (只对**没有**重排版 sidecar 的书生效)

# 2026-10-23 起, 库里的 epub 通常已被换成"等字节切片版" (见 kpages.py),
# 那种书的 page 号直接由掌阅 pct 反算 (state/kpages/<series>.json), 跟下面
# 这两个模式无关。只有当 sidecar 缺失/过期时才回落到这里的老口径:
#
#   "percent"   pages_read = round(pct * Pages)  → 书架百分比对, 位置只是近似
#   "position"  pages_read = 当前 spine 下标     → 位置对, 百分比长期不动
#                                                (狐 9 篇前 5 篇只占 2.7% 字节)
PULL_PAGE_MODE = (os.environ.get("ZY_PULL_PAGE_MODE") or "percent").strip().lower()
if PULL_PAGE_MODE not in ("percent", "position"):
    PULL_PAGE_MODE = "percent"

# 掌阅 API 端点
ZY_GET_PROCESS_URL = "https://icloud.zhangyue.com/cloud/storage2/safe/getProcess"
ZY_UPLOAD_PROCESS_URL = "https://icloud.zhangyue.com/cloud/storage2/safe/uploadProcess"


def load_zy_auth(path: Path = BRIDGE_ROOT / ".auth.json") -> dict:
    """读掌阅凭据。回退看 ZY_AUTH_JSON 环境变量。"""
    raw = os.environ.get("ZY_AUTH_JSON")
    if raw:
        return json.loads(raw)
    with open(path) as f:
        return json.load(f)


def zy_query_params(auth: dict, extra: dict | None = None) -> dict:
    """拼掌阅接口必带参数。"""
    p = {
        "usr": auth["usr"],
        "zysid": auth["zysid"],
        "p29": auth["p29"],
        "p2": auth["p2"],
        "p3": auth["p3"],
        "p4": auth["p4"],
        "p33": auth.get("p33", "com.chaozh.iReaderFree"),
    }
    if extra:
        p.update(extra)
    return p


def ensure_dirs():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def find_zy_epub(book_name: str, book_id: str = "") -> str:
    """按书名在 ZY_EPUB_DIR 里找 epub, 找不到返空串。

    掌阅那边书名原样带过来 (含全角括号), 文件名也原样, 所以先精确匹配。
    book_id 兜底: 文件名里不含 id 时靠前缀模糊。

    2026-10-24 补: 云书的多卷存成 "路邊的藤井 - 卷01" (书名) 但文件是
    "路邊的藤井/路邊的藤井 卷01.epub" —— 分隔符和目录层级都不同, 于是
    三卷全都拿不到几何 (readPercent/totalCount 只能走粗略回退)。
    这里加"去分隔符 + 递归"两跳。
    """
    for base in (ZY_ORIG_DIR, ZY_EPUB_DIR):
        p = base / f"{book_name}.epub"
        if p.exists():
            return str(p)
    # 退化: 去掉常见修饰 (完结/精校/全本) 再找
    stem = book_name
    for junk in ("（1-54册）完结精校版", "完结精校版", "完结版", "全本"):
        stem = stem.replace(junk, "")
    for base in (ZY_ORIG_DIR, ZY_EPUB_DIR):
        p2 = base / f"{stem}.epub"
        if p2.exists():
            return str(p2)
    # 退化: 分隔符归一 (" - " / "-" / "_" 全当空格), 且在子目录里递归找
    flat = re.sub(r"[\s　]*[-–—_]+[\s　]*", " ", book_name).strip()
    flat = re.sub(r"\s+", " ", flat)
    for base in (ZY_ORIG_DIR, ZY_EPUB_DIR):
        if not base.exists():
            continue
        for p3 in base.rglob("*.epub"):
            if not p3.is_file():
                continue
            n = re.sub(r"[\s　]*[-–—_]+[\s　]*", " ", p3.stem).strip()
            if re.sub(r"\s+", " ", n) == flat:
                return str(p3)
    return ""
