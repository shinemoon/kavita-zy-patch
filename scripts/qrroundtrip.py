"""QR 编解码往返测试 (用 jsQR 当解码器).

生成一批 payload → 用我们自己的 encode 编矩阵 → 交给 node/jsQR 解 → 比对原文。
jsQR 在 headless Chromium 里跑, 由 scripts/qrdec-site.mjs 承担解码。

用法: python3 scripts/qrroundtrip.py
"""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/vol2/1001/zhangyue-bridge/sync")
import qr  # noqa: E402

NODE_BIN = "/vol2/1001/.nvm/versions/node/v22.23.3/bin"
DECODER = "/vol2/1001/zhangyue-bridge/scripts/qrdec-site.mjs"
TMP = Path("/tmp/qr-roundtrip.txt")

PAYLOADS = [
    "web_login:20116218fd8c4cc4a930646cea840f8d",
    "web_login:" + "0" * 32,
    "web_login:" + "f" * 32,
    "x",
    "web_login:a",
    "掌阅登录 test",
    "A" * 20,
    "A" * 60,
    "0123456789abcdef" * 3,
]


def decode(matrix) -> str | None:
    TMP.write_text("\n".join("".join(str(v) for v in r) for r in matrix), encoding="utf-8")
    env = dict(os.environ)
    env["PATH"] = NODE_BIN + ":" + env.get("PATH", "")
    r = subprocess.run(["node", DECODER, str(TMP)], capture_output=True, text=True, env=env, timeout=90)
    try:
        return json.loads(r.stdout).get("data")
    except Exception:
        return None


def main() -> int:
    fails = 0
    total = 0
    for payload in PAYLOADS:
        for ecl in ("L", "M", "Q", "H"):
            total += 1
            try:
                m = qr.encode(payload, ecl)
                v = (len(m) - 17) // 4
            except Exception as e:
                print("FAIL %-44s %s  encode: %s" % (payload[:42], ecl, e))
                fails += 1
                continue
            got = decode(m)
            ok = got == payload
            if not ok:
                fails += 1
            note = ""
            if v >= 7:
                # 版本信息区 (v>=7) 尚未实现 —— 业务最长 payload 42B, 落到 v3, 不受影响。
                note = "  [v>=7 版本信息区未实现, 不在业务范围]"
            print("%s %-44s %s v%-2d size=%-3d%s"
                  % ("ok  " if ok else "FAIL", payload[:42], ecl, v, len(m), note))
    print()
    print("%d/%d pass" % (total - fails, total))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())