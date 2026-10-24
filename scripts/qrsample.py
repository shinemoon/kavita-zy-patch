"""从掌阅 PC 站的二维码 PNG 精确采样出模块矩阵.

用法: python3 qrsample.py <png> [期望尺寸]
输出: 采样矩阵 (0/1) 到 stdout, 同时打印与 segno 的 diff。
"""
import sys

from PIL import Image

import segno

FINDER = [
    "1111111",
    "1000001",
    "1011101",
    "1011101",
    "1011101",
    "1000001",
    "1111111",
]


def sample(img, size, border):
    g = img.convert("L")
    w, h = g.size
    total = size + border * 2
    sx, sy = w / total, h / total
    rows = []
    for r in range(size):
        line = ""
        for c in range(size):
            cx = min(w - 1, int((c + border + 0.5) * sx))
            cy = min(h - 1, int((r + border + 0.5) * sy))
            line += "1" if g.getpixel((cx, cy)) < 140 else "0"
        rows.append(line)
    return rows


def main():
    png = sys.argv[1]
    img = Image.open(png)
    print("png size:", img.size)
    text = sys.argv[2] if len(sys.argv) > 2 else None

    for border in (4, 2, 0):
        for size in range(21, 58, 4):
            rows = sample(img, size, border)
            if all(rows[r][:7] == FINDER[r] for r in range(7)):
                print(f"match: size={size} border={border}")
                if text:
                    for ecl in ("M", "L"):
                        try:
                            q = segno.make(text, error=ecl, mode="byte", version=(size - 17) // 4)
                        except Exception:
                            continue
                        d = sum(1 for r in range(size) for c in range(size)
                                if q.matrix[r][c] != int(rows[r][c]))
                        print(f"  segno ecl={ecl} v{(size - 17) // 4} mask={q.mask} diff={d}")
                else:
                    print("\n".join(rows))
                return 0
    print("no finder match")
    return 1


if __name__ == "__main__":
    sys.exit(main())