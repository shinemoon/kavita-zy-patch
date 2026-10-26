"""Kavita 阅读样式增强 (2026-10-26)

5012 控制台里增删的阅读排版规则 (目前只有"行间距", 结构上任意 CSS 规则都行),
保存成一份 JSON, 由补丁前端 (`zy-kavita-patch.js`) 定期拉 `/zy/api/reader-css`
后注入 `<style>` 到 Kavita 页面 —— **不改 Kavita 文件, 不重启容器**。

配置文件落在补丁仓 (跟补丁后端共用一份):
    <PATCH_ROOT>/state/reader_css.json      默认 /vol2/1001/kavita-zy-patch/state/

一条规则 = {id, name, enabled, selector, decls:{prop: value}, important}
渲染 = selector { prop: value !important; ... }

为什么不写进 wwwroot: 那里是 bind mount 的两个固定文件 + 容器 overlay,
新增文件既没挂载、容器重建还会丢; 前端注入则跟着补丁版本走, 永远最新。
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

CONF_NAME = "reader_css.json"

MAX_ITEMS = 40
MAX_SELECTOR = 400
MAX_DECL_VAL = 200
CSS_VER_HEADER = "/* zy-reader-css v{ver} · {ts} · 5012 阅读样式增强 */"

# 常用阅读元素: 行高要打到所有会自带 line-height 的块级元素上,
# 只写 .book-content 会被 epub 自带的 p { line-height } 盖掉。
TEXT_SELECTOR = (".book-content, .book-content p, .book-content div, "
                 ".book-content li, .book-content blockquote, .book-content td")

DEFAULT_ITEMS = [
    {
        "id": "line-height",
        "name": "行间距",
        "enabled": True,
        "selector": TEXT_SELECTOR,
        "decls": {"line-height": "1.8"},
        "important": True,
    },
]

# 控制台「快速添加」用的预设 (前端也读这份, 保持单一来源)
PRESETS = [
    {"name": "行间距", "selector": TEXT_SELECTOR, "decls": {"line-height": "1.8"}},
    {"name": "段间距", "selector": ".book-content p", "decls": {"margin": "0.85em 0"}},
    {"name": "字间距", "selector": TEXT_SELECTOR, "decls": {"letter-spacing": "0.02em"}},
    {"name": "首行缩进", "selector": ".book-content p", "decls": {"text-indent": "2em"}},
    {"name": "两端对齐", "selector": ".book-content p", "decls": {"text-align": "justify"}},
    {"name": "正文字号", "selector": TEXT_SELECTOR, "decls": {"font-size": "1.05em"}},
]

_PROP_RE = re.compile(r"^[a-z][a-z-]{1,40}$")


def patch_root() -> Path:
    return Path(os.environ.get("ZY_PATCH_ROOT", "/vol2/1001/kavita-zy-patch"))


def path() -> Path:
    return patch_root() / "state" / CONF_NAME


# ---- 清洗 / 校验 -----------------------------------------------------------

def _clean_selector(s) -> str:
    s = str(s or "").replace("{", "").replace("}", "").replace("<", "").replace(">", "")
    s = s.replace("@", "").replace(";", "").strip()
    return s[:MAX_SELECTOR]


def _clean_decls(d) -> dict[str, str]:
    out: dict[str, str] = {}
    if not isinstance(d, dict):
        return out
    for k, v in d.items():
        k = str(k or "").strip().lower()
        if not _PROP_RE.match(k):
            continue
        v = str(v if v is not None else "").strip()
        for bad in ("{", "}", "<", ">", ";", "@import"):
            v = v.replace(bad, "")
        v = v.strip()[:MAX_DECL_VAL]
        if v:
            out[k] = v
    return out


def _uniq_id(base: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9_-]+", "-", str(base or "rule").lower()).strip("-") or "rule"
    base = base[:40]
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def default_conf() -> dict:
    return {
        "version": 1,
        "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "enabled": True,
        "items": [json.loads(json.dumps(i)) for i in DEFAULT_ITEMS],
    }


def normalize(conf) -> dict:
    """把任意输入收敛成合法配置。坏字段丢掉, 不抛错 (502 不该因为一条脏规则消失)。"""
    conf = conf if isinstance(conf, dict) else {}
    items_in = conf.get("items")
    items_in = items_in if isinstance(items_in, list) else []
    taken: set[str] = set()
    items: list[dict] = []
    for it in items_in[:MAX_ITEMS]:
        if not isinstance(it, dict):
            continue
        sel = _clean_selector(it.get("selector"))
        decls = _clean_decls(it.get("decls"))
        if not sel or not decls:
            continue
        iid = _uniq_id(it.get("id") or it.get("name"), taken)
        taken.add(iid)
        items.append({
            "id": iid,
            "name": str(it.get("name") or iid)[:60],
            "enabled": bool(it.get("enabled", True)),
            "selector": sel,
            "decls": decls,
            "important": bool(it.get("important", True)),
        })
    try:
        ver = int(conf.get("version") or 1)
    except Exception:
        ver = 1
    return {
        "version": max(1, ver),
        "updated": str(conf.get("updated") or ""),
        "enabled": bool(conf.get("enabled", True)),
        "items": items,
    }


# ---- 读写 ------------------------------------------------------------------

def load() -> dict:
    p = path()
    if not p.exists():
        return default_conf()
    try:
        conf = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        # 文件坏了不要静默丢规则 —— 让调用方看到空配置, 但保留原文件
        return normalize({"enabled": False, "items": []})
    return normalize(conf)


def save(conf) -> dict:
    """校验 + 落盘 + version 自增 (前端靠 version 变化决定要不要重注入)。"""
    cur = load()
    new = normalize(conf)
    new["version"] = int(cur.get("version") or 1) + 1
    new["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)
    try:  # 5012 以 root 跑, 补丁其他工具是普通用户
        parent = p.parent
        st = parent.stat()
        os.chmod(p, 0o644)
        os.chown(p, st.st_uid, st.st_gid)
    except Exception:
        pass
    return new


def render_css(conf) -> str:
    conf = normalize(conf)
    if not conf["enabled"]:
        return ""
    lines = [CSS_VER_HEADER.format(ver=conf["version"], ts=conf["updated"] or "-")]
    for it in conf["items"]:
        if not it["enabled"]:
            continue
        bang = " !important" if it.get("important", True) else ""
        body = " ".join(f"{k}: {v}{bang};" for k, v in it["decls"].items())
        lines.append(f"{it['selector']} {{ {body} }}")
    if len(lines) == 1:
        return ""
    return "\n".join(lines) + "\n"


def set_line_height(conf, value) -> dict:
    """控制台的"行间距"滑块走这条: 没有就建, 有就改 decls['line-height']。"""
    conf = normalize(conf)
    item = next((i for i in conf["items"] if i["id"] == "line-height"), None)
    if item is None:
        item = {"id": "line-height", "name": "行间距", "enabled": True,
                "selector": TEXT_SELECTOR, "decls": {}, "important": True}
        conf["items"].insert(0, item)
    item["decls"]["line-height"] = str(value)
    item["enabled"] = True
    return conf


def summary() -> dict:
    conf = load()
    return {
        "ok": True,
        "enabled": conf["enabled"],
        "version": conf["version"],
        "updated": conf["updated"],
        "items": conf["items"],
        "presets": PRESETS,
        "css": render_css(conf),
        "path": str(path()),
    }


def main() -> None:  # 手工排障用: python3 sync/reader_css.py [show]
    import sys
    arg = sys.argv[1] if len(sys.argv) > 1 else "show"
    if arg == "show":
        print(json.dumps(summary(), ensure_ascii=False, indent=2))
    elif arg == "path":
        print(path())
    else:
        print("usage: reader_css.py [show|path]")


if __name__ == "__main__":
    main()
