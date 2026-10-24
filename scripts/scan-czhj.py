import os, re

ROOTS = ['/vol2/1001/zhangyue-bridge/apk/jadx/sources/com/czhj',
         '/vol2/1001/zhangyue-bridge/apk/jadx/sources/com/zhangyue']
PATS = {
    'api_path': re.compile(r'"(/[a-zA-Z0-9/_.-]*(?:process|progress|readpos|bookmark|sync|record)[a-zA-Z0-9/_.-]*)"'),
    'method':   re.compile(r'\b(?:public|private|protected|static).{0,80}?\b(\w*(?:BookProcess|ReadProcess|BookProgress|ReadingProgress|ReadRecord|BookRecord|ProcessInfo)\w*)\s*\('),
    'classname': re.compile(r'(?:class|interface)\s+(\w*(?:BookProcess|ReadProcess|BookProgress|ReadingProgress|ReadRecord|BookRecord)\w*)'),
    'const':    re.compile(r'"([^"]*(?:book_id|section_id|chapter_id|readPos|read_pos|lastRead|last_read|progress_offset|chapterIndex|chapter_index)[^"]*)"'),
    'table':    re.compile(r'"(book_[a-z_]+|tb_[a-z_]+|t_[a-z_]+|[a-z_]*process[a-z_]*)"'),
}
hits = {k: {} for k in PATS}
nf = 0
for R in ROOTS:
    if not os.path.isdir(R):
        print('MISSING ' + R); continue
    for dp, _, fs_ in os.walk(R):
        for fn in fs_:
            if not fn.endswith('.java'):
                continue
            nf += 1
            p = os.path.join(dp, fn)
            t = open(p, encoding='utf8', errors='ignore').read()
            for k, rx in PATS.items():
                for m in rx.finditer(t):
                    hits[k].setdefault(m.group(1), set()).add(os.path.relpath(p, R))
print('scanned %d java files under com/czhj + com/zhangyue\n' % nf)
for k in ('api_path', 'classname', 'method', 'table', 'const'):
    d = hits[k]
    print('===== %s (%d)' % (k, len(d)))
    for v, fs_ in sorted(d.items(), key=lambda x: -len(x[1]))[:35]:
        print('  %-58s %s' % (v[:58], sorted(fs_)[0][:64]))
    print()