"""严格反解左上格式区坐标映射.

做法: 对每个坐标 (r,c) 和每个 bit 候选 b, 遍历全部 (ecl, mask) 组合,
检查 segno[r][c] 是否 **恒等于** 该 bit。若恒成立则 b 是真映射。
"""
import sys

sys.path.insert(0, "/vol2/1001/zhangyue-bridge/sync")
import segno  # noqa: E402

CANDS = ([(8, i) for i in range(9)] + [(7, 8)] + [(i, 8) for i in range(8)])
CANDS = list(dict.fromkeys(CANDS))
ECLB = {"L": 0b01, "M": 0b00, "Q": 0b11, "H": 0b10}


def fmt_bits(ecl_bits: int, mask: int) -> int:
    fmt = (ecl_bits << 3) | mask
    g15 = 0x537
    v = fmt << 10
    for i in range(4, -1, -1):
        if v & (1 << (i + 10)):
            v ^= g15 << i
    return ((fmt << 10) | v) ^ 0x5412


samples = []
for ecl, eb in ECLB.items():
    for mask in range(8):
        sm = [list(r) for r in segno.make("A" * 30, error=ecl, mode="byte", mask=mask).matrix]
        samples.append((sm, fmt_bits(eb, mask)))

print("坐标 -> bit  (对全部 32 个 ecl/mask 组合恒成立)")
mapping = {}
for (r, c) in CANDS:
    if r == 6 or c == 6:
        continue
    good = [b for b in range(15)
            if all(sm[r][c] == ((data >> b) & 1) for sm, data in samples)]
    mapping[(r, c)] = good
    print("  (%2d,%2d)  %s" % (r, c, ("bit%d" % good[0]) if len(good) == 1 else f"AMBIG {good}"))

print()
uniq = {g[0]: rc for rc, g in mapping.items() if len(g) == 1}
print("按 bit 排出的坐标顺序:")
for b in range(15):
    print("  bit%-2d <- %s" % (b, uniq.get(b, "??")))