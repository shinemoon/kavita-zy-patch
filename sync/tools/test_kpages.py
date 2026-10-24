import sys, zipfile, json, re, time
sys.path.insert(0, '/vol2/1001/zhangyue-bridge/sync')
from sync import kpages
from xml.etree import ElementTree as ET

src = sys.argv[1]
dst = sys.argv[2]
t0 = time.time()

def prog(i, n):
    if i % 50 == 0:
        print(' ...', i, '/', n, '%.1fs' % (time.time() - t0))

sc = kpages.build_edition(src, dst, progress=prog)
print('built %.1fs' % (time.time() - t0), 'pages', sc['total_pages'], 'src bytes', sc['src_total_bytes'])
print('sidecar pages[0..4]', sc['pages'][:5])
print('docs', [(d['idx'], d['path'].split('/')[-1], d['size'], d['page']) for d in sc['docs']])

# validate
z = zipfile.ZipFile(dst)
names = z.namelist()
print('entries', len(names), 'first', names[0], 'compress_type', z.getinfo('mimetype').compress_type)
bad = []
for n in names:
    if n.lower().endswith('.opf'):
        ET.fromstring(z.read(n))
        print('opf ok', n)
    if n.lower().endswith(('.xhtml', '.html')):
        try:
            ET.fromstring(z.read(n))
        except Exception as e:
            bad.append((n, str(e)[:120]))
print('bad xml', len(bad), bad[:5])

# opf check: spine count, href existence
opf = [n for n in names if n.lower().endswith('.opf')][0]
raw = z.read(opf).decode()
opf_dir = opf.rsplit('/', 1)[0] + '/' if '/' in opf else ''
spine = re.findall(r'<itemref[^>]*idref="([^"]+)"', raw)
items = dict(re.findall(r'<item\s+id="([^"]+)"\s+href="([^"]+)"', raw))
missing = [i for i in spine if (opf_dir + items.get(i, '')) not in names]
print('spine', len(spine), 'missing href', len(missing), missing[:3])

# link targets exist
bad_links = 0
for n in names:
    if not n.lower().endswith(('.xhtml', '.html')):
        continue
    t = z.read(n).decode('utf-8', 'replace')
    for m in re.finditer(r'(?:href|src|xlink:href)="([^"#:]+)(?:#[^"]*)?"', t):
        tgt = m.group(1)
        from posixpath import normpath
        full = normpath(n.rsplit('/', 1)[0] + '/' + tgt)
        if full not in names:
            bad_links += 1
            if bad_links < 6:
                print('  bad link', n, '->', tgt)
print('bad links', bad_links)

# content alignment: page text vs original byte offset
p = int(sys.argv[3]) if len(sys.argv) > 3 else 48
s = sc['pages'][p]['s']
html = z.read(sc['page_files'][p]).decode('utf-8', 'replace')
txt = re.sub(r'<[^>]+>', '', re.sub(r'<style[\s\S]*?</style>', ' ', html))
txt = re.sub(r'\s+', '', txt)
zs = zipfile.ZipFile(src)
# original text at that global offset
acc = 0
for d in sc['docs']:
    if d['gs'] <= s < d['gs'] + d['size']:
        zraw = zs.read(d['path']).decode('utf-8', 'replace')
        local = s - d['gs']
        frag = re.sub(r'<[^>]+>', '', zraw[local:local + 4000])
        frag = re.sub(r'\s+', '', frag)
        print('page', p, 'doc', d['path'].split('/')[-1], 'local', local)
        print('  orig frag:', frag[:60])
        print('  page text:', txt[:60])
        print('  match prefix:', txt.startswith(frag[:30]))
        break
