import os, re

ROOT = '/vol2/1001/zhangyue-bridge/apk/jadx/sources'
KEYS = ['ReadProcessPost', 'ReadProcessBean', 'readPosition', 'readingSource', 'SOURCE_WEB']
files = []
for dp, _, fs in os.walk(ROOT):
    for fn in fs:
        if fn.endswith('.java'):
            files.append(os.path.join(dp, fn))

for key in KEYS:
    print('########## %s' % key)
    n = 0
    for p in files:
        t = open(p, encoding='utf8', errors='ignore').read()
        if key in t:
            n += 1
            if n <= 14:
                print('  ' + os.path.relpath(p, ROOT))
    print('  TOTAL %d files\n' % n)