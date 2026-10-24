import zipfile, os, glob, re

for f in sorted(glob.glob('/vol2/1001/zhangyue-bridge/books/*.epub')):
    try:
        z = zipfile.ZipFile(f)
        n = z.namelist()
        opf = [x for x in n if x.endswith('.opf')]
        d = z.read(opf[0]).decode('utf8', errors='ignore') if opf else ''
        title = re.findall(r'<dc:title[^>]*>(.*?)</dc:title>', d)[:1]
        lang = re.findall(r'<dc:language[^>]*>(.*?)</dc:language>', d)[:1]
        cre = re.findall(r'<dc:creator[^>]*>(.*?)</dc:creator>', d)[:1]
        print(os.path.basename(f))
        print('  entries=%d  title=%s  lang=%s  creator=%s' % (len(n), title, lang, cre))
        print('  sample=%s' % n[:4])
    except Exception as e:
        print(os.path.basename(f), 'ERR', e)