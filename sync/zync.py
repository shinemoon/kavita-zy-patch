"""CLI 入口. 用法:
    python3 zync.py <cmd> [args]
    ZY_AUTH_JSON=... python3 zync.py push

sub-commands:
    push    Kavita -> 掌阅
    pull    掌阅 -> Kavita
    status  查每本书两侧进度
    link    加映射 (kavita-series-id ↔ zy-book-id)
    links   列映射
    daemon  轮询 push + pull
"""
from __future__ import annotations
import argparse
import json
import sys

from sync import sync as core
from sync import mapping


def cmd_push(args):
    out = core.push_once(dry_run=args.dry_run)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if not args.strict or (out.get("failed", 0) == 0) else 1


def cmd_pull(args):
    out = core.pull_once(dry_run=args.dry_run)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if not args.strict or (out.get("failed", 0) == 0) else 1


def cmd_status(args):
    out = core.status()
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


def cmd_link(args):
    l = mapping.link(
        kavita_series_id=args.kavita_series_id,
        zy_book_id=args.zy_book_id,
        zy_book_name=args.zy_book_name,
        zy_anchor_chapter_id=args.anchor,
        kavita_chapter_count=args.kavita_chapter_count,
        note=args.note,
        kavita_chapter_id=args.kavita_chapter_id,
    )
    print(json.dumps(l.__dict__, ensure_ascii=False, indent=1))
    return 0


def cmd_link_list(args):
    out = [l.__dict__ for l in mapping.all_links()]
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


def cmd_daemon(args):
    core.daemon(poll_seconds=args.interval)
    return 0


def cmd_notes(args):
    from sync import zy_notes
    sid = args.series or None
    if args.action == "pull":
        out = zy_notes.pull(sid, dry_run=args.dry_run)
    elif args.action == "push":
        out = zy_notes.push(sid, dry_run=args.dry_run)
    else:
        out = zy_notes.sync(sid, dry_run=args.dry_run)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if not out.get("errors") else 1


def cmd_time(args):
    from datetime import date
    from sync import zy_time
    if args.action == "push":
        out = zy_time.push(date.fromisoformat(args.day), dry_run=args.dry_run)
    elif args.action == "pull":
        out = zy_time.pull(date.fromisoformat(args.start), date.fromisoformat(args.end),
                           dry_run=args.dry_run)
    else:
        out = zy_time.sync(date.fromisoformat(args.day), dry_run=args.dry_run)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if not out.get("errors") else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="zhangyue-bridge")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_push = sub.add_parser("push", help="Kavita -> 掌阅")
    p_push.add_argument("--dry-run", action="store_true")
    p_push.add_argument("--strict", action="store_true")
    p_push.set_defaults(func=cmd_push)

    p_pull = sub.add_parser("pull", help="掌阅 -> Kavita")
    p_pull.add_argument("--dry-run", action="store_true")
    p_pull.add_argument("--strict", action="store_true")
    p_pull.set_defaults(func=cmd_pull)

    p_st = sub.add_parser("status")
    p_st.set_defaults(func=cmd_status)

    p_lk = sub.add_parser("link", help="加一本映射")
    p_lk.add_argument("--kavita-series-id", type=int, required=True)
    p_lk.add_argument("--zy-book-id", required=True)
    p_lk.add_argument("--zy-book-name", required=True)
    p_lk.add_argument("--anchor", default=None)
    p_lk.add_argument("--kavita-chapter-count", type=int, default=0)
    p_lk.add_argument("--kavita-chapter-id", type=int, default=-1,
                      help="卷级映射: 该系列里第几号 Chapter/卷 (-1=整本)")
    p_lk.add_argument("--note", default="")
    p_lk.set_defaults(func=cmd_link)

    p_ll = sub.add_parser("links")
    p_ll.set_defaults(func=cmd_link_list)

    p_daemon = sub.add_parser("daemon")
    p_daemon.add_argument("--interval", type=int, default=30)
    p_daemon.set_defaults(func=cmd_daemon)

    p_nt = sub.add_parser("notes", help="划线/书签 双向同步")
    p_nt.add_argument("action", choices=("sync", "pull", "push"))
    p_nt.add_argument("--series", type=int, action="append", default=[],
                      help="只处理这些 Kavita series id (可多次)")
    p_nt.add_argument("--dry-run", action="store_true")
    p_nt.set_defaults(func=cmd_notes)

    p_tm = sub.add_parser("time", help="阅读时长 双向同步")
    p_tm.add_argument("action", choices=("sync", "pull", "push"))
    p_tm.add_argument("--day", default=None, help="push 用: YYYY-MM-DD (默认今天)")
    p_tm.add_argument("--start", default=None, help="pull 用: 起始日")
    p_tm.add_argument("--end", default=None, help="pull 用: 结束日")
    p_tm.add_argument("--dry-run", action="store_true")
    p_tm.set_defaults(func=cmd_time)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
