"""掌阅 cloud API: getProgress / uploadProgress

URL 实测 (2026-10-21):
    GET  https://icloud.zhangyue.com/cloud/storage2/safe/getProcess?...
    POST https://icloud.zhangyue.com/cloud/storage2/safe/uploadProcess?...

注意:
    - uploadProcess body 字段含 updateTime (ms), readProcess.readPosition 用
      "1_0_0$##0-0-1-100-0##$" 这种 'chapterId_index_part_total_lastRead'
      格式 (没拿到完整 spec, 这个模板是 App 实际跑通的 payload)
    - readPercent "0"-"100" 字符串
    - chapterId 是掌阅自家章节 ID, 不是页面位置
"""
from __future__ import annotations
import json
import time
import urllib.request
import urllib.parse
import urllib.error
from typing import Any

from .config import (
    ZY_GET_PROCESS_URL, ZY_UPLOAD_PROCESS_URL,
    load_zy_auth, zy_query_params,
)


class ZYError(Exception):
    """掌阅接口非 OK。"""
    def __init__(self, code: int, msg: str, raw: dict | None = None):
        self.code = code
        self.msg = msg
        self.raw = raw or {}
        super().__init__(f"ZY code={code} msg={msg}")


def _do(url: str, params: dict, body: dict | None = None, timeout: int = 25) -> dict:
    qs = urllib.parse.urlencode(params)
    full = f"{url}?{qs}"
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(full, data=data, method="POST" if data else "GET")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise ZYError(-1, "non-json", {"raw": raw[:200]})
    if parsed.get("code") not in (0, None):
        raise ZYError(parsed["code"], parsed.get("msg", "?"), parsed)
    return parsed


def get_progress(auth: dict, book_ids: list[str]) -> dict[str, dict]:
    """批量拉取进度。返回 {bookId: record}。"""
    if not book_ids:
        return {}
    params = zy_query_params(auth, {"bookIds": ",".join(map(str, book_ids))})
    out = _do(ZY_GET_PROCESS_URL, params)
    result = {}
    for item in out.get("body") or []:
        bid = str(item.get("bookId"))
        result[bid] = item
    return result


def upload_progress(auth: dict, *, book_id: str, book_name: str,
                    chapter_id: str, percent: str | int,
                    read_position: str = "0_0_0$##0-0-1-100-0##$",
                    total_count: str = "8",
                    read_complete: str = "0", book_over_status: int = 0,
                    is_fine_book: int = 1, source: str = "kavita-bridge",
                    device_name: str = "ClaudBridge") -> dict[str, Any]:
    """写入单本单章进度。readPosition 模板来自 App 实际 payload。

    percent: 0-100 (int 或 str)
    chapter_id: 掌阅章节 ID 字符串 (None/空串 = 当前在卷首, App 会接受)
    """
    now = str(int(time.time() * 1000))
    body = {
        "usr": auth["usr"],
        "bookId": book_id,
        "bookName": book_name,
        "deviceName": device_name,
        "updateTime": now,
        "readProcess": {
            "readPosition": read_position,
            "readPercent": str(percent),
            "lastReadDate": now,
            "totalCount": str(total_count),
            "chapterId": str(chapter_id or "0"),
            "readComplete": str(read_complete),
            "bookOverStatus": book_over_status,
            "isCartoon": 0,
            "isFineBook": is_fine_book,
            "readingSource": source,
        },
    }
    params = zy_query_params(auth)
    return _do(ZY_UPLOAD_PROCESS_URL, params, body)


# 调试用快捷
if __name__ == "__main__":
    import sys
    auth = load_zy_auth()
    bids = sys.argv[1:] if len(sys.argv) > 1 else ["1379"]
    print(json.dumps(get_progress(auth, bids), ensure_ascii=False, indent=1))
