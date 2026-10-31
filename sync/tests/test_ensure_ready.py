"""epubprep.ensure_ready 的离线自检 (不碰真 Kavita / 不碰真库目录)。

为什么单独测它

    下载收尾是**多步、可半路失败**的流程 (归档 → 切片 → sidecar → 扫库等
    页数 → 建档 → pull)。2026-10-31 万古江河那次就是"库里已换成切片版, 但
    扫库那步抛异常 (API key 路径), 于是没有 sidecar、没有页数核对", 而
    prep_download 下次看到 is_edition 直接 return "edition" —— 中间态永远补
    不回来。

    ensure_ready 的契约是: **从磁盘现状出发, 幂等补齐缺的步骤**。所以测试的
    重点不是"跑步流程能跑通", 而是:

      1. 已经是切片版 + sidecar 在 → 不重建 (build_edition 不再调)
      2. 切片版但 sidecar 丢了 → 用原书确定性重建
      3. 新书 → 一路建起来, 该有的步骤都 ok
      4. 多卷系列 / 不在库目录 / 文件不存在 → 明确的跳过或错误, 不硬做
      5. pull="auto" 时, Kavita 已有进度就不动它
"""
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/vol2/1001/zhangyue-bridge/sync")
from sync import epubprep, kpages  # noqa: E402

FOX = "/vol2/1001/zhangyue-bridge/books/狐的故事.epub"

OK = []


def check(name, cond, extra=""):
    OK.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ((" — " + str(extra)) if extra else ""))


class Env:
    """把 epubprep 的所有落盘位置和 kavita 依赖换掉, 只留 kpages 真实逻辑。"""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.lib = tmp / "lib"
        self.orig = tmp / "orig"
        self.backup = tmp / "backup"
        self.kp = tmp / "kpages"
        for d in (self.lib, self.orig, self.backup, self.kp):
            d.mkdir(parents=True, exist_ok=True)
        self.calls = {"build": 0, "scan": 0, "pull": 0, "mapping": []}
        self.series = {          # filename -> series id
        }
        self.multi = {}          # series id -> [files...]
        self.pages = {}          # series id -> kavita pages
        self.pages_read = {}     # series id -> PagesRead

    # ---- 进入 / 退出 ----
    def __enter__(self):
        self._saved = {}
        for name, val in (("LIB", self.lib), ("ORIG", self.orig), ("BACKUP", self.backup)):
            self._saved[name] = getattr(epubprep, name)
            setattr(epubprep, name, val)
        self._saved["in_library"] = epubprep.in_library
        self._saved["_scan_library"] = epubprep._scan_library
        self._saved["_chapter_pages"] = epubprep._chapter_pages
        self._saved["_wait_pages"] = epubprep._wait_pages
        self._saved["_ensure_mapping"] = epubprep._ensure_mapping
        self._saved["_ensure_volume_mapping"] = epubprep._ensure_volume_mapping
        self._saved["_maybe_pull"] = epubprep._maybe_pull
        self._saved["resolve_series"] = epubprep.resolve_series
        self._saved["sidecar_path"] = kpages.sidecar_path
        self._saved["build_edition"] = kpages.build_edition
        self._saved["kdb"] = {
            "series_for_file": epubprep.kavita_db.series_for_file,
            "series_files": epubprep.kavita_db.series_files,
            "series_name": epubprep.kavita_db.series_name,
            "list_chapters": epubprep.kavita_db.list_chapters,
            "pages_read_by_series": epubprep.kavita_db.pages_read_by_series,
        }

        kpages.sidecar_path = lambda sid, state_dir=None: self.kp / f"{sid}.json"

        def _build(src, dst, **kw):
            self.calls["build"] += 1
            return self._saved["build_edition"](src, dst, **kw)

        kpages.build_edition = _build

        epubprep.in_library = lambda p: self.lib in Path(p).resolve().parents
        epubprep._scan_library = lambda: self.calls.__setitem__("scan", self.calls["scan"] + 1)
        epubprep._chapter_pages = lambda sid: self.pages.get(int(sid), 0)
        epubprep._wait_pages = lambda sid, want, wait=0: self.pages.get(int(sid), 0) == want
        epubprep.resolve_series = lambda target, **kw: self.series.get(Path(target).name)

        def _map(sid, *, zy_book_id="", book_name="", orig=None, log=print):
            self.calls["mapping"].append(("series", int(sid)))
            return "created" if zy_book_id else "no_zy_id"

        def _vmap(sid, target, *, zy_book_id="", book_name="", log=print):
            self.calls["mapping"].append(("volume", int(sid)))
            return "created" if zy_book_id else "no_zy_id"

        epubprep._ensure_mapping = _map
        epubprep._ensure_volume_mapping = _vmap

        def _pull(res, sid, pull, say):
            self.calls["pull"] += 1
            res.setdefault("steps", []).append({"name": "pull", "ok": True,
                                                "detail": f"stub pull={pull}"})

        epubprep._maybe_pull = _pull

        kdb = epubprep.kavita_db
        kdb.series_for_file = lambda name: self.series.get(name)
        kdb.series_files = lambda sid: self.multi.get(int(sid), [])
        kdb.series_name = lambda sid: f"series{sid}"
        kdb.list_chapters = lambda sid: [{"ChId": 1, "ChPages": self.pages.get(int(sid), 0)}]
        kdb.pages_read_by_series = lambda user_id=None: dict(self.pages_read)
        return self

    def __exit__(self, *exc):
        for name in ("LIB", "ORIG", "BACKUP"):
            setattr(epubprep, name, self._saved[name])
        for name in ("in_library", "_scan_library", "_chapter_pages", "_wait_pages",
                     "_ensure_mapping", "_ensure_volume_mapping", "_maybe_pull",
                     "resolve_series"):
            setattr(epubprep, name, self._saved[name])
        kpages.sidecar_path = self._saved["sidecar_path"]
        kpages.build_edition = self._saved["build_edition"]
        for k, v in self._saved["kdb"].items():
            setattr(epubprep.kavita_db, k, v)
        return False

    # ---- 工具 ----
    def put(self, name="book.epub", *, edition=False) -> Path:
        dst = self.lib / name
        shutil.copy2(FOX, dst)
        if edition:
            # 先用真实 build 生成切片版 (绕过计数, 这是"上一步已经做过"的模拟)
            tmp = dst.with_suffix(".tmp.epub")
            sc = self._saved["build_edition"](self.orig / name if (self.orig / name).exists()
                                             else dst, tmp)
            shutil.move(str(tmp), str(dst))
            return dst, sc
        return dst


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="zy-ensure-"))

    # ---- 1. 新书: 一路建起来 ----
    with Env(tmp / "a") as env:
        tgt = env.put("新书.epub")
        env.series["新书.epub"] = 7
        env.pages[7] = 100          # 扫库后"Kavita 报 100 页"
        env.pages_read[7] = 0
        r = epubprep.ensure_ready(tgt, book_name="新书", zy_book_id="ABC", pull="auto")
        names = {s["name"]: s for s in r["steps"]}
        check("新书 state=ok", r["state"] == "ok", r)
        check("新书 建了切片版", env.calls["build"] == 1)
        check("新书 归档了原书", (env.orig / "新书.epub").exists())
        check("新书 sidecar 落盘", (env.kp / "7.json").exists())
        check("新书 steps 全 ok", all(s["ok"] for s in r["steps"]),
              [s for s in r["steps"] if not s["ok"]])
        check("新书 建了映射", env.calls["mapping"] == [("series", 7)])
        check("新书 跑了 pull", env.calls["pull"] == 1)
        check("新书 pages step 对上", names["pages"]["detail"] == "Kavita 100 页 / 期望 100",
              names["pages"]["detail"])

    # ---- 2. 再跑一次: 幂等, 不重建 ----
    with Env(tmp / "a") as env:
        # 复用上一段的状态: 文件已是切片版, sidecar 在
        tgt = env.lib / "新书.epub"
        check("前置: 文件已是切片版", kpages.is_edition(tgt))
        sc = kpages.load(7)
        check("前置: sidecar 有 100 页", sc and len(sc["pages"]) == 100, sc and len(sc["pages"]))
        env.series["新书.epub"] = 7
        env.pages[7] = 100
        env.pages_read[7] = 0
        r = epubprep.ensure_ready(tgt, book_name="新书", zy_book_id="ABC", pull="auto")
        check("重跑 state=ok", r["state"] == "ok", r)
        check("重跑 没重建切片版", env.calls["build"] == 0, env.calls["build"])
        check("重跑 edition step 说已是切片版",
              "已是切片版" in [s for s in r["steps"] if s["name"] == "edition"][0]["detail"])

    # ---- 3. 切片版但 sidecar 丢了 → 从原书重建 ----
    with Env(tmp / "a") as env:
        for f in env.kp.glob("*.json"):
            f.unlink()
        tgt = env.lib / "新书.epub"
        env.series["新书.epub"] = 7
        env.pages[7] = 100
        r = epubprep.ensure_ready(tgt, book_name="新书", zy_book_id="ABC", pull="auto")
        check("丢 sidecar 后重建了切片版", env.calls["build"] == 1, env.calls["build"])
        check("丢 sidecar 后 state=ok", r["state"] == "ok", r)
        check("丢 sidecar 后 sidecar 回来了", (env.kp / "7.json").exists())

    # ---- 4. 切片版 + 原书也没了 → 明确报错, 不瞎修 ----
    with Env(tmp / "b") as env:
        tgt = env.lib / "无原书.epub"
        shutil.copy2(FOX, tgt)
        sc = kpages.build_edition(tgt, tgt.with_suffix(".t.epub"))
        shutil.move(str(tgt.with_suffix(".t.epub")), str(tgt))
        # 不写原书归档, 不写 sidecar
        env.series["无原书.epub"] = 9
        env.pages[9] = len(sc["pages"])
        r = epubprep.ensure_ready(tgt, book_name="无原书", zy_book_id="X")
        check("缺原书 state=error", r["state"] == "error", r.get("error"))
        check("缺原书 说明是原书缺失", "原书" in (r.get("error") or ""), r.get("error"))

    # ---- 5. 不在库目录 → 跳过 ----
    with Env(tmp / "c") as env:
        outside = tmp / "c" / "outside.epub"
        outside.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(FOX, outside)
        r = epubprep.ensure_ready(outside)
        check("库外 state=skipped", r["state"] == "skipped", r)
        check("库外 明确说了原因", "库目录" in (r.get("why") or ""), r.get("why"))

    # ---- 6. 文件不存在 ----
    with Env(tmp / "d") as env:
        r = epubprep.ensure_ready(env.lib / "nope.epub")
        check("缺文件 state=error", r["state"] == "error", r)
        check("缺文件 steps 记了 file 失败",
              [s for s in r["steps"] if s["name"] == "file"][0]["ok"] is False)

    # ---- 7. 多卷系列: 不切片, 建卷级映射 ----
    with Env(tmp / "e") as env:
        tgt = env.put("卷01.epub")
        env.series["卷01.epub"] = 29
        env.multi[29] = ["a.epub", "b.epub", "c.epub"]
        env.pages[29] = 10
        r = epubprep.ensure_ready(tgt, book_name="卷01", zy_book_id="V1", pull="auto")
        check("多卷 state=skipped", r["state"] == "skipped", r["state"])
        check("多卷 没切片", env.calls["build"] == 0)
        check("多卷 建了卷级映射", env.calls["mapping"] == [("volume", 29)], env.calls["mapping"])

    # ---- 8. pull="auto" 且已有进度 → 真逻辑不 pull ----
    with Env(tmp / "f") as env:
        # 这段要测 _maybe_pull 的真身, 所以把 stub 换回去
        epubprep._maybe_pull = env._saved["_maybe_pull"]
        tgt = env.put("读过.epub")
        env.series["读过.epub"] = 11
        env.pages[11] = 50
        env.pages_read[11] = 7
        r = epubprep.ensure_ready(tgt, book_name="读过", zy_book_id="Z", pull="auto")
        pull_step = [s for s in r["steps"] if s["name"] == "pull"][0]
        check("已有进度 → pull 跳过", "跳过" in pull_step["detail"], pull_step["detail"])
        check("已有进度 → pull step 仍算 ok", pull_step["ok"] is True)

    shutil.rmtree(tmp, ignore_errors=True)
    bad = [i for i, v in enumerate(OK) if not v]
    print(f"\n{len(OK) - len(bad)}/{len(OK)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
