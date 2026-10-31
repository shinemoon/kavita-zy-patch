"""Kavita REST 小客户端 (按 API key 调 5000 端口)。

secrets 路径不能写死: 这个仓库会以不同用户跑 (root 起 5012 / claud 用 CLI),
发布到 GitHub 时也不能留本机家目录。所以按 env > 自身家目录 > /home/* 全扫
的顺序找 .env, 找不到就明确报错 (而不是 FileNotFoundError 埋在下载流程里)。

2026-10-31 事故: 脱敏时把 /home/claud 改成 /home/youruser, 结果下载完成的
书在建档/扫库那一步抛 FileNotFoundError, 库里的文件已换成切片版却没有
sidecar 和映射 → 那本书永远不同步。硬编码家目录就是这类事故的源头。
"""
import glob
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("KAVITA_BASE") or "http://127.0.0.1:5000"


def _env_candidates() -> list[str]:
    out = []
    if os.environ.get("KAVITA_ENV"):
        out.append(os.environ["KAVITA_ENV"])
    home = os.path.expanduser("~")
    out.append(os.path.join(home, ".picoclaw/workspace/secrets/kavita/.env"))
    out.append("/vol2/1001/.picoclaw/workspace/secrets/kavita/.env")
    out.extend(sorted(glob.glob("/home/*/.picoclaw/workspace/secrets/kavita/.env")))
    seen, uniq = set(), []
    for p in out:
        if p and p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def _load_key() -> str:
    cands = _env_candidates()
    for p in cands:
        try:
            with open(p) as f:
                env = dict(l.split("=", 1) for l in f.read().splitlines() if "=" in l)
        except OSError:
            continue
        key = (env.get("KAVITA_API_KEY") or "").strip()
        if key:
            return key
    raise RuntimeError(
        "找不到 Kavita API key (试过: " + ", ".join(cands) + "); "
        "可用环境变量 KAVITA_ENV 指定 .env 路径"
    )


KEY = _load_key()


def call(path, method="GET", body=None, headers=None, raw=False):
    h = {"x-api-key": KEY, "Content-Type": "application/json"}
    if headers:
        h.update(headers)
    data = None
    if body is not None:
        if isinstance(body, (bytes, bytearray)):
            data = bytes(body)
        else:
            data = json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, method=method, data=data, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            b = r.read()
            return r.status, (b if raw else b[:3000].decode('utf-8', 'replace'))
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:500].decode('utf-8', 'replace')


if __name__ == "__main__":
    path = sys.argv[1]
    method = sys.argv[2] if len(sys.argv) > 2 else "GET"
    print(call(path, method))
