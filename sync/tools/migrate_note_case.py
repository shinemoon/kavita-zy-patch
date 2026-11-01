"""把小写身份的笔记/书签并入大写身份 (2026-11-01 统一大写)。

云端对 bookid 大小写敏感, 同一本书的小写/大写是**两个独立身份**, App 的
「笔记本」列表会显示成两条同名书 (实测剑来: 小写 20 条 / 大写 1 条)。
掌阅自己的本地书身份是 `core.getFileMD5(f).toUpperCase()` (of/a.java:1670),
所以统一大写。App 有时仍会写小写, 因此 `pull` 每次也会自动融合一次。

用法:
    python3 tools/migrate_note_case.py --dry-run
    python3 tools/migrate_note_case.py
    python3 tools/migrate_note_case.py --delete-old
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sync import mapping, zy_notes  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", type=int, action="append", help="只处理这些 Kavita 系列")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--delete-old", action="store_true", help="迁完把旧的小写条目删掉")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    links = [l for l in mapping.all_links()
             if not a.series or l.kavita_series_id in a.series]
    reps = []
    for link in links:
        r = zy_notes.consolidate_case(link, dry_run=a.dry_run, delete_old=a.delete_old)
        r.update({"series": link.kavita_series_id, "book": link.zy_book_name,
                  "old": link.zy_book_id[:32].lower(), "new": zy_notes.kb32(link.zy_book_id)})
        reps.append(r)
    if a.json:
        print(json.dumps(reps, ensure_ascii=False, indent=2))
        return 0
    for r in reps:
        head = f"[{r['series']}] {r.get('book')}: {r['old']} -> {r['new']}"
        if r.get("skip"):
            print(f"{head}  skip ({r['skip']})")
            continue
        print(head)
        print(f"    小写: notes={r.get('old_notes')} marks={r.get('old_marks')}"
              f"  并入大写: notes={r.get('merged')} marks={r.get('merged_marks')}"
              + ("  [dry-run]" if r.get("dry_run") else "")
              + (f"  删除旧: {r.get('deleted')}" if r.get("deleted") else ""))
        if r.get("error"):
            print(f"    ERROR {r['error']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
