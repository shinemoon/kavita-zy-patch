"""极简 QR 编码器 (纯 stdlib, 无依赖).

只做我们要的: 字节模式 + EC level L + 最高版本裁剪。
输入短 (web_login: + 32 hex = 43 字节), 版本 3-4 足够。

实现参考 ISO/IEC 18004。仅供内部登录二维码用, 不追求通用性。
"""
from __future__ import annotations

# ---- GF(256) 算术 (QR 用的本原多项式 0x11d) ----------------------------

_EXP = [0] * 512
_LOG = [0] * 256


def _init_tables() -> None:
    x = 1
    for i in range(255):
        _EXP[i] = x
        _LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        _EXP[i] = _EXP[i - 255]


_init_tables()


def _mul(a: int, b: int) -> int:
    return 0 if a == 0 or b == 0 else _EXP[_LOG[a] + _LOG[b]]


# ---- 版本表 ----------------------------------------------------------
# (版本, 总码字数, ec 每块码字, 组数[ (ec块数, 数据块码字数), ... ])
# 数据取自 ISO/IEC 18004 表 13-22, 已用 segno.consts.ECC[ver][level] 逐版本核对。
# EC level 映射 (2 bit): L=0b01, M=0b00, Q=0b11, H=0b10
_TABLES = {
    "L": {
        1: (26, 7, [(1, 19)]), 2: (44, 10, [(1, 34)]), 3: (70, 15, [(1, 55)]),
        4: (100, 20, [(1, 80)]), 5: (134, 26, [(1, 108)]), 6: (172, 18, [(2, 68)]),
        7: (196, 20, [(2, 78)]), 8: (242, 24, [(2, 97)]), 9: (292, 30, [(2, 116)]),
        10: (346, 18, [(2, 68), (2, 69)]),
    },
    "M": {
        1: (26, 10, [(1, 16)]), 2: (44, 16, [(1, 28)]), 3: (70, 26, [(1, 44)]),
        4: (100, 18, [(2, 32)]), 5: (134, 24, [(2, 43)]), 6: (172, 16, [(4, 27)]),
        7: (196, 18, [(4, 31)]), 8: (242, 22, [(2, 38), (2, 39)]),
        9: (292, 22, [(3, 36), (2, 37)]), 10: (346, 26, [(4, 43), (1, 44)]),
    },
    "Q": {
        1: (26, 13, [(1, 13)]), 2: (44, 22, [(1, 22)]), 3: (70, 18, [(2, 17)]),
        4: (100, 26, [(2, 24)]), 5: (134, 18, [(2, 15), (2, 16)]),
        6: (172, 24, [(4, 19)]), 7: (196, 18, [(2, 14), (4, 15)]),
        8: (242, 22, [(4, 18), (2, 19)]), 9: (292, 20, [(4, 16), (4, 17)]),
        10: (346, 24, [(6, 19), (2, 20)]),
    },
    "H": {
        1: (26, 17, [(1, 9)]), 2: (44, 28, [(1, 16)]), 3: (70, 22, [(2, 13)]),
        4: (100, 16, [(4, 9)]), 5: (134, 22, [(2, 11), (2, 12)]),
        6: (172, 28, [(4, 15)]), 7: (196, 26, [(4, 13), (1, 14)]),
        8: (242, 26, [(2, 14), (4, 15)]), 9: (292, 24, [(4, 12), (4, 13)]),
        10: (346, 28, [(6, 15), (2, 16)]),
    },
}
_ECL_BITS = {"L": 0b01, "M": 0b00, "Q": 0b11, "H": 0b10}
# 掌阅 PC 站用的是 M 级 (2026-10-21 从其二维码反解: 42 字节 -> 版本 3)。
DEFAULT_ECL = "M"
MAX_VERSION = 10
# 每版余位 (ISO 表 1): 版本 2-6 余 7 位, 其余余 0 位
_REMAINDER = {1: 0, 2: 7, 3: 7, 4: 7, 5: 7, 6: 7, 7: 0, 8: 0, 9: 0, 10: 0}

def _table(ecl: str, version: int):
    """取该 EC 级别的版本行。ecl 传小写也认。"""
    t = _TABLES.get(str(ecl).upper())
    if t is None:
        raise ValueError(f"unknown EC level: {ecl!r} (want one of L/M/Q/H)")
    if version not in t:
        raise ValueError(f"unsupported version {version}")
    return t[version]


def _version_for(nbytes: int, ecl: str = DEFAULT_ECL) -> int:
    """按 EC 级别挑能装下 nbytes 的最小版本 (1..MAX_VERSION)."""
    t = _TABLES[str(ecl).upper()]
    for v in range(1, MAX_VERSION + 1):
        if v not in t:
            continue
        _, _, groups = t[v]
        data = sum(count * size for count, size in groups)
        # 模式(4) + 长度(8 bit, version<10) + 数据 + 结束符(最多4)
        need = 4 + 8 + nbytes * 8
        if need <= data * 8:
            return v
    raise ValueError(f"{nbytes} bytes too long for version <= {MAX_VERSION} (EC {ecl})")


def _rs_generator(deg: int) -> list[int]:
    g = [1]
    for i in range(deg):
        g = _poly_mul(g, [1, _EXP[i]])
    return g


def _poly_mul(a: list[int], b: list[int]) -> list[int]:
    out = [0] * (len(a) + len(b) - 1)
    for i, ai in enumerate(a):
        if ai:
            for j, bj in enumerate(b):
                out[i + j] ^= _mul(ai, bj)
    return out


def _rs_encode(data: list[int], ec_count: int) -> list[int]:
    gen = _rs_generator(ec_count)
    buf = list(data) + [0] * ec_count
    for i in range(len(data)):
        c = buf[i]
        if c:
            for j, g in enumerate(gen):
                buf[i + j] ^= _mul(g, c)
    return buf[len(data):]


def _make_codewords(data: bytes, version: int, ecl: str = DEFAULT_ECL) -> list[int]:
    total, ecpb, groups = _table(ecl, version)

    # bit stream: mode 0100 + 长度(8bit) + payload + terminator
    bits: list[int] = []

    def put(val: int, n: int) -> None:
        for i in range(n - 1, -1, -1):
            bits.append((val >> i) & 1)

    put(0b0100, 4)
    put(len(data), 8)
    for b in data:
        put(b, 8)
    cap = total * 8
    put(0, min(4, cap - len(bits)))
    while len(bits) % 8:
        bits.append(0)

    cw = [int("".join(str(b) for b in bits[i:i + 8]), 2) for i in range(0, len(bits), 8)]
    pad = [0xEC, 0x11]
    i = 0
    while len(cw) < total:
        cw.append(pad[i % 2])
        i += 1

    # 分块加纠错
    blocks: list[list[int]] = []
    ec_blocks: list[list[int]] = []
    off = 0
    for count, size in groups:
        for _ in range(count):
            blk = cw[off:off + size]
            off += size
            blocks.append(blk)
            ec_blocks.append(_rs_encode(blk, ecpb))

    out: list[int] = []
    maxd = max(len(b) for b in blocks)
    for i in range(maxd):
        for b in blocks:
            if i < len(b):
                out.append(b[i])
    for i in range(ecpb):
        for b in ec_blocks:
            out.append(b[i])
    return out


# ---- 模块矩阵 -----------------------------------------------------------

# 每版对齐图形中心坐标 (ISO/IEC 18004 Table E.1)。v1 没有对齐图形。
# 注意: 每个版本只列**中心坐标**, 网格是 centers × centers, 再扣掉与
# 三个 finder 重叠的三种组合。v7 起有两个中心值。
_ALIGN_CENTERS = {
    1: [],
    2: [6, 18],
    3: [6, 22],
    4: [6, 26],
    5: [6, 30],
    6: [6, 34],
    7: [6, 22, 38],
    8: [6, 24, 42],
    9: [6, 26, 46],
    10: [6, 28, 50],
}


def _matrix_positions(version: int):
    """(row, col) 顺序, 跟标准一致."""
    size = version * 4 + 17
    m = [[None] * size for _ in range(size)]
    res: list[tuple[int, int]] = []

    def finder(r, c):
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                rr, cc = r + dr, c + dc
                if not (0 <= rr < size and 0 <= cc < size):
                    continue
                if (0 <= dr <= 6 and (dc in (0, 6))) or (0 <= dc <= 6 and (dr in (0, 6))) \
                        or (2 <= dr <= 4 and 2 <= dc <= 4):
                    m[rr][cc] = 1
                else:
                    m[rr][cc] = 0

    # 定位图形 + 分隔符
    finder(0, 0); finder(0, size - 7); finder(size - 7, 0)

    # 定时图形
    for i in range(size):
        if m[6][i] is None:
            m[6][i] = 1 if i % 2 == 0 else 0
        if m[i][6] is None:
            m[i][6] = 1 if i % 2 == 0 else 0

    # 对齐图形 (v>=2)。跳过与三个 finder 重叠的三种组合。
    last = size - 7
    for r in _ALIGN_CENTERS[version]:
        for c in _ALIGN_CENTERS[version]:
            if (r, c) in ((6, 6), (6, last), (last, 6)):
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    # 标准 5x5: 四个角 + 正中心为黑, 其余白
                    #   #.#.#
                    #   .....
                    #   #.#.#
                    #   .....
                    #   #.#.#
                    # 即 (|dr|,|dc|) 都属于 {0,2} 时为黑。
                    if abs(dr) in (0, 2) and abs(dc) in (0, 2):
                        m[r + dr][c + dc] = 1
                    else:
                        m[r + dr][c + dc] = 0

    # 暗模块
    m[size - 8][8] = 1

    # 预留: 格式信息区 + 版本信息区 (v>=7)
    reserve: set[tuple[int, int]] = set()
    reserve |= {(8, c) for c in range(9)}          # 左上
    reserve |= {(r, 8) for r in range(9)}
    reserve |= {(8, c) for c in range(size - 8, size)}   # 右上
    reserve |= {(r, 8) for r in range(size - 8, size)}   # 左下
    if version >= 7:
        for i in range(6):
            for j in range(3):
                reserve |= {(size - 11 + j, i)}
                reserve |= {(i, size - 11 + j)}

    up = True
    col = size - 1
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(size - 1, -1, -1) if up else range(size)
        for row in rows:
            for c in (col, col - 1):
                if (row, c) not in reserve and m[row][c] is None:
                    m[row][c] = 0
                    res.append((row, c))
        up = not up
        col -= 2
    return m, res


def _apply_format(m, size: int, ecl_bits: int, mask: int) -> None:
    """写格式信息 (ISO/IEC 18004 Table C.1). ecl_bits: L=0b01, M=0b00, Q=0b11, H=0b10.

    data 的 bit0 = LSB。两处布局方向不同, 别弄反:
      左上角  行 8 是**倒序** (bit14 在 (8,0)), 列 8 是**正序** (bit0 在 (0,8))
      右上角  行 8 正序, 列 8 倒序
    """
    fmt = (ecl_bits << 3) | mask
    g15 = 0x537
    v = fmt << 10
    for i in range(4, -1, -1):
        if v & (1 << (i + 10)):
            v ^= g15 << i
    data = ((fmt << 10) | v) ^ 0x5412   # 0x5412 掩码

    def setbit(r, c, on):
        if 0 <= r < size and 0 <= c < size:
            m[r][c] = 1 if on else 0

    for i in range(15):
        on = (data >> i) & 1

        # --- 左上角 ---
        if i <= 5:
            setbit(i, 8, on)            # bit0..5  -> (0..5, 8)   倒序
        elif i == 6:
            setbit(7, 8, on)            # bit6     -> (7, 8)
        elif i == 7:
            setbit(8, 8, on)            # bit7     -> (8, 8)  固定 1
        elif i == 8:
            setbit(8, 7, on)            # bit8     -> (8, 7)
        else:
            setbit(8, 14 - i, on)       # bit9..14 -> (8, 5..0) 正序

        # --- 右上角 ---
        if i < 8:
            setbit(8, size - 1 - i, on)
        else:
            setbit(size - 15 + i, 8, on)


_MASK_FN = [
    lambda r, c: (r + c) % 2 == 0,
    lambda r, c: r % 2 == 0,
    lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
    lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
]


def _masked(m, res, fn, base):
    """对数据位套掩码 (只动原来是 0 的位)."""
    out = [row[:] for row in base]
    for (r, c) in res:
        if fn(r, c):
            out[r][c] ^= 1
    return out


def encode(text: str, ecl: str = DEFAULT_ECL) -> list[list[int]]:
    """编成模块矩阵 (1=黑)."""
    ecl = str(ecl).upper()
    data = text.encode("utf-8")
    version = _version_for(len(data), ecl)
    cw = _make_codewords(data, version, ecl)
    m, res = _matrix_positions(version)

    # 铺码字 (bit 顺序: 高位在前, 先沿 res 顺序)。
    # 末尾余位 (ISO 表 1) 必须补 0, 否则解码器读到垃圾。
    bits = []
    for v in cw:
        for i in range(7, -1, -1):
            bits.append((v >> i) & 1)
    bits += [0] * (len(res) - len(bits))

    base = [row[:] for row in m]
    for i, (r, c) in enumerate(res):
        base[r][c] = bits[i]

    # 选最优掩码 (八种罚分全扫)
    best, best_pen = None, None
    size = len(base)
    ecl_bits = _ECL_BITS[ecl]
    for mask in range(8):
        cand = _masked(base, res, _MASK_FN[mask], base)
        mm = [row[:] for row in cand]
        _apply_format(mm, size, ecl_bits, mask)
        pen = _penalty(mm)
        if best_pen is None or pen < best_pen:
            best, best_pen = mm, pen
    return best


def _penalty(m) -> int:
    """四类标准罚分 (简化, 够用)."""
    size = len(m)
    pen = 0
    # 规则1: 同色连续 >=7
    for line in list(m) + [[m[r][c] for r in range(size)] for c in range(size)]:
        run, prev = 1, line[0]
        for v in line[1:]:
            if v == prev:
                run += 1
            else:
                if run >= 7:
                    pen += 3 + (run - 7)
                run, prev = 1, v
        if run >= 7:
            pen += 3 + (run - 7)
    # 规则2: 2x2 同色
    for r in range(size - 1):
        for c in range(size - 1):
            v = m[r][c]
            if v == m[r][c + 1] == m[r + 1][c] == m[r + 1][c + 1]:
                pen += 3
    # 规则3: 1:1:3:1:1 模式
    pat_a = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0]
    pat_b = list(reversed(pat_a))
    for line in list(m) + [[m[r][c] for r in range(size)] for c in range(size)]:
        s = "".join(str(v) for v in line)
        pen += 10 * s.count("10111010000") + 10 * s.count("00001011101")
    return pen


def to_svg(text: str, *, scale: int = 6, border: int = 4,
           ecl: str = DEFAULT_ECL) -> str:
    """编成 SVG (可直接塞进 HTML, 无外部依赖)."""
    m = encode(text, ecl)
    size = len(m) + border * 2
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size*scale} {size*scale}" '
             f'shape-rendering="crispEdges" width="{size*scale}" height="{size*scale}">',
             f'<rect width="100%" height="100%" fill="#fff"/>']
    for r, row in enumerate(m):
        c = 0
        while c < len(row):
            if row[c]:
                c2 = c
                while c2 < len(row) and row[c2]:
                    c2 += 1
                parts.append(
                    f'<rect x="{(c+border)*scale}" y="{(r+border)*scale}" '
                    f'width="{(c2-c)*scale}" height="{scale}" fill="#000"/>')
                c = c2
            else:
                c += 1
    parts.append("</svg>")
    return "".join(parts)


if __name__ == "__main__":
    import sys
    t = sys.argv[1] if len(sys.argv) > 1 else "web_login:demo"
    m = encode(t)
    print(f"version={(len(m)-17)//4} size={len(m)}")
    for row in m:
        print("".join("██" if v else "  " for v in row))