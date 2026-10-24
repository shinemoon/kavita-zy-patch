import re, glob

KW = ['.apk', 'android', 'downLoad', 'download_url', 'ZhangYueJS', 'getBookProcess', 'readProgress', 'uploadProgress']
for f in sorted(glob.glob('/tmp/zy-js-*.js')):
    t = open(f, encoding='utf8', errors='ignore').read()
    print('===== ' + f.split('/')[-1])
    for kw in KW:
        idxs = [m.start() for m in re.finditer(re.escape(kw), t)]
        if not idxs:
            continue
        print('  %-16s x%d' % (kw, len(idxs)))
        for i in idxs[:3]:
            print('     ...' + t[max(0, i-150):i+170].replace('\n', ' ') + '...')