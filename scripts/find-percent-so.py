import os, subprocess, sys

D = '/vol2/1001/zhangyue-bridge/apk/sos/lib/arm64-v8a'
KEYS = [b'PercentByPosition', b'PositionChapIndex', b'comparePosition',
        b'setEnableSerialFullProgress', b'FullProgress']

hits = {}
for fn in sorted(os.listdir(D)):
    if not fn.endswith('.so'):
        continue
    p = os.path.join(D, fn)
    try:
        out = subprocess.run(['strings', '-a', p], capture_output=True).stdout
    except Exception:
        continue
    for k in KEYS:
        c = out.count(k)
        if c:
            hits.setdefault(fn, {})[k.decode()] = c

for fn, d in hits.items():
    print(fn, d)

print('--- scan done, files:', len(os.listdir(D)))