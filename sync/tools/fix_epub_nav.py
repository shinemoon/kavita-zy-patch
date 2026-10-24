#!/usr/bin/env python3
"""给缺 EPUB3 nav 文档的 epub 补一份 nav.xhtml（Kavita / VersOne.Epub 报
`Epub3NavException: NAV item not found in EPUB manifest` 时用）。

背景（2026-10-24）: 掌阅云书架里《路邊的藤井》三卷声明 `package version="3.0"`
但 manifest 里只有 EPUB2 的 NCX（`media-type="application/x-dtbncx+xml"`），
没有 `properties="nav"` 的条目。Kavita 用 VersOne.Epub 解析，见到 3.0 就强制要
nav → 抛异常 → 整本读不出来（书架里看不到 / 打开报错）。

修法: 从 NCX（没有就从 spine 顺序）生成一份合法 nav.xhtml，写进 opf 所在目录，
manifest 里加 `<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml"
properties="nav"/>`，zip 其余条目原样复制。

用法:
    python3 tools/fix_epub_nav.py <epub|目录> [...] [--dry-run] [--backup-dir DIR]
退出码: 0 = 全部 OK（含本来就正常而被跳过的），1 = 有一本失败。
"""
from __future__ import annotations

import argparse
import html
import os
import posixpath
import re
import shutil
import sys
import zipfile
from xml.sax.saxutils import escape

MIMETYPE = "application/epub+zip"


def _read_container(z: zipfile.ZipFile) -> str:
    for name in ("META-INF/container.xml",):
        try:
            text = z.read(name).decode("utf-8", "replace")
        except KeyError:
            continue
        m = re.search(r'full-path="([^"]+)"', text)
        if m:
            return m.group(1)
    raise RuntimeError("container.xml missing rootfile")


def _items(opf: str) -> dict[str, dict]:
    """id -> {href, media-type, properties}"""
    out = {}
    for m in re.finditer(r"<item\b[^>]*>", opf):
        tag = m.group(0)
        def attr(k):
            mm = re.search(k + r'\s*=\s*"([^"]*)"', tag)
            return mm.group(1) if mm else ""
        iid = attr("id")
        if iid:
            out[iid] = {"href": attr("href"), "media-type": attr("media-type"),
                        "properties": attr("properties"), "raw": tag}
    return out


def _spine_ids(opf: str) -> list[str]:
    m = re.search(r"<spine\b[^>]*>(.*?)</spine>", opf, re.S)
    if not m:
        return []
    return re.findall(r'<itemref\b[^>]*idref\s*=\s*"([^"]+)"', m.group(1))


def _titles_from_ncx(z: zipfile.ZipFile, ncx_href: str) -> dict[str, str]:
    """NCX -> {opf 相对 href: 标题}"""
    try:
        text = z.read(ncx_href).decode("utf-8", "replace")
    except KeyError:
        return {}
    base = posixpath.dirname(ncx_href)
    out = {}
    for label, src in re.findall(
            r"<navLabel>\s*<text>(.*?)</text>\s*</navLabel>\s*<content\s+src=\"([^\"]+)\"",
            text, re.S):
        href = posixpath.normpath(posixpath.join(base, src.split("#")[0]))
        title = html.unescape(re.sub(r"<[^>]+>", "", label)).strip()
        if href and title and href not in out:
            out[href] = title
    return out


def _titles_from_nav(z: zipfile.ZipFile, nav_href: str) -> dict[str, str]:
    try:
        text = z.read(nav_href).decode("utf-8", "replace")
    except KeyError:
        return {}
    base = posixpath.dirname(nav_href)
    out = {}
    for href, label in re.findall(r"<a\b[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", text, re.S):
        path = posixpath.normpath(posixpath.join(base, href.split("#")[0]))
        title = html.unescape(re.sub(r"<[^>]+>", "", label)).strip()
        if path and title and path not in out:
            out[path] = title
    return out


def _build_nav(title: str, entries: list[tuple[str, str]]) -> str:
    lis = "\n".join(
        '      <li><a href="%s">%s</a></li>' % (escape(href, {'"': "&quot;"}), escape(label))
        for href, label in entries)
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="zh">\n'
        '  <head>\n'
        '    <meta charset="utf-8"/>\n'
        f'    <title>{escape(title)}</title>\n'
        '  </head>\n'
        '  <body>\n'
        '    <nav epub:type="toc" id="toc">\n'
        f'      <h1>{escape(title)}</h1>\n'
        '      <ol>\n'
        f'{lis}\n'
        '      </ol>\n'
        '    </nav>\n'
        '  </body>\n'
        '</html>\n'
    )


def _rel(from_dir: str, to_path: str) -> str:
    if not from_dir:
        return to_path
    return posixpath.relpath(to_path, from_dir)


def needs_fix(path: str) -> tuple[bool, str]:
    with zipfile.ZipFile(path) as z:
        opf_path = _read_container(z)
        opf = z.read(opf_path).decode("utf-8", "replace")
    items = _items(opf)
    for it in items.values():
        if "nav" in (it["properties"] or "").split():
            return False, "已有 nav 条目"
    m = re.search(r'<package\b[^>]*version\s*=\s*"([^"]+)"', opf)
    ver = m.group(1) if m else "?"
    try:
        major = int(str(ver).split(".")[0])
    except ValueError:
        major = 3
    if major < 3:
        # EPUB2 走 NCX，Kavita/VersOne 不会要 nav；给 2.0 塞 EPUB3 的
        # properties="nav" 反而不合规，跳过。
        return False, f"package version={ver}（EPUB2，用 NCX，无需 nav）"
    return True, f"无 nav 条目 (package version={ver})"


def fix(path: str, dry_run: bool = False, backup_dir: str | None = None) -> dict:
    res = {"file": path, "ok": False, "action": "", "entries": 0, "note": ""}
    need, why = needs_fix(path)
    res["note"] = why
    if not need:
        res["ok"] = True
        res["action"] = "skip"
        return res
    if dry_run:
        res["ok"] = True
        res["action"] = "dry-run"
        return res

    with zipfile.ZipFile(path) as z:
        opf_path = _read_container(z)
        opf_dir = posixpath.dirname(opf_path)
        opf = z.read(opf_path).decode("utf-8", "replace")
        items = _items(opf)

        # 标题来源: 已有 nav > NCX > spine 顺序
        titles: dict[str, str] = {}
        nav_href = next((it["href"] for it in items.values()
                         if "nav" in (it["properties"] or "").split() and it["href"]), "")
        ncx_href = next((it["href"] for it in items.values()
                         if it["media-type"] == "application/x-dtbncx+xml"), "")
        if nav_href:
            titles = _titles_from_nav(z, nav_href)
        if not titles and ncx_href:
            titles = _titles_from_ncx(z, ncx_href)

        spine = [items[i]["href"] for i in _spine_ids(opf) if i in items and items[i]["href"]]
        if not spine:
            spine = [it["href"] for it in items.values()
                     if it["media-type"] in ("application/xhtml+xml", "text/html") and it["href"]]

        entries: list[tuple[str, str]] = []
        seen = set()
        for n, href in enumerate(spine, 1):
            full = posixpath.normpath(posixpath.join(opf_dir, href))
            if full in seen:
                continue
            seen.add(full)
            label = titles.get(full) or titles.get(href) or f"第 {n} 页"
            entries.append((_rel(opf_dir, full), label))
        if not entries:
            raise RuntimeError("spine 与 manifest 都拿不到内容条目")

        title = ""
        m = re.search(r"<dc:title[^>]*>(.*?)</dc:title>", opf, re.S)
        if m:
            title = html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
        title = title or posixpath.basename(path)

        nav_name = "nav.xhtml"
        existing = {name for name in z.namelist()}
        nav_zip_path = posixpath.normpath(posixpath.join(opf_dir, nav_name))
        k = 1
        while nav_zip_path in existing:
            k += 1
            nav_name = f"nav{k}.xhtml"
            nav_zip_path = posixpath.normpath(posixpath.join(opf_dir, nav_name))

        nav_xml = _build_nav(title, entries)

        new_opf = opf.replace(
            "</manifest>",
            '    <item id="nav" href="%s" media-type="application/xhtml+xml" properties="nav"/>\n'
            "  </manifest>" % nav_name, 1)
        if new_opf == opf:
            raise RuntimeError("opf 里找不到 </manifest>，无法插入 nav 条目")

        tmp = path + ".navfix.tmp"
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
            # mimetype 必须第一个且不压缩
            mi = zipfile.ZipInfo("mimetype")
            mi.compress_type = zipfile.ZIP_STORED
            out.writestr(mi, MIMETYPE)
            for info in z.infolist():
                if info.filename == "mimetype":
                    continue
                data = z.read(info.filename)
                if info.filename == opf_path:
                    data = new_opf.encode("utf-8")
                if info.filename == nav_zip_path:
                    continue
                out.writestr(info, data)
            ni = zipfile.ZipInfo(nav_zip_path, date_time=(1980, 1, 1, 0, 0, 0))
            ni.compress_type = zipfile.ZIP_DEFLATED
            out.writestr(ni, nav_xml.encode("utf-8"))

    if backup_dir:
        os.makedirs(backup_dir, exist_ok=True)
        shutil.copy2(path, os.path.join(backup_dir, os.path.basename(path)))
    os.replace(tmp, path)
    res["ok"] = True
    res["action"] = "fixed"
    res["entries"] = len(entries)
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="补 EPUB3 nav 文档")
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--backup-dir")
    a = ap.parse_args(argv)

    targets: list[str] = []
    for p in a.paths:
        if os.path.isdir(p):
            targets += [os.path.join(p, f) for f in sorted(os.listdir(p))
                        if f.lower().endswith(".epub")]
        else:
            targets.append(p)

    rc = 0
    for t in targets:
        try:
            r = fix(t, dry_run=a.dry_run, backup_dir=a.backup_dir)
            print(f"[{r['action']:7s}] {os.path.basename(t)}  {r['note']}"
                  + (f"  ({r['entries']} 条目)" if r["entries"] else ""))
        except Exception as e:  # noqa: BLE001
            rc = 1
            print(f"[FAIL   ] {os.path.basename(t)}  {type(e).__name__}: {e}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
