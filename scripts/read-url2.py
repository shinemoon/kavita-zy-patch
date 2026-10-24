import re
p = '/vol2/1001/zhangyue-bridge/apk/jadx/sources/com/zhangyue/iReader/app/URL.java'
t = open(p, encoding='utf8', errors='ignore').read()
lines = t.split('\n')
print('===== URL_BASE_* assignments =====')
for i, ln in enumerate(lines):
    if re.search(r'URL_BASE\w*\s*=', ln):
        print('%5d | %s' % (i + 1, ln.strip()[:200]))

print('\n===== storage2 / safe neighbours =====')
for i, ln in enumerate(lines):
    if re.search(r'storage2', ln):
        print('%5d | %s' % (i + 1, ln.strip()[:200]))