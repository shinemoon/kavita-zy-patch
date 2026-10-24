"""epubprep / kpages 的离线自检 (不碰 Kavita, 不碰库文件)。

1. 3.0 声明但没有 nav 的书 → 切片版 opf 应降级成 2.0
2. 图片型 epub → prep 应 skip 而不是硬重排
3. 多卷系列 → prep 应 skip (靠 kavita_db.series_files, 这里只用假 series_id 模拟)
4. 正常文字书 → 页数/校验/换库后的文件形态
"""
import sys, shutil, zipfile, re
from pathlib import Path

sys.path.insert(0, "/vol2/1001/zhangyue-bridge/sync")
from sync import kpages, epubprep

OK = []
def check(name, cond, extra=""):
    OK.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ((" — " + str(extra)) if extra else ""))


FOX = "/vol2/1001/zhangyue-bridge/books/狐的故事.epub"
TOU = "/vol2/1001/dav/Books/掌阅/路邊的藤井/路邊的藤井 卷01.epub"


def make_epub3_no_nav(src, dst):
    """造一个"声明 3.0 但没有 nav"的书: 拿 2.0 的源, 只把 package 版本改成 3.0。

    掌阅个人云里就有一批这样的书 (例如 路邊的藤井 三卷) —— 声明 3.0 却只有 NCX,
    Kavita 的 VersOne.Epub 抛 Epub3NavException, 整本不进库。
    """
    with zipfile.ZipFile(src) as zin:
        names = zin.namelist()
        opf = next(n for n in names if n.lower().endswith(".opf"))
        raw = zin.read(opf).decode("utf-8", "replace")
        assert 'version="2.0"' in raw, "源书不是 2.0, 换个样本"
        assert "properties=\"nav\"" not in raw, "源书竟然有 nav"
        new_raw = raw.replace('version="2.0"', 'version="3.0"', 1)
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
            for n in names:
                zout.writestr(n, new_raw.encode() if n == opf else zin.read(n))
    return opf


# ---- 1. 3.0 无 nav → 降级 ----
tmp_src = Path("/tmp/t-3nonav.epub")
opf_path = make_epub3_no_nav(FOX, tmp_src)
with zipfile.ZipFile(tmp_src) as z:
    raw = z.read(opf_path).decode()
check("造样本: package 是 3.x", 'version="3.0"' in raw)
check("造样本: 没有 nav item", 'properties="nav"' not in raw)

sc = kpages.build_edition(tmp_src, "/tmp/t-3nonav-out.epub")
with zipfile.ZipFile("/tmp/t-3nonav-out.epub") as z:
    out = z.read(next(n for n in z.namelist() if n.lower().endswith(".opf"))).decode()
pkg = re.search(r'<package\b[^>]*>', out).group(0)
check("切片版 opf 降级成 2.0", 'version="2.0"' in pkg, pkg[:120])
check("sidecar 标记 downgraded", sc.get("opf_version_downgraded") is True)
check("切片版自检无坏链接", kpages.validate_edition("/tmp/t-3nonav-out.epub") == [])
check("切片版 is_edition", kpages.is_edition("/tmp/t-3nonav-out.epub"))

# ---- 2. 有 nav 的书不该被降级 ----
sc2 = kpages.build_edition(FOX, "/tmp/t-fox-out.epub")
check("正常书不降级", sc2.get("opf_version_downgraded") is False)

# ---- 3. 图片型 → image_book_reason 命中 ----
reason = kpages.image_book_reason(kpages.load_epub(TOU))
check("图片型 epub 被识别", bool(reason), reason)

# ---- 4. prep 的对外行为 (不碰库: 库外路径直接 skip) ----
r = epubprep.prep_download("/tmp/t-fox-out.epub")
check("库外路径 → skipped", r.get("state") == "skipped", r.get("why"))

r = epubprep.prep_download(TOU, log=lambda m: None)
check("藤井 (多卷/图片) → skipped", r.get("state") == "skipped", r.get("why"))

print("\n%d/%d" % (sum(OK), len(OK)))
sys.exit(0 if all(OK) else 1)
