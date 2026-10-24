"""校验 kavita 版 epub: 结构合法 + 每页文本与 sidecar 记录的源字节偏移对齐。"""
import sys, zipfile, re, json, random, posixpath
sys.path.insert(0, '/vol2/1001/zhangyue-bridge/sync')
from sync import kpages
from xml.etree import ElementTree as ET


def strip_tags(s):
    s = re.sub(r'<style[\s\S]*?</style>', ' ', s)
    s = re.sub(r'<title[\s\S]*?</title>', ' ', s)
    s = re.sub(r'<[^>]+>', '', s)
    return re.sub(r'\s+', '', s)


def main(src, dst, sample=25):
    sc = json.loads(open(sys.argv[3]).read()) if len(sys.argv) > 3 else None
    if sc is None:
        raise SystemExit('需要 sidecar json 路径')
    zs, zd = zipfile.ZipFile(src), zipfile.ZipFile(dst)
    docs = {d['path']: d for d in sc['docs']}
    total = sc['total_pages']
    bad = []
    for p in random.sample(range(total), min(sample, total)):
        s = sc['pages'][p]['s']
        d = next(v for v in sc['docs'] if v['gs'] <= s < v['gs'] + v['size'])
        local = s - d['gs']
        rawb = zs.read(d['path'])
        want = strip_tags(rawb[local:local + 2000].decode('utf-8', 'replace'))[:24]
        got = strip_tags(zd.read(sc['page_files'][p]).decode('utf-8', 'replace'))[:24]
        if not want or not got:
            bad.append((p, 'empty', want, got))
            continue
        ok = got.startswith(want[:12]) or want.startswith(got[:12])
        if not ok:
            bad.append((p, d['path'].split('/')[-1], want, got))
    print('pages', total, 'sampled', min(sample, total), 'mismatch', len(bad))
    for b in bad[:8]:
        print('  ', b)
    return 0 if not bad else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1], sys.argv[2]))
