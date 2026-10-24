import os, re, sys

ROOT = '/vol2/1001/zhangyue-bridge/apk/jadx'
PATS = {
    'api_path': re.compile(r'["\'](/[a-z0-9/_-]*(?:process|progress|readpos|bookmark|sync)[a-z0-9/_-]*)["\']', re.I),
    'method':   re.compile(r'\b(get|set|update|upload|push|sync|report|post)(\w{0,20})(BookProcess|ReadProcess|BookProgress|ReadProgress|ReadingProcess|ProcessInfo)\w*', re.I),
    'classname': re.compile(r'class\s+(\w*(?:BookProcess|ReadProcess|BookProgress|ReadingProgress|ReadRecord|BookRecord)\w*)'),
    'const':    re.compile(r'"([^"]*(?:book_id|bookId|section_id|sectionId|chapter_id|chapterId|readPos|read_pos|lastRead|progress)[^"]*)"'),
}

hits = {k: {} for k in PATS}
nfile = 0
for dirpath, _, files in os.walk(ROOT):
    for fn in files:
        if not fn.endswith('.java'):
            continue
        nfile += 1
        p = os.path.join(dirpath, fn)
        try:
            t = open(p, encoding='utf8', errors='ignore').read()
        except Exception:
            continue
        for k, rx in PATS.items():
            for m in rx.finditer(t):
                val = m.group(1)
                hits[k].setdefault(val, set()).add(os.path.relpath(p, ROOT))

print('scanned %d java files\n' % nfile)
for k in ('api_path', 'classname', 'method', 'const'):
    d = hits[k]
    print('===== %s  (%d distinct)' % (k, len(d)))
    for v, files in sorted(d.items(), key=lambda x: -len(x[1]))[:40]:
        print('  %-62s %s' % (v[:62], list(files)[0][:70]))
    print()