import os, subprocess, re

D = '/vol2/1001/zhangyue-bridge/apk/sos_full/lib/arm64-v8a'
PAT = re.compile(rb'(percent|Percent|chapIndex|ChapIndex|curPositon|fullProgress|FullProgress|readPos|serialProgress)', re.I)

rows = []
for fn in sorted(os.listdir(D)):
    if not fn.endswith('.so'):
        continue
    p = os.path.join(D, fn)
    try:
        out = subprocess.run(['strings', '-a', '-n', '5', p], capture_output=True).stdout
    except Exception:
        continue
    hits = set()
    for line in out.splitlines():
        if PAT.search(line):
            s = line.decode('utf8', 'ignore').strip()
            if 4 < len(s) < 80:
                hits.add(s)
    if hits:
        rows.append((fn, os.path.getsize(p), sorted(hits)))

for fn, sz, hits in rows:
    print('=== %s  (%d KB)  %d hits' % (fn, sz // 1024, len(hits)))
    for h in hits[:60]:
        print('    ' + h)
    print()

print('--- files with hits:', len(rows))