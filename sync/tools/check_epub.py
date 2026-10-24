import sys, zipfile, re, posixpath
from xml.etree import ElementTree as ET

path = sys.argv[1]
z = zipfile.ZipFile(path)
names = set(z.namelist())
print('entries', len(names), 'first', z.namelist()[0], 'mimetype stored', z.getinfo('mimetype').compress_type == 0)
bad = []
for n in z.namelist():
    if n.lower().endswith(('.xhtml', '.html', '.opf', '.ncx')):
        try:
            ET.fromstring(z.read(n))
        except Exception as e:
            bad.append((n, str(e)[:80]))
print('bad xml', len(bad), bad[:5])
opf = [n for n in z.namelist() if n.lower().endswith('.opf')][0]
raw = z.read(opf).decode()
opf_dir = opf.rsplit('/', 1)[0] + '/' if '/' in opf else ''
spine = re.findall(r'<itemref[^>]*idref="([^"]+)"', raw)
items = dict(re.findall(r'<item id="([^"]+)" href="([^"]+)"', raw))
missing = [i for i in spine if (opf_dir + items.get(i, '')) not in names]
print('spine', len(spine), 'missing', len(missing), missing[:3])
bad_links = 0
sample = []
for n in z.namelist():
    if not n.lower().endswith(('.xhtml', '.html')):
        continue
    t = z.read(n).decode('utf-8', 'replace')
    for m in re.finditer(r'(?:href|src|xlink:href)="([^"#:]+)(?:#[^"]*)?"', t):
        tgt = m.group(1)
        if tgt.startswith('data:'):
            continue
        full = posixpath.normpath(n.rsplit('/', 1)[0] + '/' + tgt)
        if full not in names:
            bad_links += 1
            if len(sample) < 5:
                sample.append((n, tgt))
print('bad links', bad_links, sample)
# ncx/nav 里的链接也查
for n in z.namelist():
    if n.lower().endswith(('.ncx',)) or 'nav' in n.lower().split('/')[-1]:
        if not n.lower().endswith(('.ncx', '.xhtml')):
            continue
        t = z.read(n).decode('utf-8', 'replace')
        for m in re.finditer(r'src="([^"#:]+)(?:#[^"]*)?"', t):
            full = posixpath.normpath(n.rsplit('/', 1)[0] + '/' + m.group(1))
            if full not in names:
                print('  ncx/nav bad', n, m.group(1))
