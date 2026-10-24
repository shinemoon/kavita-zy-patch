"""Pure-python strings extractor (no binutils dependency)."""
import os, re, sys

PRINTABLE = re.compile(rb'[\x20-\x7e]{4,}')

def extract(path, minlen=4):
    data = open(path, 'rb').read()
    return [m.group().decode('ascii', 'ignore') for m in PRINTABLE.finditer(data)]


def scan(d, pats, minlen=4, limit=40):
    rx = re.compile(pats, re.I)
    for fn in sorted(os.listdir(d)):
        if not fn.endswith('.so'):
            continue
        p = os.path.join(d, fn)
        try:
            ss = extract(p, minlen)
        except Exception as e:
            print('skip', fn, e)
            continue
        hits = []
        seen = set()
        for s in ss:
            if rx.search(s) and s not in seen and len(s) < 120:
                seen.add(s)
                hits.append(s)
        if hits:
            print('=== %s (%d KB) %d hits' % (fn, os.path.getsize(p) // 1024, len(hits)))
            for h in hits[:limit]:
                print('   ' + h)
            print()
    print('--- done')

if __name__ == '__main__':
    d = sys.argv[1]
    pat = sys.argv[2] if len(sys.argv) > 2 else r'percent|chapIndex|curPositon|FullProgress'
    scan(d, pat)