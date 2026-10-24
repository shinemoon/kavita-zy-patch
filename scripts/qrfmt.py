"""对比格式信息区 / 版本信息区 — 定位位序错.

用法: python3 qrfmt.py
"""
import sys

sys.path.insert(0, "/vol2/1001/zhangyue-bridge/sync")
import segno  # noqa: E402

import qr  # noqa: E402

TEXT = "A" * 60
V = 5
MASK = 7
ECL_BITS = 0b11  # Q

n = V * 4 + 17
sm = [list(r) for r in segno.make(TEXT, error="Q", mode="byte", mask=MASK).matrix]

# --- 我们算的格式信息 ---
fmt = (ECL_BITS << 3) | MASK
g15 = 0x537
v = fmt << 10
for i in range(4, -1, -1):
    if v & (1 << (i + 10)):
        v ^= g15 << i
data = ((fmt << 10) | v) ^ 0x5412
print("format value = 0b%s (0x%04X)" % (format(data, "015b"), data))
print("bit14..bit0 =", [(data >> i) & 1 for i in range(14, -1, -1)])
print()

# --- 我们写入后的矩阵 ---
m, res = qr._matrix_positions(V)
cw = qr._make_codewords(TEXT.encode(), V)
bits = []
for x in cw:
    for i in range(7, -1, -1):
        bits.append((x >> i) & 1)
base = [r[:] for r in m]
for i, (r, c) in enumerate(res):
    base[r][c] = bits[i] if i < len(bits) else 0
ours = qr._masked(base, res, qr._MASK_FN[MASK], base)
qr._apply_format(ours, n, ECL_BITS, MASK)

print("左上格式区 (bits 0-5 -> (8,0..5), 6->(8,7), 7->(8,8), 8->(7,8), 9-14->(14-i,8)):")
print("pos      ours segno  expected")
coords = ([(8, i) for i in range(6)] + [(8, 7), (8, 8), (7, 8)]
          + [(14 - i, 8) for i in range(9, 15)])
for i, (r, c) in enumerate(coords):
    exp = (data >> i) & 1
    mark = "" if ours[r][c] == sm[r][c] else "  <-- DIFF"
    print("bit%-2d (%2d,%2d)   %d    %d       %d%s" % (i, r, c, ours[r][c], sm[r][c], exp, mark))

print()
print("右上格式区 (bits 0-7 -> (8, size-1-i), 8-14 -> (size-15+i, 8)):")
for i in range(15):
    r, c = (8, n - 1 - i) if i < 8 else (n - 15 + i, 8)
    exp = (data >> i) & 1
    mark = "" if ours[r][c] == sm[r][c] else "  <-- DIFF"
    print("bit%-2d (%2d,%2d)   %d    %d       %d%s" % (i, r, c, ours[r][c], sm[r][c], exp, mark))