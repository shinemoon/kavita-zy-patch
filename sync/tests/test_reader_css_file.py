"""reader_css 文件侧 patch (写进 Kavita index.html) 的回归测试。

用临时 PATCH_ROOT 造一个假 index.html, 不碰真容器。
跑: python3 tests/test_reader_css_file.py
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
    tmp = Path(tempfile.mkdtemp(prefix="zycss-"))
    root = tmp / "patch"
    (root / "state" / "wwwroot").mkdir(parents=True)
    idx = root / "state" / "wwwroot" / "index.html"
    idx.write_text(
        "<!doctype html><html><head><meta charset=utf-8><title>Kavita</title>\n"
        "</head><body><app-root></app-root></body></html>\n",
        encoding="utf-8")
    (root / "patch.conf").write_text(
        'CONTAINER="kavita"\nCONTAINER_WWWROOT="/kavita/wwwroot"\n'
        f'WWWROOT_MOUNT_DIR="{root / "state" / "wwwroot"}"\n', encoding="utf-8")

    os.environ["ZY_PATCH_ROOT"] = str(root)
    from sync import reader_css as rc  # noqa: E402  (要在设好 env 之后 import)

    print("1) 初始: 没 patch, 也没 desired 选择")
    st = rc.file_state()
    check("mode=mount", st["mode"] == "mount", st["mode"])
    check("patched=False", st["patched"] is False)
    check("desired=None", rc.file_desired() is None)

    print("2) patch: 写进 <head>, id 前缀正确")
    r = rc.patch_to_kavita("test")
    text = idx.read_text(encoding="utf-8")
    check("changed=True", r["changed"] is True)
    check("块只有一个", text.count(rc.KAVITA_BEGIN) == 1, text.count(rc.KAVITA_BEGIN))
    check("有 style id", f'id="{rc.FILE_STYLE_ID}"' in text)
    check("在 </head> 之前", text.index(rc.KAVITA_BEGIN) < text.index("</head>"))
    check("app-root 没被动", "<app-root></app-root>" in text)
    st = rc.file_state()
    check("file_state patched", st["patched"] is True)
    check("file_state 版本 == 配置版本", st["version"] == rc.load()["version"],
          (st["version"], rc.load()["version"]))
    check("desired=True", rc.file_desired() is True)

    print("3) 重复 patch: 幂等, 不重复插")
    r2 = rc.patch_to_kavita("test")
    text2 = idx.read_text(encoding="utf-8")
    check("changed=False", r2["changed"] is False)
    check("仍然只有一块", text2.count(rc.KAVITA_BEGIN) == 1)
    check("文件内容没变", text2 == text)

    print("4) 配置改版本后 patch: 版本跟着变")
    conf = rc.load()
    conf["items"][0]["decls"]["line-height"] = "2.1"
    saved = rc.save(conf)
    rc.patch_to_kavita("test")
    st = rc.file_state()
    check("版本同步到新配置", st["version"] == saved["version"], (st["version"], saved["version"]))
    check("css 里有 2.1", "2.1" in idx.read_text(encoding="utf-8"))

    print("5) publish 重建 (index.html 被覆盖) → reapply 恢复")
    idx.write_text(
        "<!doctype html><html><head><title>Kavita</title></head><body></body></html>\n",
        encoding="utf-8")
    r3 = rc.reapply_after_publish()
    check("reapply 写回", r3.get("changed") is True, r3)
    check("块回来了", rc.file_state()["patched"] is True)

    print("6) unpatch: 只删文件侧, 配置不动; publish 不再塞回去")
    ver_before = rc.load()["version"]
    r4 = rc.unpatch_from_kavita()
    check("changed=True", r4["changed"] is True)
    check("文件里没有标记", "ZY-READER-CSS" not in idx.read_text(encoding="utf-8"))
    check("配置版本不变", rc.load()["version"] == ver_before)
    check("desired=False", rc.file_desired() is False)
    idx.write_text(
        "<!doctype html><html><head><title>Kavita</title></head><body></body></html>\n",
        encoding="utf-8")
    r5 = rc.reapply_after_publish()
    check("publish 跳过 (removed_by_user)", r5.get("skipped") == "removed_by_user", r5)
    check("确实没塞回去", "ZY-READER-CSS" not in idx.read_text(encoding="utf-8"))

    print("7) 半截块 (只有 BEGIN 没有 END) 也能清掉")
    idx.write_text(
        "<html><head><!-- ZY-READER-CSS-BEGIN -->\n<style>p{color:red}</style>\n"
        "</head><body></body></html>\n", encoding="utf-8")
    rc.unpatch_from_kavita()
    t = idx.read_text(encoding="utf-8")
    check("BEGIN 被清掉", "ZY-READER-CSS-BEGIN" not in t)
    check("注入的 style 也被清掉", "color:red" not in t)

    print()
    if FAIL:
        print(f"{len(FAIL)} 项失败: {', '.join(FAIL)}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
