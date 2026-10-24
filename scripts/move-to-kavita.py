#!/usr/bin/env python3
"""跨文件系统搬运 EPUB 到 Kavita 书库。

/vol2 (ext4, dev=64769) -> /fs (trimafs, dev=50) 跨设备,
os.replace 会抛 OSError EXDEV, 所以走 copy + fsync + 校验 + replace 语义。

每个文件:
  1. copy 到 <dst>.part
  2. flush + fsync
  3. 校验大小 + sha256 与源一致
  4. os.replace(.part -> dst)   (同文件系统内, 原子)
  5. chmod 0700 (对齐 Kavita 现有书)
  6. 校验通过才删源
任一步失败: 保留源, 清理 .part, 不做破坏性操作。
"""
import hashlib
import os
import shutil
import sys

SRC_DIR = "/vol2/1001/zhangyue-bridge/books"
DST_DIR = "/fs/1001/ftp/dav/Books/掌阅"
CHUNK = 4 * 1024 * 1024


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def move_one(name, dry_run=False):
    src = os.path.join(SRC_DIR, name)
    dst = os.path.join(DST_DIR, name)
    part = dst + ".part"

    s_size = os.path.getsize(src)
    print(f"\n--- {name}")
    print(f"    src {s_size:>12,} B")
    print(f"    dst exists: {os.path.exists(dst)}")

    if os.path.exists(dst):
        print("    SKIP: 目标已存在")
        return "skip"

    if dry_run:
        print("    [dry-run] 不执行")
        return "dry"

    s_hash = sha256(src)
    print(f"    src sha256 {s_hash[:16]}...")

    # 1+2 copy + fsync
    try:
        with open(src, "rb") as fin, open(part, "wb") as fout:
            shutil.copyfileobj(fin, fout, CHUNK)
            fout.flush()
            os.fsync(fout.fileno())
    except Exception as e:
        print(f"    FAIL copy: {type(e).__name__}: {e}")
        if os.path.exists(part):
            os.remove(part)
        return "fail"

    # 3 校验
    d_size = os.path.getsize(part)
    if d_size != s_size:
        print(f"    FAIL size: {d_size} != {s_size}")
        os.remove(part)
        return "fail"
    d_hash = sha256(part)
    if d_hash != s_hash:
        print(f"    FAIL sha256: {d_hash[:16]} != {s_hash[:16]}")
        os.remove(part)
        return "fail"
    print(f"    ok  size+sha256 match")

    # 4 原子落位
    try:
        os.replace(part, dst)
    except Exception as e:
        print(f"    FAIL replace: {type(e).__name__}: {e}")
        if os.path.exists(part):
            os.remove(part)
        return "fail"

    # 5 对齐 Kavita 权限
    os.chmod(dst, 0o700)

    # 5.5 落位后再验一次 (防 replace 后读取异常)
    if sha256(dst) != s_hash:
        print("    FAIL post-verify sha256")
        os.remove(dst)
        return "fail"
    print(f"    ok  placed + chmod 0700")

    # 6 才删源
    os.remove(src)
    print(f"    ok  source removed")
    return "moved"


def main():
    dry = "--dry-run" in sys.argv
    names = sorted(n for n in os.listdir(SRC_DIR) if n.lower().endswith(".epub"))
    if not names:
        print("SRC_DIR 没有 epub")
        return
    print(f"src: {SRC_DIR}")
    print(f"dst: {DST_DIR}")
    print(f"dry-run: {dry}")
    print(f"共 {len(names)} 本\n")

    tally = {}
    for n in names:
        r = move_one(n, dry_run=dry)
        tally[r] = tally.get(r, 0) + 1

    print(f"\n=== 结果 ===")
    for k in sorted(tally):
        print(f"  {k}: {tally[k]}")

    print(f"\n=== 源目录剩余 ===")
    for n in sorted(os.listdir(SRC_DIR)):
        print(f"  {n}")
    print(f"\n=== 目标目录 ===")
    for n in sorted(os.listdir(DST_DIR)):
        p = os.path.join(DST_DIR, n)
        print(f"  {n}  {os.path.getsize(p):,} B  {oct(os.stat(p).st_mode & 0o777)}")


if __name__ == "__main__":
    main()