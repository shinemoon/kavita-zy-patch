import os, re, glob

for f in sorted(glob.glob('/tmp/zy-js-*.js')):
    t = open(f, encoding='utf8', errors='ignore').read()
    print('===== ' + os.path.basename(f) + ' (' + str(len(t)) + ' bytes)')
    for kw in ['getBatchProgress', 'BookID', 'book_progress', 'BookIds', 'bookIds']:
        idxs = [m.start() for m in re.finditer(re.escape(kw), t)]
        print('  %-18s x%d' % (kw, len(idxs)))
        for i in idxs[:4]:
            print('     ...' + t[max(0, i - 160):i + 220].replace('\n', ' ') + '...')