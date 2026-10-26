# 进度记录的 bookId 双 key + chapterId 口径 (2026-10-26 晚)

症状: 5012 手动 push 后，手机 App 打开书进度还是旧的；反过来，App 读到很后面
（藤井卷01 99.5%）Kavita 却一直停在 14.3%。

一次实测把两个方向都解释清楚了。

## 1. 本地导入书在云端有 **两份独立的进度记录**

| key | 谁用 | 实测来源 |
|---|---|---|
| `32 hex 大写` = 文件内容 MD5 | **App 读写的那条** | `BookHolder.mLocalBookFileMd5 = core.getFileMD5(file).toUpperCase()`；`kd/e.java:o()` 刷新书架、`q6()` 开书都用它调 `getProcess` |
| `64 hex 大写` = MD5 + MD5(path) | 云书架 `book_id`（我们自己一直写这条） | 云书架列表接口返回的 `book_id` |

服务端**不会**在这两条之间同步。用剑来 (2026-10-26 19:08) 做的决定性实验：

```
push 到 64up  →  64up = 0.27999589 / ch 158 / src kavita-bridge
                 32up = 0.27951244 / ch 157 / src app      (没变)
push 到 32up  →  32up = 0.27999589 / ch 157 / src kavita-bridge   (变了)
```

App 读的是 32 hex，我们一直写 64 hex → **push 全部写进了 App 看不见的抽屉**。
以前的"推送成功"验证是读回自己写的 64 hex，属自证循环。

反向同理: `pull` 只读 64 hex + `getUserNotebooks`。后者只列**有笔记的书**，
所以藤井（没有笔记）根本拿不到 App 的真实进度 → Kavita 卡在我们上次推的 14.3%。

**修法** (`sync.app_book_keys()`): 64 hex 的书同时写 `[32up, 64up]`；读的时候
两条都读、取最靠前的那条当基线/权威。

## 2. `chapterId` 是**目录项下标**，不是 spine-1

App: `BookItem.mChapterCount = core.getCatalogCount()`，目录项不含封面/制作信息这些
前置文档；而 `readPercent` 的分母/前缀是**spine 字节**，用的是 spine-1。

| 书 | spine | catalog (totalCount) | App 记录 chapterId | readPosition 的 pre |
|---|---|---|---|---|
| 剑来 | 590 | 588 | **157** | 159 |
| 藤井卷03 | 193 | 192 | 189 | 190 |

即 `chapterId = pre - (spine - catalog)`。我们以前一律写 `spine-1`，只在前置文档
恰好 1 篇时碰巧正确 —— 剑来一直是多 1（App 里显示"第 159 章"）。

**修法** (`sync._catalog_chapter()`): 只改 `chapterId` 字段，`readPosition` 仍用
spine 下标 (`zpct.build_position(ch, f3)`)。

## 3. 顺带收紧回退闸

`push_decision(meta_stale=True)` 原来在半页容差内允许"修元信息"回拨
（狐 50.33% 会被写成 50.22%）。改成：meta 写要求 `new_pct >= cur_pct`，
修元信息绝不回拨。

## 4. App 侧什么时候才会应用云端进度（读代码得到，非猜测）

* `kd.e.o(visibleShelfItems)`（书架滚动/刷新）→ `getProcess` →
  `Q0(totalCount, chapterId, readPercent, ...)` 写书架显示串。
  条件: `ConfigMgr.mEnableAutoCloud` 开 + 本书**本次会话没同步过**（`f71355l` 列表）。
* `kd.e.n(...)`（开书时 `q6()`）→ 拉回进度后，只有在
  `core.comparePosition(本地位置, 云端 readPosition) < 0` 且 `!R1` 时才跳转。
* App 退出/息屏会把自己**本地**的进度上传覆盖云端。

所以"push 后手机没变"天然有两层原因：① 我们写错了 key（本次修掉）；
② App 只在书架刷新时拉一次，且每会话每本只拉一次 —— 改完进度要
**下拉刷新书架 / 重启 App** 才会看到。第二层我们无法从服务端绕过。

## 复现 / 验证命令

```bash
cd /vol2/1001/zhangyue-bridge/sync
python3 - <<'PY'
import sys; sys.path.insert(0,'.')
from sync import config, zy_client
auth = config.load_zy_auth()
full = "70776A64C32C1C065DAB241F3E9F133A55C7D8B38C5C6FBC79271E87E00E9526"
for tag, b in [("32up", full[:32]), ("64up", full), ("32low", full[:32].lower())]:
    for k, v in (zy_client.get_progress(auth, [b]) or {}).items():
        rp = v.get("readProcess") or {}
        print(tag, rp.get("readPercent"), rp.get("chapterId"), rp.get("readingSource"), rp.get("readPosition"))
PY
```

测试: `python3 tests/test_align.py`（新增 `test_app_book_keys_writes_both_local_keys`
/ `test_catalog_chapter_excludes_front_matter` / `test_push_meta_never_rewinds`）
