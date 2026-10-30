"""Kavita 阅读样式增强 (2026-10-26)

5012 控制台里增删的阅读排版规则 (目前只有"行间距", 结构上任意 CSS 规则都行),
保存成一份 JSON。生效有两条路:

1. **运行时注入** (默认): 补丁前端 (`zy-kavita-patch.js`) 定期拉
   `/zy/api/reader-css` 后注入 `<style id="zy-reader-css">`, 60s 内生效。
2. **文件侧 patch** (可选): `patch_to_kavita()` 把渲染结果直接写进 Kavita 的
   index.html (`<!-- ZY-READER-CSS-BEGIN -->` 包一段 <style>)。不依赖后端/JS,
   但浏览器可能缓存 index.html, 改完要强刷才看到。

配置文件落在补丁仓 (跟补丁后端共用一份):
    <PATCH_ROOT>/state/reader_css.json      默认 /vol2/1001/kavita-zy-patch/state/

一条规则 = {id, name, enabled, selector, decls:{prop: value}, important}
渲染 = selector { prop: value !important; ... }
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
    targets = [i for i in conf["items"] if "line-height" in (i.get("decls") or {})]
    if not targets:
        item = {"id": "line-height", "name": "行间距", "enabled": True,
                "selector": TEXT_SELECTOR, "decls": {}, "important": True}
        conf["items"].insert(0, item)
        targets = [item]
    for it in targets:
        it["decls"]["line-height"] = str(value)
        it["enabled"] = True
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
        "file": file_state(),
    }


# ---- patch 到 Kavita 文件侧 (2026-10-26) ------------------------------------
#
# 运行时注入 (render_css + 前端 <style id="zy-reader-css">) 依赖补丁后端活着、
# JS 跑起来。想更"硬"一点就写进 Kavita 自己的 index.html: 浏览器一进来就有样式,
# 与后端/JS 无关。文件侧是**兜底**, 运行时注入照旧 (内容相同, 重复无害)。
#
# 落点:
#   挂载模式 → <PATCH_ROOT>/state/wwwroot/index.html —— **就地覆盖, 保留 inode**;
#              os.replace/rm+mv 会换 inode, 容器里的单文件 bind mount 会指旧文件
#   拷贝模式 → docker cp 出容器 → 改 → docker cp 回去
#
# 幂等: 每次先剥掉我们自己那一段再重写, 不会越写越多。
KAVITA_BEGIN = "<!-- ZY-READER-CSS-BEGIN -->"
KAVITA_END = "<!-- ZY-READER-CSS-END -->"
FILE_STYLE_ID = "zy-reader-css-file"

_BLOCK_RE = re.compile(
    re.escape(KAVITA_BEGIN) + r".*?" + re.escape(KAVITA_END) + r"\s*", re.S)
_LOOSE_RE = re.compile(
    re.escape(KAVITA_BEGIN) + r".*?</style>\s*(?:" + re.escape(KAVITA_END) + r")?", re.S)
_VER_IN_CSS = re.compile(r"zy-reader-css v(\d+)")


def _patch_conf() -> dict[str, str]:
    """读补丁仓 patch.conf 的 KEY="VALUE" (只认我们需要的几个键)。"""
    out: dict[str, str] = {}
    p = patch_root() / "patch.conf"
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return out


def wwwroot_target() -> dict:
    """文件侧 index.html 在哪。挂载模式看宿主, 否则走 docker cp。"""
    conf = _patch_conf()
    mount_dir = conf.get("WWWROOT_MOUNT_DIR") or str(patch_root() / "state" / "wwwroot")
    host = Path(mount_dir) / "index.html"
    container = conf.get("CONTAINER") or "kavita"
    inner = (conf.get("CONTAINER_WWWROOT") or "/kavita/wwwroot").rstrip("/") + "/index.html"
    if host.exists():
        return {"mode": "mount", "host": str(host), "container": container, "inner": inner}
    return {"mode": "copy", "host": "", "container": container, "inner": inner}


def _run(cmd: list[str], timeout: int = 30) -> tuple[int, str]:
    import subprocess
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:  # noqa: BLE001
        return 1, f"{type(e).__name__}: {e}"


def read_index(target: dict) -> str:
    if target["mode"] == "mount":
        return Path(target["host"]).read_text(encoding="utf-8", errors="replace")
    rc, out = _run(["docker", "cp", f"{target['container']}:{target['inner']}", "-"])
    if rc != 0:
        raise RuntimeError(f"docker cp 取 index.html 失败: {out.strip()[:200]}")
    return out


def write_index(target: dict, text: str) -> None:
    if target["mode"] == "mount":
        # 就地截断写: 同一 inode, bind mount 立刻看到新内容
        with open(target["host"], "w", encoding="utf-8") as f:
            f.write(text)
        return
    tmp = str(patch_root() / "build" / "index.html.zycss")
    Path(tmp).parent.mkdir(parents=True, exist_ok=True)
    Path(tmp).write_text(text, encoding="utf-8")
    rc, out = _run(["docker", "cp", tmp, f"{target['container']}:{target['inner']}"])
    if rc != 0:
        raise RuntimeError(f"docker cp 写 index.html 失败: {out.strip()[:200]}")


def _strip_block(text: str) -> str:
    text = _BLOCK_RE.sub("", text or "")
    return _LOOSE_RE.sub("", text)  # 半截 (只有 BEGIN 没有 END) 也清掉


def _build_block(css: str) -> str:
    if not (css or "").strip():
        return ""
    return (f"{KAVITA_BEGIN}\n"
            f'<style id="{FILE_STYLE_ID}">\n{css}</style>\n'
            f"{KAVITA_END}\n")


def _insert_block(text: str, block: str) -> str:
    if not block:
        return text
    i = text.lower().rfind("</head>")
    if i < 0:
        raise RuntimeError("index.html 里找不到 </head>, 拒绝瞎写")
    return text[:i] + block + text[i:]


# "文件侧要不要有这段" 是用户的显式选择 —— 单独记一份。因为 install.sh publish
# 会用镜像原版 index.html 重建 (那一段必然被冲掉), 重建后要按这个选择恢复,
# 而不是"有配置就重新塞回去"(用户点了「从文件移除」就得一直移除)。
FILE_STATE_NAME = "reader_css_file.json"


def _file_flag_path() -> Path:
    return patch_root() / "state" / FILE_STATE_NAME


def file_desired():
    """True=应该 patch / False=用户显式移除 / None=从没 patch 过。"""
    try:
        v = json.loads(_file_flag_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception:
        return None
    return bool(v.get("desired"))


def set_file_desired(v) -> None:
    p = _file_flag_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"desired": bool(v),
                               "ts": time.strftime("%Y-%m-%dT%H:%M:%S")},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)
    try:
        st = p.parent.stat()
        os.chmod(p, 0o644)
        os.chown(p, st.st_uid, st.st_gid)
    except Exception:
        pass


def reapply_after_publish() -> dict:
    """install.sh publish 重建 index.html 之后调 —— 尊重用户「移除」的选择。"""
    d = file_desired()
    if d is None:
        return {"ok": True, "skipped": "never_patched"}
    if not d:
        return {"ok": True, "skipped": "removed_by_user"}
    return patch_to_kavita("publish")


def file_state() -> dict:
    """文件侧现在有没有我们这段、是哪一版 (从文件里读, 不存额外状态)。"""
    target = wwwroot_target()
    st = {"mode": target["mode"], "host": target["host"],
          "container": target["container"], "inner": target["inner"],
          "patched": False, "version": None, "css_bytes": 0, "error": "",
          "desired": file_desired()}
    try:
        text = read_index(target)
    except Exception as e:  # noqa: BLE001
        st["error"] = str(e)[:200]
        return st
    m = _BLOCK_RE.search(text) or _LOOSE_RE.search(text)
    if not m:
        return st
    block = m.group(0)
    st["patched"] = True
    st["css_bytes"] = len(block)
    vm = _VER_IN_CSS.search(block)
    if vm:
        try:
            st["version"] = int(vm.group(1))
        except Exception:
            pass
    return st


def patch_to_kavita(reason: str = "manual") -> dict:
    """把当前配置渲染成 CSS, 写进 Kavita 的 index.html (幂等)。"""
    conf = load()
    css = render_css(conf)
    target = wwwroot_target()
    if target["mode"] == "copy":
        rc, out = _run(["docker", "inspect", target["container"], "--format", "{{.Id}}"])
        if rc != 0:
            raise RuntimeError(f"容器 {target['container']} 不可用: {out.strip()[:120]}")
    text = read_index(target)
    new = _insert_block(_strip_block(text), _build_block(css))
    set_file_desired(True)
    if new == text:
        return {"ok": True, "mode": target["mode"], "changed": False,
                "patched": bool(css.strip()), "version": conf["version"],
                "css_bytes": len(css), "reason": reason,
                "where": target["host"] or f"{target['container']}:{target['inner']}"}
    write_index(target, new)
    return {"ok": True, "mode": target["mode"], "changed": True,
            "patched": bool(css.strip()), "version": conf["version"],
            "css_bytes": len(css), "reason": reason,
            "where": target["host"] or f"{target['container']}:{target['inner']}"}


def unpatch_from_kavita() -> dict:
    """把文件侧那段去掉 (Kavita 恢复原样), 运行时注入不受影响。"""
    target = wwwroot_target()
    text = read_index(target)
    new = _strip_block(text)
    set_file_desired(False)
    if new == text:
        return {"ok": True, "changed": False, "patched": False,
                "where": target["host"] or f"{target['container']}:{target['inner']}"}
    write_index(target, new)
    return {"ok": True, "changed": True, "patched": False,
            "where": target["host"] or f"{target['container']}:{target['inner']}"}


def main() -> None:  # 手工排障用: python3 sync/reader_css.py [show|path|patch|unpatch|file]
    import sys
    arg = sys.argv[1] if len(sys.argv) > 1 else "show"
    if arg == "show":
        print(json.dumps(summary(), ensure_ascii=False, indent=2))
    elif arg == "path":
        print(path())
    elif arg == "file":
        print(json.dumps(file_state(), ensure_ascii=False, indent=2))
    elif arg in ("patch", "apply"):
        print(json.dumps(patch_to_kavita("cli"), ensure_ascii=False, indent=2))
    elif arg in ("unpatch", "strip", "remove"):
        print(json.dumps(unpatch_from_kavita(), ensure_ascii=False, indent=2))
    else:
        print("usage: reader_css.py [show|path|file|patch|unpatch]")


if __name__ == "__main__":
    main()
