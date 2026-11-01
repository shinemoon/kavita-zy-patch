"""把云端笔记/书签从 **小写身份** 迁到 **大写身份** (2026-11-01 统一大写)。

背景: 掌阅云端同一本书可以并存两个身份 (`70776a64…` / `70776A64…`), App 的
「笔记本」列表就显示成两条同名书 (小写 20 条 / 大写 1 条)。掌阅自己的本地书身份
是 `core.getFileMD5(f).toUpperCase()` (of/a.java:1670), 所以统一到大写。

本工具做的事 (幂等, 可反复跑):
  1. 读小写身份的 `getUserDataVer` → 每条 uniquecheck / marktime
  2. 正文用原书按字节切 (`positions_payload`), 批注从 Kavita 注解补 (如果有关联)
  3. 以大写 bookid 上传一份副本 (已存在的 key 跳过)
  4. `--delete-old` 时再把小写那份删掉 (delMarkOrNote)

用法:
    python3 tools/migrate_note_case.py --dry-run
    python3 tools/migrate_note_case.py
    python3 tools/migrate_note_case.py --delete-old
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sync import mapping, zy_notes  # noqa: E402


def _old_key(zy_book_id: str) -> str:
    return (zy_book_id or "").strip().split("_")[0][:32].lower()


def migrate_one(link: mapping.BookLink, *, dry_run: bool, delete_old: bool) -> dict:
    old = _old_key(link.zy_book_id)
    new = zy_notes.kb32(link.zy_book_id)
    rep: dict = {"series": link.kavita_series_id, "book": link.zy_book_name,
                 "old": old, "new": new}
    if old == new:
        rep["skip"] = "already_upper"
        return rep
    epub = zy_notes._orig_epub(link)
    if not epub:
        rep["error"] = "no_orig_epub"
        return rep
    geo = zy_notes.geometry(link)
    if geo is None:
        rep["error"] = "no_geometry"
        return rep

    src = zy_notes.positions_payload(old, epub)          # 正文从原书字节切
    src_notes = src.get("booknotes") or []
    src_marks = src.get("bookmarks") or []
    rep["old_counts"] = {"notes": len(src_notes), "marks": len(src_marks)}

    dst = zy_notes.data_ver(new)
    dst_notes = {zy_notes.norm_key(str(i.get("uniquecheck"))) for i in (dst.get("booknotesVerList") or [])}
    dst_marks = {zy_notes.norm_key(str(i.get("uniquecheck"))) for i in (dst.get("bookmarksVerList") or [])}
    rep["new_had"] = {"notes": len(dst_notes), "marks": len(dst_marks)}

    # Kavita 注解 (拿批注正文 / 章名兜底)
    ann_by_key = {}
    for r in zy_notes.links_for_series(link.kavita_series_id):
        ann_by_key[(r["kind"], zy_notes.norm_key(r["zy_key"]))] = int(r["kavita_annotation_id"] or 0)
    ann_text: dict[int, tuple[str, str]] = {}
    try:
        for a in (zy_notes.kavita_annotations(link.kavita_series_id) or []):
            ann_text[int(a.get("Id") or 0)] = ((a.get("SelectedText") or "").strip(),
                                               (a.get("CommentPlainText") or "").strip())
    except Exception as e:                                # noqa: BLE001
        rep.setdefault("warn", []).append(f"annotations: {e}")

    out_notes, out_marks = [], []
    for n in src_notes:
        key_new = zy_notes.norm_key(n["uniquecheck"])
        if key_new in dst_notes:
            continue
        text = (n.get("summary") or "").strip()
        remark = ""
        ann_id = ann_by_key.get(("note", key_new), 0)
        if ann_id and ann_id in ann_text:
            a_text, a_cmt = ann_text[ann_id]
            remark = a_cmt
            if a_text:                                       # 原书切出来是空的话用注解的
                text = text or a_text
        pos_s, pos_e = zy_notes._split_positions(n["uniquecheck"])[:2]
        item = {"uniquecheck": key_new, "style": 0, "color": zy_notes.DEFAULT_COLOR,
                "summary": text, "remark": remark,
                "positionstart": pos_s, "positionend": pos_e,
                "positionstartl": 0, "positionendl": 0,
                "marktime": int(n.get("marktime") or 0)}
        g = zy_notes.pos_to_global(geo, pos_s)
        if g is not None:
            item.update(zy_notes._idea_fields(epub, geo, g))
        out_notes.append(item)

    for m in src_marks:
        key_new = zy_notes.norm_key(m["uniquecheck"])
        if key_new in dst_marks:
            continue
        pos = m.get("markpostion") or (_split_first(m["uniquecheck"]))
        g = zy_notes.pos_to_global(geo, pos)
        label = ""
        pct = 0
        if g is not None:
            try:
                label = zy_notes._chapter_label(geo, int(geo.locate(g)[0]))
            except Exception:                                # noqa: BLE001
                label = ""
            pct = round(g / max(1, geo.total_bytes), 8)
        out_marks.append({"uniquecheck": key_new, "marksummary": label,
                          "markpostion": pos, "markpercent": pct,
                          "markstyle": 0, "marktime": int(m.get("marktime") or 0)})

    rep["to_push"] = {"notes": len(out_notes), "marks": len(out_marks)}
    if dry_run:
        rep["dry_run"] = True
        rep["sample"] = (out_notes[0]["summary"][:40] if out_notes else "")
        return rep

    if out_notes or out_marks:
        nb = next((r for r in zy_notes.notebooks()
                   if zy_notes.kb32(str(r.get("bookid", ""))) == new), {})
        payload = {"bookbase": {
            "name": nb.get("name") or link.zy_book_name,
            "type": int(nb.get("type") or 5),
            "readpostion": nb.get("readpostion") or "",
            "readpercent": nb.get("readpercent") or 0,
            "bookid": new,
            "updatetime": int(time.time() * 1000),
            "deviceName": "KavitaBridge",
            "marknum": len(out_marks), "notenum": len(out_notes)}}
        if out_marks:
            payload["bookmarks"] = out_marks
        if out_notes:
            payload["booknotes"] = out_notes
        res = zy_notes.upload(payload)
        rep["upload"] = res.get("code")
        if res.get("code") not in (0, None):
            rep["error"] = f"upload: {res}"
            return rep

    # 复验
    chk = zy_notes.data_ver(new)
    rep["after"] = {"notes": len(chk.get("booknotesVerList") or []),
                    "marks": len(chk.get("bookmarksVerList") or [])}

    if delete_old:
        dels_notes = [zy_notes.norm_key(n["uniquecheck"]) for n in src_notes]
        dels_marks = [zy_notes.norm_key(m["uniquecheck"]) for m in src_marks]
        # 用**小写** bookid 删, 才落在旧身份上
        for keys, type_ in ((dels_notes, 2), (dels_marks, 1)):
            if not keys:
                continue
            old_keys = [k.lower() for k in keys]
            r = zy_notes.delete_items(old, old_keys, type_=type_)
            rep.setdefault("delete", []).append({"type": type_, "n": len(old_keys),
                                                 "code": r.get("code")})
        chk2 = zy_notes.data_ver(old)
        rep["old_after"] = {"notes": len(chk2.get("booknotesVerList") or []),
                            "marks": len(chk2.get("bookmarksVerList") or [])}
    return rep


def _split_first(uniquecheck: str) -> str:
    poses = zy_notes._split_positions(uniquecheck)
    return poses[0] if poses else ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", type=int, action="append", help="只处理这些 Kavita 系列")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--delete-old", action="store_true", help="迁完把旧的小写条目删掉")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    links = [l for l in mapping.all_links()
             if not a.series or l.kavita_series_id in a.series]
    reps = [migrate_one(l, dry_run=a.dry_run, delete_old=a.delete_old) for l in links]
    if a.json:
        print(json.dumps(reps, ensure_ascii=False, indent=2))
        return 0
    for r in reps:
        head = f"[{r['series']}] {r.get('book')} {r.get('old')} -> {r.get('new')}"
        if r.get("skip"):
            print(f"{head}: skip ({r['skip']})")
            continue
        if r.get("error"):
            print(f"{head}: ERROR {r['error']}")
            continue
        print(f"{head}")
        print(f"    旧身份: {r.get('old_counts')}  大写原有: {r.get('new_had')}"
              f"  本次上传: {r.get('to_push')}"
              + ("  [dry-run]" if r.get("dry_run") else ""))
        if "upload" in r:
            print(f"    upload code={r['upload']}  大写现在: {r.get('after')}")
        if "delete" in r:
            print(f"    删除旧身份: {r['delete']}  小写现在: {r.get('old_after')}")
        if r.get("warn"):
            print(f"    warn: {r['warn']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
