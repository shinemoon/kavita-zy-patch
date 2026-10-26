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

# ---- 阅读主题 (2026-10-26) --------------------------------------------------
#
# 用户要的三选一: 默认 (Kavita 原界面, 一个字都不改) / 飞书云文档 / 代码 Console。
# **主题只动字体/颜色/底色/页边距这一类"外观", 绝不写 line-height** ——
# 行间距永远由上面那条 line-height 规则独立控制 (主题切换不影响滑块)。
# 主题规则先渲染, 用户规则后渲染 → 冲突时用户的规则赢。
#
# 选择器只用 Kavita 阅读器的真实结构 (实测 DOM, 2026-10-26):
#   .reader-container           整页底 (实测白底)
#   .reading-section.immersive  上下 38px padding
#   .book-container.immersive
#   .book-content.immersive     正文容器 (实测 840px 宽, 里面是 epub 自带 div/p)
# epub 自带的内联 font-family 要整片压掉 → 用 `.book-content *` 打 !important
# (author !important 能压 inline style, 普通声明压不住)。
_THEME_FONT_FEISHU = ('-apple-system, "PingFang SC", "Noto Sans SC", '
                      '"Microsoft YaHei", "Helvetica Neue", sans-serif')
_THEME_FONT_CONSOLE = ('"JetBrains Mono", "Fira Code", ui-monospace, SFMono-Regular, '
                       'Menlo, Consolas, "Noto Sans Mono CJK SC", monospace')

# Kavita 的主题 CSS (brtheme-white) 里有这几条硬声明, 跟我们同级甚至更晚:
#   .reader-container { color: black !important; background-color: white !important; }   ← !important
#   .book-content :not(code), .book-content :not(a) { background-color: white; ... }
# 所以我们的选择器要**抬一级特异度** (双写类名) 才压得住; 光靠 !important 不够
# (同级 !important 谁在后面谁赢, 而注入的 <style> 在 brtheme 之前)。
_K = ".book-content.book-content"          # 特异度 (0,2,0)
_CANVAS = ".reader-container.reader-container"

THEMES = [
    {
        "id": "default",
        "name": "默认（Kavita 原界面）",
        "desc": "一个字都不改，只应用下面这些规则（行间距等）",
        "swatch": ["#1f2020", "#efefef"],
        "rules": [],
    },
    {
        "id": "feishu",
        "name": "飞书云文档",
        "desc": "浅灰画布 + 白色纸张卡片，苹方/思源黑，正文 #1f2329，链接飞书蓝",
        "swatch": ["#f2f3f5", "#3370ff"],
        "rules": [
            {"selector": _CANVAS,
             "decls": {"background-color": "#f2f3f5"}},
            {"selector": _K,
             "decls": {
                 "background-color": "#ffffff",
                 "width": "100%",
                 "max-width": "780px",
                 "margin": "0 auto",
                 "padding": "clamp(20px, 4vw, 48px) clamp(16px, 4vw, 52px) 56px",
                 "border-radius": "8px",
                 "box-shadow": "0 1px 2px rgba(31,35,41,.06), 0 6px 18px rgba(31,35,41,.06)",
                 "box-sizing": "border-box",
             }},
            {"selector": _K + ", " + _K + " *",
             "decls": {"font-family": _THEME_FONT_FEISHU}},
            {"selector": _K + " *",
             "decls": {"color": "#1f2329", "letter-spacing": "0"}},
            {"selector": _K + " h1, " + _K + " h2, " + _K + " h3, " + _K + " h4",
             "decls": {"color": "#1f2329", "font-weight": "600"}},
            {"selector": _K + " a",
             "decls": {"color": "#3370ff"}},
            {"selector": _K + " blockquote",
             "decls": {"color": "#646a73", "background-color": "#f7f8fa",
                       "border-left": "3px solid #dee0e3", "margin-left": "0",
                       "padding": "8px 14px", "border-radius": "0 4px 4px 0"}},
            {"selector": _K + " hr",
             "decls": {"border-color": "#dee0e3"}},
            {"selector": _K + " table, " + _K + " th, " + _K + " td",
             "decls": {"border-color": "#dee0e3"}},
            {"selector": _K + " th",
             "decls": {"background-color": "#f5f6f7"}},
            {"selector": _K + " img",
             "decls": {"border-radius": "4px"}},
            {"selector": _K + " ::selection",
             "decls": {"background": "rgba(51,112,255,.22)"}},
        ],
    },
    {
        "id": "console",
        "name": "代码 Console",
        "desc": "深色终端底 + 等宽字体，标题绿、链接青、引用灰",
        "swatch": ["#0b0e14", "#7ee787"],
        "rules": [
            {"selector": _CANVAS,
             "decls": {"background-color": "#0b0e14", "color": "#c9d1d9"}},
            {"selector": _K,
             "decls": {
                 "background-color": "#0d1117",
                 "width": "100%",
                 "max-width": "900px",
                 "margin": "0 auto",
                 "padding": "28px clamp(14px, 3vw, 36px) 48px",
                 "border": "1px solid #21262d",
                 "border-radius": "6px",
                 "box-shadow": "0 10px 30px rgba(0,0,0,.35)",
                 "box-sizing": "border-box",
             }},
            {"selector": _K + ", " + _K + " *",
             "decls": {"font-family": _THEME_FONT_CONSOLE}},
            # epub 常在 div/span 上写死白底 (brtheme 也会补白底), 不压掉就是白块黑字
            {"selector": _K + " *",
             "decls": {"color": "#c9d1d9", "background-color": "transparent",
                       "letter-spacing": "0"}},
            {"selector": _K + " h1, " + _K + " h2, " + _K + " h3, " + _K + " h4",
             "decls": {"color": "#7ee787", "border-left": "3px solid #238636",
                       "padding-left": "10px", "background-color": "transparent"}},
            {"selector": _K + " a",
             "decls": {"color": "#58a6ff", "background-color": "transparent"}},
            {"selector": _K + " blockquote",
             "decls": {"color": "#8b949e", "border-left": "3px solid #30363d",
                       "margin-left": "0", "padding-left": "12px",
                       "background-color": "transparent"}},
            {"selector": _K + " hr",
             "decls": {"border-color": "#21262d"}},
            {"selector": _K + " table, " + _K + " th, " + _K + " td",
             "decls": {"border-color": "#30363d"}},
            {"selector": _K + " ::selection",
             "decls": {"background": "rgba(56,139,253,.35)"}},
        ],
    },
]
THEME_IDS = [t["id"] for t in THEMES]
DEFAULT_THEME = "default"


def theme_meta() -> list[dict]:
    """给 5012 前端用 (不含 rules, 省流量)。"""
    return [{"id": t["id"], "name": t["name"], "desc": t["desc"],
             "swatch": t.get("swatch") or []} for t in THEMES]


def _theme_rules(theme_id) -> list[dict]:
    t = next((x for x in THEMES if x["id"] == theme_id), None)
    return list(t["rules"]) if t else []


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
        "theme": DEFAULT_THEME,
        "items": [json.loads(json.dumps(i)) for i in DEFAULT_ITEMS],
    }


def normalize(conf) -> dict:
    """把任意输入收敛成合法配置。坏字段丢掉, 不抛错 (502 不该因为一条脏规则消失)。"""
    conf = conf if isinstance(conf, dict) else {}
    theme = str(conf.get("theme") or DEFAULT_THEME).strip().lower()
    if theme not in THEME_IDS:
        theme = DEFAULT_THEME
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
        "theme": theme,
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


def _rule_line(selector: str, decls: dict, important: bool = True) -> str:
    bang = " !important" if important else ""
    body = " ".join(f"{k}: {v}{bang};" for k, v in decls.items())
    return f"{selector} {{ {body} }}"


def render_css(conf) -> str:
    conf = normalize(conf)
    if not conf["enabled"]:
        return ""
    lines = [CSS_VER_HEADER.format(ver=conf["version"], ts=conf["updated"] or "-")]
    # 主题先行: 用户规则渲染在后面, 冲突时用户赢 (主题不写 line-height)
    theme = conf.get("theme") or DEFAULT_THEME
    for rule in _theme_rules(theme):
        sel = _clean_selector(rule.get("selector"))
        decls = _clean_decls(rule.get("decls"))
        if sel and decls:
            lines.append(f"/* theme: {theme} */ " + _rule_line(sel, decls))
    for it in conf["items"]:
        if not it["enabled"]:
            continue
        lines.append(_rule_line(it["selector"], it["decls"],
                                it.get("important", True)))
    if len(lines) == 1:
        return ""
    return "\n".join(lines) + "\n"


def set_theme(conf, theme) -> dict:
    """切换阅读主题。只动 theme 字段 —— items (行间距等) 一个字都不碰。"""
    conf = normalize(conf)
    t = str(theme or "").strip().lower()
    conf["theme"] = t if t in THEME_IDS else DEFAULT_THEME
    return conf


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
        "theme": conf.get("theme") or DEFAULT_THEME,
        "themes": theme_meta(),
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
