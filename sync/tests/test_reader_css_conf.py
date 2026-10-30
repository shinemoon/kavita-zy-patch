"""reader_css 配置层回归测试 (行间距联动 / 清洗 / 渲染)。

不碰真配置: 用临时 PATCH_ROOT + ZY_PATCH_ROOT 环境变量。
跑: python3 tests/test_reader_css_conf.py
"""
from __future__ import annotations

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
    tmp = Path(tempfile.mkdtemp(prefix="zycss-conf-"))
    root = tmp / "patch"
    (root / "state").mkdir(parents=True)
    os.environ["ZY_PATCH_ROOT"] = str(root)

    from sync import reader_css as rc  # noqa: E402

    # 用户在 5012 里自己加的规则, id 是从名字派生的 (rule / rule-2 / rule-3)
    user_conf = {
        "enabled": True,
        "items": [
            {"id": "rule", "name": "字体", "selector": ".book-content p",
             "decls": {"font-family": "寒蝉活宋体_復"}, "important": True},
            {"id": "rule-2", "name": "行间距", "selector": ".book-content p",
             "decls": {"line-height": "1.8"}, "important": True},
            {"id": "rule-3", "name": "阅读底色",
             "selector": ".reader-container.reader-container",
             "decls": {"background": "#ddd"}, "important": True},
        ],
    }

    print("1) 行间距滑块: 按 decls 找, 不按 id 找")
    conf = rc.set_line_height(rc.normalize(user_conf), "1.55")
    lh = [i for i in conf["items"] if "line-height" in i["decls"]]
    check("只有一条 line-height 规则", len(lh) == 1, len(lh))
    check("值被改到 1.55", lh and lh[0]["decls"]["line-height"] == "1.55",
          lh and lh[0]["decls"])
    check("没新建多余规则", len(conf["items"]) == 3, len(conf["items"]))
    check("id 保持 rule-2", lh and lh[0]["id"] == "rule-2", lh and lh[0]["id"])

    print("2) 两条 line-height 规则: 一起改 (否则互相打架)")
    two = rc.normalize({
        "enabled": True,
        "items": [
            {"id": "a", "name": "x", "selector": ".book-content p",
             "decls": {"line-height": "1.2"}},
            {"id": "b", "name": "y", "selector": ".book-content div",
             "decls": {"line-height": "2.0"}},
        ],
    })
    conf2 = rc.set_line_height(two, "1.8")
    vals = sorted(i["decls"]["line-height"] for i in conf2["items"]
                  if "line-height" in i["decls"])
    check("两条都被设成 1.8", vals == ["1.8", "1.8"], vals)

    print("3) 没有 line-height 规则: 建一条")
    empty = rc.normalize({"enabled": True, "items": [
        {"id": "r", "name": "字体", "selector": ".book-content p",
         "decls": {"color": "#666"}}]})
    conf3 = rc.set_line_height(empty, "1.5")
    lh3 = [i for i in conf3["items"] if "line-height" in i["decls"]]
    check("新建了一条", len(lh3) == 1, len(lh3))
    check("id=line-height", lh3 and lh3[0]["id"] == "line-height")
    check("值 1.5", lh3 and lh3[0]["decls"]["line-height"] == "1.5")

    print("4) 渲染: !important + 选择器")
    css = rc.render_css(rc.normalize(user_conf))
    check("有 line-height 1.8", "line-height: 1.8 !important" in css, css[:200])
    check("有阅读底色", "background: #ddd !important" in css)
    check("版本头", css.startswith("/* zy-reader-css v"))

    print("5) 清洗: 坏选择器 / 坏属性被丢掉")
    dirty = rc.normalize({
        "enabled": True,
        "items": [
            {"id": "x", "name": "bad", "selector": ".a{color:red}", "decls": {"color": "#111"}},
            {"id": "y", "name": "bad2", "selector": ".b", "decls": {"color": "red;background:url(x)"}},
            {"id": "z", "name": "bad3", "selector": "", "decls": {"color": "#222"}},
        ],
    })
    sels = [i["selector"] for i in dirty["items"]]
    check("花括号被剥掉", "{color:red}" not in sels, sels)
    check("空选择器那条被丢", len(dirty["items"]) == 2, len(dirty["items"]))
    check("声明里的分号被剥", all(";" not in v for i in dirty["items"]
                                for v in i["decls"].values()))

    print("6) 落盘 + version 自增")
    saved = rc.save(rc.normalize(user_conf))
    check("文件落盘", (root / "state" / "reader_css.json").exists())
    check("version >= 1", saved["version"] >= 1, saved["version"])
    again = rc.save(rc.load())
    check("再存 version 递增", again["version"] == saved["version"] + 1,
          (saved["version"], again["version"]))
    check("load 回来条目数对", len(rc.load()["items"]) == 3, len(rc.load()["items"]))

    print("7) 文件坏了不炸")
    (root / "state" / "reader_css.json").write_text("{not json", encoding="utf-8")
    bad = rc.load()
    check("坏文件 → enabled=False", bad["enabled"] is False, bad["enabled"])
    check("坏文件 → 无条目", bad["items"] == [], bad["items"])

    print()
    if FAIL:
        print(f"FAILED {len(FAIL)}: {FAIL}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
