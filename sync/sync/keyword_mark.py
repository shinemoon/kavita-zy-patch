"""Kavita 阅读器「关键词高亮」滤镜层 (2026-10-30)

epub-refiner (5013) 会给每本书生成一份 ``keywords.json``::

    {"meta": {"title": "...", "author": "...", ...},
     "categories": {"person": {"name": "人名",
                               "items": [{"term": "Pandora",
                                          "aliases": ["潘多拉", "P."], ...}]},
                    ...},
     "index": {"person": ["Pandora", ...]}}

这一层把它变成**阅读时的关键词高亮**, 整条链路纯前端滤镜:

    5012 (root) 写  <PATCH_ROOT>/state/keyword_mark.json
      → 补丁后端  GET /zy/api/keyword-mark?series=N  (词表 + 每类样式, 按书匹配)
      → 前端      zy-kavita-patch.js 在 .book-content 上铺一层高亮

两种渲染模式:

``highlight`` (默认)
    用 CSS Custom Highlight API (``CSS.highlights`` + ``Range``) 上色。
    **一个字符都不改 DOM** —— Kavita 存的 XPath 进度 / 注解 / 书签完全不受影响,
    这是"中间滤镜层"最贴近原意的做法。局限: ``::highlight()`` 只认
    color / background-color / text-decoration / text-shadow,
    粗体、圆角、内边距这些不支持。

``span``
    把命中文字包进 ``<span class="zy-kw zy-kw-<cat>">``, 任意 CSS 都能用,
    但会改 DOM 结构。Kavita 的进度 XPath 一般只到元素级 (``//body/div[1]/p[23]``),
    包 span 不影响同级索引, 不过仍属于"改 DOM", 有疑虑就用默认模式。

不写 Kavita 文件、不走 Kavita 后端、不重启容器; 配置改完前端 60s 内自己拉新版。
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

CONF_NAME = "keyword_mark.json"

MAX_DIRS = 8
MAX_CORPORA = 400
MAX_TERMS = 6000
MAX_WORD = 80
MIN_LEN = 2
MODES = ("highlight", "span")

# 语料里会混进 "it / him / the dog" 这类泛词 (LLM 抽词时的噪声)。
# 默认挡掉最显然的一批; 用户想全高亮就把 stop 清空。
DEFAULT_STOP = [
    "it", "its", "him", "his", "her", "hers", "he", "she", "they", "them",
    "the", "a", "an", "of", "to", "in", "on", "at", "and", "or", "but",
    "is", "was", "are", "were", "be", "been", "do", "did", "does",
    "my", "me", "mine", "you", "your", "i", "we", "us", "our", "this", "that",
]

# 分类兜底样式 (json 里带 name 就用 json 的, 颜色只在这里定)
_CAT_DEFAULTS = {
    "person": ("人名", "#1f2329", "#ffe58f"),
    "place": ("地点", "#102a43", "#cfe8ff"),
    "org": ("组织", "#10351f", "#d8f5d0"),
    "object": ("物件", "#3d1f05", "#ffe0c2"),
    "event": ("事件", "#2f1050", "#f0d9fb"),
    "time": ("时间", "#06373a", "#d4f2f2"),
    "term": ("术语", "#22262b", "#e9ecef"),
    "work": ("作品", "#4a0d20", "#ffd9e0"),
}
_FALLBACK_STYLE = ("#22262b", "#fff3a0")

_COLOR_RE = re.compile(
    r"^(#[0-9a-fA-F]{3,8}"
    r"|(?:rgb|rgba|hsl|hsla)\([0-9.,%\s/]*\)"
    r"|[a-zA-Z]{3,24})$")

# 书名比较时剥掉的噪声 (与 api_server 里那套同源, 这里独立一份避免循环 import)
_NAME_NOISE = re.compile(
    r"[─-▿！-～（）()《》\[\]【】_\-\s·・:：,，.。!?'\"“”‘’]|"
    r"简体|繁體|繁体|中文|英文|完整版|精校版|校对版|无删减|完结|"
    r" epub|epub|pdf|mobi|azw3|kindle|txt",
    re.I,
)


# ---- 路径 / 读写 -----------------------------------------------------------

def patch_root() -> Path:
    return Path(os.environ.get("ZY_PATCH_ROOT", "/vol2/1001/kavita-zy-patch"))


def path() -> Path:
    return patch_root() / "state" / CONF_NAME


def _default_style(cid: str) -> dict:
    name, color, bg = _CAT_DEFAULTS.get(cid, ("", _FALLBACK_STYLE[0], _FALLBACK_STYLE[1]))
    return {"enabled": True, "color": color, "bg": bg,
            "underline": False, "bold": False, "italic": False,
            "radius": "", "pad": "", "shadow": ""}


def default_conf() -> dict:
    return {
        "version": 1,
        "updated": "",
        "enabled": True,
        "mode": "highlight",
        "dirs": ["/vol2/1001/epub-refiner/exports"],
        "bind": {},            # {"27": "Adrian_Mole_....json"}
        "only": [],            # 只高亮这些分类 id; 空 = 全部
        "stop": list(DEFAULT_STOP),   # 忽略词 (大小写不敏感)
        "min_len": MIN_LEN,
        "skip_digits": True,   # 纯数字词 (1981 / 13) 不高亮
        "max_terms": MAX_TERMS,
        "styles": {cid: _default_style(cid) for cid in _CAT_DEFAULTS},
    }


def _clean_color(s) -> str:
    s = str(s or "").strip()[:32]
    return s if _COLOR_RE.match(s) else ""


def _clean_short(s, n: int = 24) -> str:
    s = str(s or "")
    for bad in (";", "{", "}", "<", ">", "@", "\\", '"', "'"):
        s = s.replace(bad, "")
    return s.strip()[:n]


def _clean_style(d) -> dict:
    d = d if isinstance(d, dict) else {}
    out = {"enabled": bool(d.get("enabled", True)),
           "color": _clean_color(d.get("color")),
           "bg": _clean_color(d.get("bg")),
           "underline": bool(d.get("underline")),
           "bold": bool(d.get("bold")),
           "italic": bool(d.get("italic")),
           "radius": _clean_short(d.get("radius")),
           "pad": _clean_short(d.get("pad")),
           "shadow": _clean_short(d.get("shadow"), 60)}
    return out


def normalize(conf) -> dict:
    """任意输入 → 合法配置。坏字段丢掉, 不抛错 (配置脏了不该让阅读器白屏)。"""
    conf = conf if isinstance(conf, dict) else {}
    base = default_conf()

    dirs = []
    for d in (conf.get("dirs") or []):
        d = str(d or "").strip()
        if d.startswith("/") and d not in dirs:
            dirs.append(d)
    if not dirs:
        dirs = list(base["dirs"])

    styles_in = conf.get("styles") if isinstance(conf.get("styles"), dict) else {}
    styles = {}
    for cid, st in styles_in.items():
        cid = _clean_short(cid, 32)
        if not cid:
            continue
        merged = _default_style(cid)
        merged.update(_clean_style(st))
        styles[cid] = merged
    for cid in _CAT_DEFAULTS:
        styles.setdefault(cid, _default_style(cid))

    bind_in = conf.get("bind") if isinstance(conf.get("bind"), dict) else {}
    bind = {}
    for k, v in bind_in.items():
        try:
            sid = int(k)
        except Exception:
            continue
        v = str(v or "").strip()
        if sid > 0 and v:
            bind[str(sid)] = v

    def _int(v, lo, hi, dflt):
        try:
            n = int(v)
        except Exception:
            return dflt
        return max(lo, min(hi, n))

    stop_in = conf.get("stop")
    stop = ([str(x) for x in stop_in if str(x)][:200]
            if isinstance(stop_in, list) else list(DEFAULT_STOP))

    mode = str(conf.get("mode") or base["mode"]).lower()
    if mode not in MODES:
        mode = "highlight"
    try:
        ver = int(conf.get("version") or 1)
    except Exception:
        ver = 1
    return {
        "version": max(1, ver),
        "updated": str(conf.get("updated") or ""),
        "enabled": bool(conf.get("enabled", True)),
        "mode": mode,
        "dirs": dirs[:MAX_DIRS],
        "bind": bind,
        "only": [str(x) for x in (conf.get("only") or []) if str(x)][:32],
        "stop": stop,
        "min_len": _int(conf.get("min_len"), 1, 12, MIN_LEN),
        "max_terms": _int(conf.get("max_terms"), 50, MAX_TERMS, MAX_TERMS),
        "skip_digits": bool(conf.get("skip_digits", True)),
        "styles": styles,
    }


def load() -> dict:
    p = path()
    if not p.exists():
        return normalize(default_conf())
    try:
        return normalize(json.loads(p.read_text(encoding="utf-8")))
    except Exception:
        # 文件坏了不要静默用默认样式乱高亮 —— 关掉, 但保留原文件供人看
        return normalize({"enabled": False})


def save(conf) -> dict:
    cur = load()
    new = normalize(conf)
    new["version"] = int(cur.get("version") or 1) + 1
    new["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)
    try:  # 5012 以 root 跑, 补丁侧工具是普通用户
        st = p.parent.stat()
        os.chmod(p, 0o644)
        os.chown(p, st.st_uid, st.st_gid)
    except Exception:
        pass
    return new


# ---- 语料 (keywords.json) --------------------------------------------------

def _words_of_item(it: dict) -> list[str]:
    out = []
    term = it.get("term")
    if term:
        out.append(str(term))
    for a in (it.get("aliases") or []):
        if a:
            out.append(str(a))
    return out


def load_corpus(p: Path) -> dict | None:
    """读一份 keywords.json。支持 epub-refiner 格式与 translator-skill 的 mappings 格式。"""
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    cats: dict[str, dict] = {}
    if isinstance(d.get("categories"), dict):
        for cid, c in d["categories"].items():
            if not isinstance(c, dict):
                continue
            cid = str(cid)
            words: list[str] = []
            # 按 count 降序排 (refiner 给的条目带出现次数), 让 max_terms 截断
            # 先砍掉冷门词, 而不是按文件里的书写顺序砍 (2026-10-31 修)
            items = list(c.get("items") or [])
            if any(isinstance(it, dict) for it in items):
                def _cnt(pair):
                    idx, it = pair
                    if not isinstance(it, dict):
                        return (1, idx)
                    try:
                        n = int(it.get("count") or 0)
                    except Exception:
                        n = 0
                    return (-n, idx)
                items = [it for _k, it in sorted(enumerate(items), key=_cnt)]
            for it in items:
                if isinstance(it, dict):
                    words.extend(_words_of_item(it))
                elif isinstance(it, str):
                    words.append(it)
            cats[cid] = {"id": cid, "name": str(c.get("name") or cid),
                         "words": words, "terms": len(c.get("items") or [])}
    elif isinstance(d.get("mappings"), list):
        # epub-translator-skill 的术语表: {mappings:[{source,target,tags:[]}]}
        for m in d["mappings"]:
            if not isinstance(m, dict):
                continue
            cid = str((m.get("tags") or ["term"])[0])
            slot = cats.setdefault(cid, {"id": cid, "name": cid, "words": [], "terms": 0})
            for k in ("source", "target"):
                if m.get(k):
                    slot["words"].append(str(m[k]))
                    slot["terms"] += 1
    if not cats:
        return None
    meta = d.get("meta") if isinstance(d.get("meta"), dict) else {}
    return {
        "path": str(p),
        "file": p.name,
        "stem": p.stem,
        "title": str(meta.get("title") or p.stem),
        "author": str(meta.get("author") or ""),
        "lang": str(meta.get("target_language") or ""),
        "source_epub": str(meta.get("source_epub") or ""),
        "generated_at": str(meta.get("generated_at") or ""),
        "mtime": int(p.stat().st_mtime) if p.exists() else 0,
        "size": p.stat().st_size if p.exists() else 0,
        "categories": cats,
        "words_total": sum(len(c["words"]) for c in cats.values()),
    }


_CORPUS_CACHE: dict[str, tuple[float, dict | None]] = {}


def _corpus_cached(p: Path) -> dict | None:
    key = str(p)
    try:
        mtime = p.stat().st_mtime
    except Exception:
        return None
    hit = _CORPUS_CACHE.get(key)
    if hit and hit[0] == mtime:
        return hit[1]
    c = load_corpus(p)
    _CORPUS_CACHE[key] = (mtime, c)
    return c


SKIP_FILES = {"index.json", "latest.json", CONF_NAME}


def scan_corpora(dirs=None) -> list[dict]:
    conf = None
    if dirs is None:
        conf = load()
        dirs = conf["dirs"]
    out: list[dict] = []
    seen: set[str] = set()
    for d in dirs:
        root = Path(str(d)).expanduser()
        if not root.is_absolute() or not root.exists():
            continue
        try:
            files = sorted(root.glob("*.json"))
        except Exception:
            continue
        for f in files[:MAX_CORPORA]:
            if f.name in SKIP_FILES or f.name.endswith(".tmp"):
                continue
            real = str(f)
            if real in seen:
                continue
            c = _corpus_cached(f)
            if not c:
                continue
            seen.add(real)
            out.append({
                "file": c["file"], "path": c["path"], "title": c["title"],
                "author": c["author"], "lang": c["lang"],
                "terms": sum(x["terms"] for x in c["categories"].values()),
                "words": c["words_total"],
                "categories": [{"id": x["id"], "name": x["name"], "words": len(x["words"])}
                               for x in c["categories"].values()],
                "mtime": c["mtime"], "size": c["size"],
            })
    out.sort(key=lambda x: -x["mtime"])
    return out


# ---- 选中哪一份语料 --------------------------------------------------------

def _norm_name(s: str) -> str:
    return _NAME_NOISE.sub("", str(s or "")).lower()


def _tokens(s: str) -> set[str]:
    parts = re.split(r"[^0-9A-Za-z\u4e00-\u9fff]+", str(s or "").lower())
    return {p for p in parts if len(p) >= 2}


def _name_score(series: str, corpus: dict) -> int:
    """系列名 ↔ 语料标题/文件名的相似度。0 = 不像; >=50 才算命中。

    宁可漏 (返回 0, 让人手动 bind), 也别把"同一个作者的另一本"错配上去:
    只认 完全相等 / 子串 (且较短串 >= 一半长) / 词集合 Jaccard >= 0.55。
    """
    a = _norm_name(series)
    if not a:
        return 0
    best = 0
    for cand_raw, cand in ((corpus.get("title"), _norm_name(corpus.get("title"))),
                           (corpus.get("stem"), _norm_name(corpus.get("stem")))):
        if not cand:
            continue
        if a == cand:
            return 100
        short, long = (a, cand) if len(a) <= len(cand) else (cand, a)
        if len(short) >= 4 and short in long:
            best = max(best, 50 + int(40 * len(short) / max(1, len(long))))
            continue
        ta, tb = _tokens(series), _tokens(cand_raw or "")
        if ta and tb:
            inter = len(ta & tb)
            union = len(ta | tb)
            if union and inter / union >= 0.55:
                best = max(best, 40 + int(40 * inter / union))
    return best


def resolve(series_id: int, series_name: str = "", conf: dict | None = None,
            corpora: list[dict] | None = None) -> tuple[dict | None, dict]:
    """挑这一本 Kavita 系列该用哪份 keywords.json。

    优先级: 显式 bind > 书名/文件名模糊匹配 (>40 分才认)。
    返回 (语料, 说明)。
    """
    conf = conf or load()
    corpora = corpora if corpora is not None else scan_corpora(conf["dirs"])
    want = (conf.get("bind") or {}).get(str(series_id))
    if want:
        w = str(want)
        for c in corpora:
            if w in (c["file"], c["path"], c["title"]) or Path(c["path"]).stem == w:
                full = _corpus_cached(Path(c["path"]))
                if full:
                    return full, {"how": "bind", "want": w}
        # bind 指到一个不在扫描目录里的绝对路径 → 直接读
        if w.startswith("/") and Path(w).exists():
            c = _corpus_cached(Path(w))
            if c:
                return c, {"how": "bind_path", "want": w}
        return None, {"how": "bind_missing", "want": w}

    best, best_score = None, 0
    for c in corpora:
        s = _name_score(series_name, c)
        if s > best_score:
            best, best_score = c, s
    if best and best_score >= 50:
        full = _corpus_cached(Path(best["path"]))
        if full:
            return full, {"how": "name", "score": best_score}
    return None, {"how": "none", "score": best_score}


# ---- 生成给前端的 payload --------------------------------------------------

def _clean_word(w: str, conf: dict) -> str:
    w = str(w or "").strip()
    if not w or len(w) > MAX_WORD:
        return ""
    alnum = sum(1 for ch in w if ch.isalnum())
    if alnum < conf["min_len"]:
        return ""
    if conf.get("skip_digits", True) and alnum and all(
            (not ch.isalnum()) or ch.isdigit() for ch in w):
        return ""
    return w


def _allocate(counts: dict[str, int], budget: int) -> dict[str, int]:
    """按分类公平分预算 (注水法)。

    旧实现按分类顺序一路装到 max_terms 就 break —— 大书里第一个分类 (人名)
    动辄几千词, 后面 7 类一个词都分不到, 表现为"只有人名高亮, 道具/地点全无"。
    这里每轮给还缺词的分类平均分, 分不完的额度留给别人, 直到预算用尽或词用完。
    """
    alloc = {k: 0 for k in counts}
    left = max(0, int(budget))
    pending = [k for k, v in counts.items() if v > 0]
    while left > 0 and pending:
        share = max(1, left // len(pending))
        moved = False
        for k in list(pending):
            take = min(counts[k] - alloc[k], share, left)
            if take > 0:
                alloc[k] += take
                left -= take
                moved = True
            if alloc[k] >= counts[k]:
                pending.remove(k)
            if left <= 0:
                break
        if not moved:          # 理论上到不了; 防死循环
            break
    return alloc


def build_payload(series_id: int, series_name: str = "", conf: dict | None = None) -> dict:
    conf = conf or load()
    out = {"ok": True, "enabled": bool(conf["enabled"]), "version": conf["version"],
           "mode": conf["mode"], "series": series_id,
           "minLen": conf["min_len"], "gen": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if not conf["enabled"]:
        return out

    corpora = scan_corpora(conf["dirs"])
    corpus, why = resolve(series_id, series_name, conf, corpora)
    out["pick"] = why
    out["corpora"] = len(corpora)
    if not corpus:
        out["cats"] = []
        out["total"] = 0
        return out

    only = set(conf["only"] or [])
    stop = {str(s).lower() for s in (conf["stop"] or [])}

    # 第一步: 每类清洗 + 去重, 拿到完整候选词表
    groups: list[dict] = []
    for cid, c in corpus["categories"].items():
        st = conf["styles"].get(cid) or _default_style(cid)
        if only and cid not in only:
            continue
        if not st.get("enabled", True):
            continue
        seen: set[str] = set()
        words = []
        for w in c["words"]:
            w2 = _clean_word(w, conf)
            if not w2:
                continue
            low = w2.lower()
            if low in stop or low in seen:
                continue
            seen.add(low)
            words.append(w2)
        if not words:
            continue
        groups.append({"id": cid, "name": c["name"], "style": st, "words": words})

    # 第二步: 公平分预算 (不再让第一个分类吃光 max_terms)
    quota = _allocate({g["id"]: len(g["words"]) for g in groups}, conf["max_terms"])

    cats_out = []
    total = 0
    seen_global: set[str] = set()
    for g in groups:
        n = quota.get(g["id"], 0)
        if n <= 0:
            continue
        picked = []
        for w in g["words"]:
            low = w.lower()
            if low in seen_global:      # 跨分类去重: 先到先得 (分类顺序 = 语义优先级)
                continue
            picked.append(w)
            seen_global.add(low)
            if len(picked) >= n:
                break
        if not picked:
            continue
        total += len(picked)
        cats_out.append({"id": g["id"], "name": g["name"], "style": g["style"],
                         "words": picked, "words_total": len(g["words"])})

    out["cats"] = cats_out
    out["total"] = total
    out["file"] = corpus["file"]
    out["title"] = corpus["title"]
    out["author"] = corpus["author"]
    out["corpus_mtime"] = corpus["mtime"]
    out["words_corpus"] = int(corpus.get("words_total") or 0)
    return out


def summary() -> dict:
    conf = load()
    corpora = scan_corpora(conf["dirs"])
    binds = []
    for sid_s, want in (conf.get("bind") or {}).items():
        try:
            sid = int(sid_s)
        except Exception:
            continue
        corpus, why = resolve(sid, _series_name(sid), conf, corpora)
        binds.append({"series_id": sid, "series": _series_name(sid),
                      "want": want, "how": why.get("how"),
                      "file": (corpus or {}).get("file") or "",
                      "title": (corpus or {}).get("title") or "",
                      "words": (corpus or {}).get("words_total") or 0})
    return {
        "ok": True,
        "enabled": conf["enabled"],
        "version": conf["version"],
        "updated": conf["updated"],
        "mode": conf["mode"],
        "modes": list(MODES),
        "dirs": conf["dirs"],
        "only": conf["only"],
        "stop": conf["stop"],
        "min_len": conf["min_len"],
        "max_terms": conf["max_terms"],
        "styles": conf["styles"],
        "conf": conf,
        "corpora": corpora,
        "binds": binds,
        "series": _series_rows(),
        "path": str(path()),
    }


def _series_name(sid: int) -> str:
    try:
        from sync import kavita_db
        return kavita_db.series_name(sid)
    except Exception:
        return ""


def _series_rows() -> list[dict]:
    try:
        from sync import kavita_db
        return kavita_db.all_series()
    except Exception:
        return []


def main() -> None:  # 手工排障: python3 -m sync.keyword_mark [summary|payload N|pick N]
    import sys
    arg = sys.argv[1:] or ["summary"]
    if arg[0] == "summary":
        print(json.dumps(summary(), ensure_ascii=False, indent=2)[:4000])
    elif arg[0] == "payload":
        sid = int(arg[1]) if len(arg) > 1 else 0
        print(json.dumps(build_payload(sid, _series_name(sid)), ensure_ascii=False)[:2000])
    elif arg[0] == "pick":
        sid = int(arg[1]) if len(arg) > 1 else 0
        c, why = resolve(sid, _series_name(sid))
        print(json.dumps({"series": sid, "name": _series_name(sid), "why": why,
                          "file": (c or {}).get("file")}, ensure_ascii=False))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
