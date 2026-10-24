import re
p = '/vol2/1001/zhangyue-bridge/apk/jadx/sources/com/zhangyue/iReader/app/URL.java'
t = open(p, encoding='utf8', errors='ignore').read()
print('bytes:', len(t))
lines = t.split('\n')
for i, ln in enumerate(lines):
    if re.search(r'(READ_RECORD|PROCESS|read_record|process)', ln, re.I):
        print('%5d | %s' % (i + 1, ln.strip()[:170]))