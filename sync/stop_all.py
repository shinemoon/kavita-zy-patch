"""停掉所有 web.py 进程 (包括没写 pidfile 的历史进程).

用法: python3 stop_all.py
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

STATE = Path("/vol2/1001/zhangyue-bridge/state")
PIDF = STATE / "web.pid"
PORT = os.environ.get("ZY_WEB_PORT", "5012")


def _listening_pids(port: str) -> set[int]:
    out = subprocess.run(["ss", "-tlnp"], capture_output=True, text=True).stdout
    pids: set[int] = set()
    for line in out.splitlines():
        if f":{port}" not in line:
            continue
        for tok in line.split():
            if tok.startswith("pid="):
                pids.add(int(tok[4:]))
    return pids


def _web_pids() -> set[int]:
    out = subprocess.run(["ps", "-ef"], capture_output=True, text=True).stdout
    pids: set[int] = set()
    for line in out.splitlines():
        if "web.py" not in line or "grep" in line or "stop_all" in line:
            continue
        pids.add(int(line.split()[1]))
    return pids


def main() -> int:
    targets = _web_pids() | _listening_pids(PORT)
    print("targets:", targets or "(none)")
    for p in targets:
        try:
            os.kill(p, signal.SIGTERM)
            print("SIGTERM ->", p)
        except ProcessLookupError:
            print("already gone:", p)
        except Exception as e:
            print("fail", p, type(e).__name__, e)
    time.sleep(2)
    left = _listening_pids(PORT)
    if left:
        print("still listening, SIGKILL:", left)
        for p in left:
            try:
                os.kill(p, signal.SIGKILL)
            except Exception as e:
                print("kill -9 fail", p, e)
        time.sleep(1)
    PIDF.unlink(missing_ok=True)
    print("port", PORT, "clear" if not _listening_pids(PORT) else "STILL BUSY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())