"""迁移前核对: 小写身份的正文 (原书切) vs Kavita 注解里的 SelectedText。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sync import mapping, zy_notes  # noqa: E402


def main() -> int:
    bad = 0
    for link in mapping.all_links():
        old = link.zy_book_id[:32].lower()
        epub = zy_notes._orig_epub(link)
        if not epub:
            print(f"[{link.kavita_series_id}] no epub")
            continue
        try:
            v = zy_notes.data_ver(old)
        except Exception as e:  # noqa: BLE001
            print(f"[{link.kavita_series_id}] data_ver err {e}")
            continue
        notes = v.get("booknotesVerList") or []
        ann = {zy_notes.norm_key(r["zy_key"]): r for r in zy_notes.links_for_series(link.kavita_series_id, "note")}
        ann_text = {int(a.get("Id") or 0): (a.get("SelectedText") or "") for a in zy_notes.kavita_annotations(link.kavita_series_id)}
        print(f"[{link.kavita_series_id}] {link.zy_book_name}: cloud notes={len(notes)}")
        for n in notes:
            k = zy_notes.norm_key(str(n.get("uniquecheck")))
            poses = zy_notes._split_positions(k)
            if len(poses) < 2:
                print("   !! no poses", k[:60])
                bad += 1
                continue
            txt = zy_notes.text_of_span(epub, poses[0], poses[1])
            row = ann.get(k)
            kv = ann_text.get(int(row["kavita_annotation_id"] or 0), "") if row else "<no link>"
            ok = (txt.strip() == kv.strip())
            if not ok:
                bad += 1
                print(f"   MISMATCH {k[-40:]}\n     原书: {txt[:50]!r}\n     注解: {kv[:50]!r}")
    print("mismatch:", bad)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
