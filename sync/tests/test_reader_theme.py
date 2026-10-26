"""阅读主题 (默认 / 飞书云文档 / 代码 Console) 的回归测试。

要点:
  - 主题规则**绝不包含 line-height** (行间距永远由独立规则控制)
  - 切主题不动 items (行间距值保持原样)
  - 主题规则渲染在用户规则之前 (冲突时用户赢)
  - 非法 theme 名退回 default; 老配置 (没有 theme 字段) 也能读

跑: python3 tests/test_reader_theme.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FAIL = []


def check(name, cond, extra=""):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {extra}".rstrip())
        FAIL.append(name)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="zytheme-"))
    root = tmp / "patch"
    (root / "state").mkdir(parents=True)
    os.environ["ZY_PATCH_ROOT"] = str(root)

    from sync import reader_css as rc  # noqa: E402

    print("1) 主题清单")
    ids = [t["id"] for t in rc.THEMES]
    check("三个主题", ids == ["default", "feishu", "console"], ids)
    check("meta 三份", len(rc.theme_meta()) == 3)
    check("meta 不带 rules", all("rules" not in m for m in rc.theme_meta()))
    check("default 无规则", rc._theme_rules("default") == [])

    print("2) 主题规则只动外观, 不写 line-height")
    for tid in ("feishu", "console"):
        rules = rc._theme_rules(tid)
        check(f"{tid} 有规则", len(rules) >= 8, len(rules))
        bad = [r for r in rules
               if any("line-height" in k.lower() for k in r["decls"])]
        check(f"{tid} 无 line-height", bad == [], bad[:1])
        css = rc.render_css(rc.set_theme(rc.default_conf(), tid))
        check(f"{tid} 渲染含主题注释", f"/* theme: {tid} */" in css)
        # 每一条重要属性都带 !important (否则压不住 Kavita 的 brtheme)
        body = [ln for ln in css.splitlines() if f"theme: {tid} */" in ln]
        check(f"{tid} 全部 !important",
              all(ln.rstrip().endswith("}") and "!important" in ln for ln in body))
        # 双写类名抬特异度 (brtheme-white 的 !important 才压得住)
        check(f"{tid} 抬特异度", any(".book-content.book-content" in ln for ln in body))

    print("3) 切主题不影响行间距 (items 原样)")
    conf = rc.set_line_height(rc.default_conf(), 2.1)
    for tid in ("console", "feishu", "default"):
        conf = rc.set_theme(conf, tid)
        lh = [i for i in conf["items"] if i["id"] == "line-height"][0]
        check(f"{tid} 行距保持 2.1", lh["decls"]["line-height"] == "2.1", lh["decls"])
    conf = rc.set_line_height(rc.set_theme(conf, "console"), 1.35)
    check("切行距不动主题", conf["theme"] == "console", conf["theme"])

    print("4) 渲染顺序: 主题在前, 用户规则在后")
    css = rc.render_css(conf)
    i_theme = css.find("/* theme: console */")
    i_lh = css.find("line-height: 1.35")
    check("都渲染出来了", i_theme > 0 and i_lh > 0, (i_theme, i_lh))
    check("主题在前", i_theme < i_lh, (i_theme, i_lh))

    print("5) 非法 / 缺失 theme")
    check("非法名退回 default", rc.normalize({"theme": "neon"})["theme"] == "default")
    check("缺字段 = default", rc.normalize({"items": []})["theme"] == "default")
    check("大小写容错", rc.normalize({"theme": "FEISHU"})["theme"] == "feishu")

    print("6) 落盘往返 (老配置没有 theme → default)")
    rc.save(rc.set_theme(rc.default_conf(), "console"))
    rc.path().write_text(json.dumps({"version": 3, "enabled": True,
                                     "items": rc.DEFAULT_ITEMS}), encoding="utf-8")
    got = rc.load()
    check("老配置读成 default", got["theme"] == "default", got["theme"])
    saved = rc.save(rc.set_theme(got, "feishu"))
    check("save 后是 feishu", rc.load()["theme"] == "feishu", saved["theme"])
    check("save 自增版本", saved["version"] == got["version"] + 1,
          (got["version"], saved["version"]))
    check("summary 带 themes", len(rc.summary()["themes"]) == 3)

    print("7) 关掉 enabled → 主题也不生效")
    off = rc.set_theme(rc.default_conf(), "console")
    off["enabled"] = False
    check("禁用渲染为空", rc.render_css(off) == "")

    print()
    if FAIL:
        print(f"FAILED {len(FAIL)}: {FAIL}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
