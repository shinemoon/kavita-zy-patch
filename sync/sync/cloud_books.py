"""掌阅「个人云书籍」管理 — 列表 / 下载到本地目录 / 搬移 / 删除。

2026-10-24 实测两条链路（都走 api-pc.zhangyue.com + zysid 凭据）:

A. 个人云书架（自己上传的书，**无 DRM**）—— 以前漏掉的就是这一条
    GET  /bookstore/open/cloud/bookshelf/list
         -> {body:{current_version, data:[{book_id,book_name,book_file_id,
              book_file_size,cover_file_id,is_local_book,book_type,dtype,...}]}}
         只取 is_local_book=true 的条目（狐的故事 / 剑来 / 路邊的藤井 …）
    GET  /bookstore/open/cloud/storage/file/info?file_ids=<book_file_id>
         -> {body:{<file_id>:{object_name:"book-prod/xxx.epub", sign_url:"<OSS 直链>"}}}
    POST /bookstore/open/cloud/bookshelf/upsert   (multipart)
         fields: usr, current_version, type, data=<json array>
         type=1 新增 / type=2 删除 / type=3 更新      <- 1/2 都已实测
         data 元素最小 {"book_id": <64 hex>, "dtype": 0}
    ⚠ 服务端按 current_version 做乐观锁：版本不一致返 100006，删前要重读版本。

B. 商店云书（正版，带 DRM）
    GET  /cloud/storage2/safe/getCloudBookListV2      -> 19 本
    GET  /cloud/storage2/safe/getCloudBookInfo?bookId -> downloadUrl（一层签名）
    GET  .../delBooks?bookIds=<id>,<id>               -> 删除

参数是 camelCase（bookId / bookIds / file_ids）；写 snake 会返 20099/602001。

上传（往云盘加书）没做：`/cloud/bookcase/safe/uploadData` body 是 Thrift 二进制；
`/bookstore/open/cloud/storage/upload/authorize` 参数名未知（一律 602001）；
App 老接口 `/cloud_book/storage/upload/authorize` 需要 JNI 的 sign（Security.hash），
本地算不出来。 等有别的路子再说。
"""
from __future__ import annotations

import gzip
import json
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

from .config import BRIDGE_ROOT
from . import zy_auth

PC_BASE = "https://api-pc.zhangyue.com"
CLOUD_BASE = "https://icloud.zhangyue.com"

SHELF_LIST_PATH = "/bookstore/open/cloud/bookshelf/list"
SHELF_UPSERT_PATH = "/bookstore/open/cloud/bookshelf/upsert"
FILE_INFO_PATH = "/bookstore/open/cloud/storage/file/info"

LIST_PATH = "/cloud/storage2/safe/getCloudBookListV2"
INFO_PATH = "/cloud/storage2/safe/getCloudBookInfo"
DEL_PATH = "/cloud/storage2/safe/delBooks"

STATE_DIR = BRIDGE_ROOT / "state"
INDEX_PATH = STATE_DIR / "cloud_books.json"
CACHE_PATH = STATE_DIR / "cloud_books_cache.json"
DL_DIR_PATH = STATE_DIR / "cloud_dl_dir.json"

# 只配置一个下载目录（用户可改）。 默认给 Kavita 的 Books/掌阅。
DEFAULT_DL_DIR = "/vol2/1001/dav/Books/掌阅"

UA = "iReader/8.0"


class CloudError(RuntimeError):
    pass


# ---- HTTP ---------------------------------------------------------------

def _auth() -> dict:
    a = zy_auth.load_auth()
    if not a:
        raise CloudError("掌阅凭据缺失, 先在控制台扫码登录")
    return a


def _qs(extra: dict | None = None) -> str:
    a = _auth()
    q = {
        "usr": a.get("usr", ""), "zysid": a.get("zysid", ""),
        "p29": a.get("p29") or zy_auth.FINGERPRINT["p29"],
        "p2": a.get("p2") or zy_auth.FINGERPRINT["p2"],
        "p3": a.get("p3") or zy_auth.FINGERPRINT["p3"],
        "p4": a.get("p4") or zy_auth.FINGERPRINT["p4"],
        "p33": a.get("p33") or zy_auth.FINGERPRINT["p33"],
    }
    if extra:
        q.update({k: v for k, v in extra.items() if v is not None})
    return urllib.parse.urlencode(q)


def _fetch(url: str, *, timeout: int = 40, data: bytes | None = None,
           headers: dict | None = None) -> bytes:
    hdr = {"User-Agent": UA, "Accept-Encoding": "gzip"}
    if headers:
        hdr.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdr,
                                method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            if (r.headers.get("Content-Encoding") or "").lower() == "gzip" or raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            return raw
    except urllib.error.HTTPError as e:
        raise CloudError(f"HTTP {e.code} {url[:120]}") from e
    except Exception as e:  # noqa: BLE001 — 网络错误统一成 CloudError
        raise CloudError(f"{type(e).__name__}: {e}") from e


def _api(path: str, extra: dict | None = None, *, timeout: int = 30,
         base: str = PC_BASE) -> dict:
    raw = _fetch(f"{base}{path}?{_qs(extra)}", timeout=timeout)
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except ValueError as e:
        raise CloudError(f"返回不是 JSON: {raw[:120]!r}") from e


def _multipart(path: str, fields: dict, *, timeout: int = 30, base: str = PC_BASE) -> dict:
    boundary = "----zy" + uuid.uuid4().hex
    body = b""
    for k, v in fields.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n"
                 f"{v}\r\n").encode("utf-8")
    body += f"--{boundary}--\r\n".encode("utf-8")
    raw = _fetch(f"{base}{path}?{_qs()}", data=body, timeout=timeout,
                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except ValueError as e:
        raise CloudError(f"返回不是 JSON: {raw[:120]!r}") from e


# ---- A. 个人云书架 -------------------------------------------------------

def shelf_raw() -> dict:
    """原始云书架响应；返回 {version, data:[...]}。"""
    j = _api(SHELF_LIST_PATH)
    body = j.get("body") or {}
    if str(j.get("code")) not in ("0", "None"):
        raise CloudError(f"云书架列表失败: {j.get('msg') or j}")
    return {"version": body.get("current_version"), "data": body.get("data") or []}


def shelf_books(*, personal_only: bool = True) -> list[dict]:
    raw = shelf_raw()
    out = []
    for it in raw["data"]:
        if personal_only and not it.get("is_local_book"):
            continue
        out.append(_norm_shelf(it))
    return out


def _norm_shelf(it: dict) -> dict:
    bid = str(it.get("book_id") or "")
    fid = str(it.get("book_file_id") or "")
    return {
        "key": f"shelf:{bid}",
        "source": "shelf",
        "id": bid,
        "file_id": fid,
        "cover_file_id": it.get("cover_file_id") or "",
        "name": it.get("book_name") or f"book-{bid[:8]}",
        "author": it.get("author") or "",
        "size": int(it.get("book_file_size") or 0),
        "ctime": it.get("ctime") or "",
        "book_type": it.get("book_type"),
        "personal": bool(it.get("is_local_book")),
        "raw": it,
    }


# ---- B. 商店云书 ---------------------------------------------------------

def store_books() -> list[dict]:
    j = _api(LIST_PATH, base=CLOUD_BASE)
    if str(j.get("status")) not in ("0", "None") and j.get("status") != 0:
        raise CloudError(f"商店云书列表失败: {j.get('msg') or j}")
    out = []
    for it in ((j.get("res") or {}).get("bookList") or []):
        bid = str(it.get("bookid") or "")
        out.append({
            "key": f"store:{bid}", "source": "store", "id": bid, "file_id": "",
            "name": it.get("name") or f"book-{bid}",
            "author": it.get("author") or "",
            "size": 0, "ctime": it.get("ctime") or "",
            "book_type": it.get("type"), "personal": False, "raw": it,
        })
    return out


# ---- 列表（只看个人云书架） ----------------------------------------------

_DL_SUFFIXES = (".epub", ".ebk3", ".pdf", ".mobi", ".azw3", ".txt")


def _dl_scan() -> list[Path]:
    """下载目录里已有的书（递归两层，跳过隐藏目录）。"""
    base = Path(dl_dir())
    out: list[Path] = []
    if not base.is_dir():
        return out
    for p in base.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in _DL_SUFFIXES:
            continue
        if any(part.startswith(".") for part in p.relative_to(base).parts):
            continue
        if len(p.relative_to(base).parts) > 3:
            continue
        out.append(p)
    return out


def _norm_key(s: str) -> str:
    """文件名/书名的匹配键：去空白、下划线、连字符、间隔号，转小写。"""
    return re.sub(r"[\s_\-–—·．.]+", "", str(s or "")).lower()


def _match_local(book: dict, files: list[Path]) -> dict | None:
    """索引里没有时，看下载目录里有没有同名文件（用户自己放进去的也算）。"""
    name = _norm_key(book.get("name"))
    if not name:
        return None
    for p in files:
        stem = _norm_key(p.stem)
        if stem == name or stem.startswith(name) or name in stem:
            try:
                size = p.stat().st_size
            except OSError:
                size = None
            return {"path": str(p), "name": p.name, "size": size,
                    "bookid": book.get("id"), "source": "shelf",
                    "unmanaged": True}
    return None


def list_books(*, refresh: bool = True) -> dict:
    """只列**用户自己上传**的云书（is_local_book=true，无 DRM）。

    商店云书（正版带 DRM）不在这个面板的范围里 —— 用户只需要管自己的书。
    """
    errors = []
    shelf: list[dict] = []
    version = None
    if refresh:
        try:
            raw = shelf_raw()
            version = raw["version"]
            shelf = [_norm_shelf(it) for it in raw["data"] if it.get("is_local_book")]
        except Exception as e:  # noqa: BLE001
            errors.append(f"个人云书架: {type(e).__name__}: {e}")
        doc = {"fetched_at": datetime.now().isoformat(timespec="seconds"),
               "version": version, "shelf": shelf}
        _atomic_json(CACHE_PATH, doc)
    else:
        try:
            doc = json.loads(CACHE_PATH.read_text("utf-8"))
        except (OSError, ValueError):
            doc = {"fetched_at": None, "shelf": [], "version": None}
        shelf, version = doc.get("shelf") or [], doc.get("version")

    idx = _read_index()
    files = _dl_scan()
    books = []
    for b in shelf:
        b = dict(b)
        b.pop("raw", None)
        b["local"] = idx.get(b["key"]) or _match_local(b, files)
        books.append(b)
    return {"fetched_at": doc.get("fetched_at"), "version": version,
            "count": len(books), "personal_count": len(books), "store_count": 0,
            "books": books, "dl_dir": dl_dir(), "errors": errors}


def _find_cached(key: str) -> dict | None:
    try:
        doc = json.loads(CACHE_PATH.read_text("utf-8"))
    except (OSError, ValueError):
        doc = {}
    for b in (doc.get("shelf") or []):
        if b.get("key") == key or b.get("id") == key:
            return b
    return None


# ---- 下载地址 -----------------------------------------------------------

def resolve_file_url(key: str) -> tuple[str, dict]:
    """返回 (可下载直链, book 记录)。key = "shelf:<id>" / "store:<id>" / 裸 id。"""
    book = _find_cached(key)
    if not book:
        list_books(refresh=True)
        book = _find_cached(key)
    if not book:
        raise CloudError(f"找不到这本书: {key}")

    if book["source"] == "shelf":
        fid = book.get("file_id") or ""
        if not fid:
            raise CloudError("这本书没有 book_file_id（可能只是书架条目，文件不在云上）")
        j = _api(FILE_INFO_PATH, {"file_ids": fid})
        ent = (j.get("body") or {}).get(fid) or {}
        url = ent.get("sign_url")
        if not url:
            raise CloudError(f"没拿到签名地址: {str(j)[:200]}")
        obj = ent.get("object_name") or ""
        suffix = obj.rsplit(".", 1)[-1] if "." in obj else ""
        return url, {**book, "suffix": suffix or "epub", "object_name": obj}

    # store：两层签名
    j = _api(INFO_PATH, {"bookId": book["id"]}, base=CLOUD_BASE)
    if str(j.get("status")) != "0":
        raise CloudError(f"详情失败: {j.get('msg') or j}")
    info = (j.get("res") or {}).get("bookInfo")
    if isinstance(info, str):
        info = json.loads(info)
    info = info or {}
    dl = info.get("downloadUrl")
    if not dl:
        raise CloudError("bookInfo 里没有 downloadUrl（可能是正版加密书，不在云盘链路）")
    layer = json.loads(_fetch(dl, timeout=40).decode("utf-8", "replace"))
    items = ((layer.get("body") or {}).get("downInfo") or [])
    if not items or not items[0].get("url"):
        raise CloudError(f"没拿到文件地址: {str(layer)[:200]}")
    suffix = (info.get("suffix") or "").lstrip(".") or "epub"
    return items[0]["url"], {**book, **info, "suffix": suffix}


# ---- 本地索引 -----------------------------------------------------------

def _read_index() -> dict:
    try:
        return json.loads(INDEX_PATH.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def _atomic_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), "utf-8")
    os.replace(tmp, path)


def _save_index(doc: dict) -> None:
    _atomic_json(INDEX_PATH, doc)


def _record(key: str, rec: dict | None) -> None:
    doc = _read_index()
    if rec is None:
        doc.pop(str(key), None)
    else:
        doc[str(key)] = rec
    _save_index(doc)


# ---- 下载目录（单一，可改） ---------------------------------------------

def dl_dir() -> str:
    """当前下载目录：env ZY_CLOUD_DL_DIR > state/cloud_dl_dir.json > 默认。"""
    env = (os.environ.get("ZY_CLOUD_DL_DIR") or "").strip()
    if env:
        return str(Path(os.path.expanduser(env)))
    try:
        doc = json.loads(DL_DIR_PATH.read_text("utf-8"))
        p = str(doc.get("path") or "").strip() if isinstance(doc, dict) else ""
    except (OSError, ValueError):
        p = ""
    return p or DEFAULT_DL_DIR


def set_dl_dir(path: str) -> str:
    """改下载目录（会建出来）。 只接受绝对路径，防手滑写成相对目录。"""
    raw = os.path.expanduser(str(path or "").strip())
    if not raw:
        raise CloudError("目录不能为空")
    p = Path(raw)
    if not p.is_absolute():
        raise CloudError(f"要绝对路径: {raw}")
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise CloudError(f"建不了目录 {p}: {e}") from e
    if not os.access(p, os.W_OK):
        raise CloudError(f"目录不可写: {p}")
    p = p.resolve()
    _atomic_json(DL_DIR_PATH, {"path": str(p),
                               "updated_at": datetime.now().isoformat(timespec="seconds")})
    return str(p)


def safe_dir(target: str | None = None) -> Path:
    """下载/搬移的落地目录：一律在 dl_dir() 之内（含子目录）。"""
    base = Path(dl_dir()).resolve()
    if not target:
        p = base
    else:
        p = Path(os.path.expanduser(str(target)))
        if not p.is_absolute():
            p = base / p
        p = p.resolve()
        if not (p == base or base in p.parents):
            raise CloudError(f"只能落在下载目录 {base} 里（含子目录）: {p}")
    p.mkdir(parents=True, exist_ok=True)
    return p


def _safe_name(name: str, ident: str, suffix: str) -> str:
    base = re.sub(r"[\\/:*?\"<>|\n\r\t]", "_", (name or f"book-{ident}")).strip()
    if not base:
        base = f"book-{ident}"
    if suffix and not base.lower().endswith("." + suffix.lower()):
        base = f"{base}.{suffix}"
    return base[:180]


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suf = path.stem, path.suffix
    for i in range(1, 1000):
        cand = path.with_name(f"{stem} ({i}){suf}")
        if not cand.exists():
            return cand
    return path.with_name(f"{stem}-{int(time.time())}{suf}")


# ---- 下载（后台任务） ----------------------------------------------------

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _job_new(key: str, dest: Path, name: str) -> str:
    jid = uuid.uuid4().hex[:12]
    with _jobs_lock:
        _jobs[jid] = {"id": jid, "key": str(key), "state": "queued",
                      "dest": str(dest), "name": name, "bytes": 0, "total": None,
                      "path": None, "error": None,
                      "started": datetime.now().isoformat(timespec="seconds"),
                      "finished": None}
    return jid


def _share_file(path: Path) -> None:
    """5012 以 root 跑, 下出来的文件要让 claud 侧 CLI 也能读 (属主随父目录)。"""
    try:
        st, pst = path.stat(), path.parent.stat()
        if os.geteuid() == 0 and (st.st_uid != pst.st_uid or st.st_gid != pst.st_gid):
            os.chown(path, pst.st_uid, pst.st_gid)
        os.chmod(path, 0o644)
    except OSError:
        pass


def _post_download_nav_fix(path: Path) -> str:
    """下完立刻补 EPUB3 nav（掌阅个人云里的书常声明 3.0 却没有 nav，
    Kavita/VersOne.Epub 会报 Epub3NavException 整本读不出来）。best-effort。"""
    try:
        import sys as _sys
        root = Path(__file__).resolve().parents[1]      # .../sync （仓库根）
        if str(root) not in _sys.path:
            _sys.path.insert(0, str(root))
        from tools import fix_epub_nav                                    # noqa: PLC0415
        r = fix_epub_nav.fix(str(path))
        return str(r.get("action") or "")
    except Exception as e:  # noqa: BLE001 — 补 nav 失败不能影响下载结果
        return f"skip({type(e).__name__}: {e})"


def check_path_in_dl(target: str) -> Path:
    """把一个绝对路径校验在下载目录之内（覆盖写回时用）。"""
    base = Path(dl_dir()).resolve()
    p = Path(os.path.expanduser(str(target or ""))).resolve()
    if not (p == base or base in p.parents):
        raise CloudError(f"只能落在下载目录 {base} 里: {p}")
    return p


def start_download(key: str, dest_dir: str | None = None, *, name: str | None = None,
                   overwrite: bool = False, target: str | None = None,
                   prep: bool = True) -> str:
    """起一个下载任务。

    overwrite=True + target=<下载目录内已有文件的绝对路径> → **原地覆盖**该文件
    （列表里「覆盖下载」用，避免再多出一份 `xxx (1).epub`）。

    prep=True（默认）→ 落盘落在 Kavita 库目录里时，**下完立刻重排**：原书归档到
    bridge/books，库里的那份换成等字节切片版（见 epubprep.py）。这样 Kavita 从
    第一次扫到就是细分页，进度一开始就跟掌阅对齐，不留"原书进库、事后补"的窗口。
    """
    dest = safe_dir(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    fixed: Path | None = None
    if overwrite and target:
        fixed = check_path_in_dl(target)
        fixed.parent.mkdir(parents=True, exist_ok=True)
        if fixed.is_dir():
            raise CloudError(f"目标是目录不是文件: {fixed}")
    jid = _job_new(key, fixed.parent if fixed else dest,
                   name or (fixed.name if fixed else ""))
    threading.Thread(target=_download_worker,
                     args=(jid, str(key), dest, name, fixed, bool(prep)),
                     daemon=True).start()
    return jid


def _download_worker(jid: str, key: str, dest: Path, name: str | None,
                     fixed: Path | None = None, do_prep: bool = True) -> None:
    job = _jobs[jid]

    def upd(**kw):
        with _jobs_lock:
            job.update(kw)

    try:
        upd(state="resolving")
        url, info = resolve_file_url(key)
        suffix = (info.get("suffix") or "epub").lstrip(".")
        fname = _safe_name(name or info.get("name") or "", info.get("id") or key, suffix)
        target = fixed if fixed else _unique(dest / fname)
        if fixed:
            upd(overwrite=True)
        upd(state="downloading", name=target.name)

        req = urllib.request.Request(url, headers={"User-Agent": UA})
        got = 0
        tmp = target.with_name(target.name + ".part")
        with urllib.request.urlopen(req, timeout=600) as r, open(tmp, "wb") as f:
            total = r.headers.get("Content-Length")
            upd(total=int(total) if total and total.isdigit() else None)
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                upd(bytes=got)
        if got == 0:
            tmp.unlink(missing_ok=True)
            raise CloudError("下载 0 字节")
        os.replace(tmp, target)
        _share_file(target)

        # 落盘即入库: 库目录里的书直接做成 Kavita 切片版 (原书另存)
        prep: dict = {"state": "off"}
        if do_prep:
            upd(state="preparing")
            try:
                from . import epubprep                                  # noqa: PLC0415
                prep = epubprep.prep_download(
                    target, book_name=info.get("name") or "",
                    zy_book_id=str(info.get("id") or key).split(":")[-1],
                    log=lambda m: upd(note=str(m)[:300]))
            except Exception as e:                                       # noqa: BLE001
                prep = {"state": "error", "error": f"{type(e).__name__}: {e}"}

        # 没做成切片版 (不在库目录 / 多卷 / 出错) 才需要补 EPUB3 nav
        #
        # 判据要看**磁盘上的文件**, 不看 prep 的返回值: 2026-10-31 万古江河
        # 那次 prep 已经在库里换成切片版, 只是后面扫库那步抛异常 (API key 路径
        # 被脱敏成 /home/youruser) → state=error → 走了 nav 修补, 把刚生成的
        # 切片版整本 lxml 重新序列化一遍 (页内容全变), 白折腾还掩盖了真错误。
        nav = ""
        try:
            from . import kpages as _kp                            # noqa: PLC0415
            is_edition = _kp.is_edition(target)
        except Exception:                                          # noqa: BLE001
            is_edition = prep.get("state") in ("ok", "edition")
        if is_edition:
            nav = "edition"
        else:
            nav = _post_download_nav_fix(target)

        rec = {"path": str(target), "size": got, "key": key,
               "source": info.get("source"), "bookid": info.get("id"),
               "name": target.name, "author": info.get("author"),
               "nav_fix": nav, "edition": prep.get("state"),
               "pages": prep.get("pages"), "series_id": prep.get("series_id"),
               "downloaded_at": datetime.now().isoformat(timespec="seconds")}
        _record(key, rec)
        upd(state="done", path=str(target), bytes=got, nav=nav, prep=prep,
            finished=datetime.now().isoformat(timespec="seconds"))
    except Exception as e:  # noqa: BLE001 — 失败要把原因带给前端
        upd(state="error", error=f"{type(e).__name__}: {e}",
            finished=datetime.now().isoformat(timespec="seconds"))


def job_status(jid: str) -> dict | None:
    with _jobs_lock:
        j = _jobs.get(jid)
        return dict(j) if j else None


def jobs_list(limit: int = 20) -> list[dict]:
    with _jobs_lock:
        items = sorted(_jobs.values(), key=lambda x: x["started"], reverse=True)
        return [dict(x) for x in items[:limit]]


# ---- 搬移 / 删除 ---------------------------------------------------------

def move_local(key: str, dest_dir: str, *, new_name: str | None = None) -> dict:
    idx = _read_index()
    rec = idx.get(str(key))
    if not rec:
        raise CloudError("这本还没下载到本地")
    src = Path(rec["path"])
    if not src.exists():
        _record(key, None)
        raise CloudError(f"索引里的文件已不存在: {src}")
    dest = safe_dir(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    target = _unique(dest / (_safe_name(new_name, rec.get("bookid") or key, src.suffix.lstrip("."))
                             if new_name else src.name))
    shutil.move(str(src), str(target))
    rec.update({"path": str(target), "name": target.name,
                "moved_at": datetime.now().isoformat(timespec="seconds")})
    _record(key, rec)
    return rec


def delete_local(key: str, *, delete_file: bool = False) -> dict:
    """从本地索引里移除；delete_file=True 时连文件一起删。

    也接受 `file:<绝对路径>` 形式（下载目录里没登记进索引的文件）。
    """
    if str(key).startswith("file:"):
        p = Path(str(key)[len("file:"):])
        base = Path(dl_dir()).resolve()
        try:
            rp = p.resolve()
        except OSError:
            rp = p
        if not (rp == base or base in rp.parents):
            raise CloudError(f"只能删下载目录里的文件: {p}")
        if not p.exists():
            return {"ok": True, "file_deleted": False, "path": str(p)}
        if not delete_file:
            raise CloudError("未登记进索引的文件只能整体删掉（勾选「删文件」）")
        p.unlink()
        return {"ok": True, "file_deleted": True, "path": str(p)}

    idx = _read_index()
    rec = idx.get(str(key))
    if not rec:
        raise CloudError("这本不在本地索引里")
    removed = False
    if delete_file:
        p = Path(rec.get("path") or "")
        if p.exists():
            p.unlink()
            removed = True
    _record(key, None)
    return {"ok": True, "file_deleted": removed, "path": rec.get("path")}


def delete_cloud(keys: list[str], *, source: str | None = None) -> dict:
    """删云上的书。

    source="shelf"（默认，个人云书架）→ bookshelf/upsert type=2
    source="store"（商店云书）        → storage2/safe/delBooks
    """
    keys = [str(k) for k in keys if str(k).strip()]
    if not keys:
        raise CloudError("keys 为空")
    out = {"ok": True, "deleted": [], "failed": []}

    shelf_ids, store_ids = [], []
    for k in keys:
        if k.startswith("shelf:"):
            shelf_ids.append(k.split(":", 1)[1])
        elif k.startswith("store:"):
            store_ids.append(k.split(":", 1)[1])
        elif source == "store":
            store_ids.append(k)
        else:
            shelf_ids.append(k)

    if shelf_ids:
        ver = shelf_raw().get("version")
        if ver is None:
            raise CloudError("拿不到 current_version，删不了")
        data = [{"book_id": i, "dtype": 0} for i in shelf_ids]
        j = _multipart(SHELF_UPSERT_PATH,
                       {"usr": _auth().get("usr", ""), "current_version": ver,
                        "type": 2, "data": json.dumps(data, ensure_ascii=False)})
        if str(j.get("code")) != "0":
            raise CloudError(f"删除云书架条目失败: {j.get('code')} {j.get('msg')}")
        for i in shelf_ids:
            _record(f"shelf:{i}", None)
            out["deleted"].append(f"shelf:{i}")

    if store_ids:
        j = _api(DEL_PATH, {"bookIds": ",".join(store_ids)}, base=CLOUD_BASE)
        ok = bool((j.get("res") or {}).get("success")) and str(j.get("status")) == "0"
        if ok:
            for i in store_ids:
                _record(f"store:{i}", None)
                out["deleted"].append(f"store:{i}")
        else:
            out["failed"].extend(store_ids)
            out["error"] = j.get("msg")
    out["ok"] = not out["failed"]
    return out


def local_files() -> list[dict]:
    """本地索引 + 下载目录里没登记的文件 + 文件是否存在 + 实际大小。"""
    out = []
    seen: set[str] = set()
    for key, rec in _read_index().items():
        p = Path(rec.get("path", ""))
        rec = dict(rec)
        rec["key"] = key
        rec["exists"] = p.exists()
        rec["actual_size"] = p.stat().st_size if p.exists() else None
        out.append(rec)
        try:
            seen.add(str(p.resolve()))
        except OSError:
            seen.add(str(p))
    for p in _dl_scan():
        try:
            rp = str(p.resolve())
        except OSError:
            rp = str(p)
        if rp in seen:
            continue
        out.append({"key": f"file:{rp}", "path": rp, "name": p.name,
                    "source": "本地目录", "unmanaged": True, "exists": True,
                    "actual_size": p.stat().st_size})
    return sorted(out, key=lambda r: r.get("downloaded_at") or "", reverse=True)
