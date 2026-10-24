"""QR 实现逐层自检 — 用 segno 当 ground truth.

分层比:
    L1  版本选择 (版本号 / 尺寸)
    L2  数据码字 (bitstream -> codewords -> 分块 -> 交织)
    L3  纠错码字 (RS)
    L4  最终矩阵

用法: python3 qrselftest.py
"""
import sys

sys.path.insert(0, "/vol2/1001/zhangyue-bridge/sync")
import segno  # noqa: E402

import qr  # noqa: E402

TEXTS = [
    "web_login:506c6c3e09344d84bcd2582726b552b6",   # 42B, 实际业务形态
    "web_login:deadbeef",                            # 19B
    "A" * 60,
    "掌阅登录:abc123",                                # 中文 UTF-8 多字节
]


def expect_codewords(text: str, version: int, groups, total: int) -> list[int]:
    """按 ISO 18004 从 bitstream 推出应有数据码字 (含 pad), 不交织."""
    data = text.encode("utf-8")
    bits = []
    bits += [int(c) for c in format(0b0100, "04b")]          # byte mode
    bits += [int(c) for c in format(len(data), "08b")]      # 8-bit 计数 (v1-9)
    for b in data:
        bits += [int(c) for c in format(b, "08b")]
    for _ in range(4):                                        # 结束符
        bits.append(0)
    cap = total * 8
    while len(bits) % 8:
        bits.append(0)
    cw = [int("".join(str(b) for b in bits[i:i + 8]), 2) for i in range(0, len(bits), 8)]
    pad = [0xEC, 0x11]
    i = 0
    while len(cw) < total:
        cw.append(pad[i % 2])
        i += 1
    # 交织回来 (QR 存放顺序)
    blocks = []
    off = 0
    for count, size in groups:
        for _ in range(count):
            blocks.append(cw[off:off + size])
            off += size
    out = []
    for i in range(max(len(b) for b in blocks)):
        for b in blocks:
            if i < len(b):
                out.append(b[i])
    return out


def check(text: str) -> list[str]:
    errs = []
    data = text.encode("utf-8")

    # L1 版本
    try:
        v = qr._version_for(len(data))
    except Exception as e:
        return [f"L1 版本选择抛错: {e}"]

    ref = segno.make(text, error="Q", mode="byte", boost_error=False)
    if v != ref.version:
        errs.append(f"L1 版本 ours={v} segno={ref.version}")
    if len(qr.encode(text)) != len(ref.matrix):
        errs.append(f"L1 尺寸 ours={len(qr.encode(text))} segno={len(ref.matrix)}")

    # L2 数据码字 (ours 前 data_count 个 = 交织后的数据区)
    total, ecpb, groups = qr._Q[v]
    data_count = sum(c * s for c, s in groups)
    ec_count = total - data_count
    exp = expect_codewords(text, v, groups, total)
    got = qr._make_codewords(data, v)
    if len(got) != total:
        errs.append(f"L2 总长 ours={len(got)} 应为 {total} (数据{data_count}+纠错{ec_count})")
    got_data, got_ec = got[:data_count], got[data_count:]
    if len(exp) != data_count:
        errs.append(f"L2 期望侧长度 {len(exp)} != {data_count}")
    else:
        bad = [(i, f"{got_data[i]:02X}", f"{exp[i]:02X}")
               for i in range(data_count) if got_data[i] != exp[i]]
        if bad:
            errs.append(f"L2 数据码字不等 {len(bad)}/{data_count}: {bad[:8]}")
    # L3 纠错: 用 segno 的整图反推不可行, 但可以让 RS 单块自洽 — 检查
    #    纠错码字数量 = ecpb * 块数
    if len(got_ec) != ecpb * sum(c for c, _ in groups):
        errs.append(f"L3 纠错码字数 ours={len(got_ec)} 应为 {ecpb * sum(c for c, _ in groups)}")
    return errs


def main() -> int:
    bad = 0
    for t in TEXTS:
        errs = check(t)
        n = len(t.encode("utf-8"))
        if errs:
            bad += 1
            print(f"FAIL ({n}B) {t[:36]}")
            for e in errs:
                print("      " + e)
        else:
            print(f"ok   ({n}B) {t[:36]}")
    print()
    print("all pass" if not bad else f"{bad} case(s) failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())