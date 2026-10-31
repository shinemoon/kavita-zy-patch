# 掌阅 (iReader) 书架桥接

把掌阅 App「传书到手机」书架里的 EPUB 拉到本地。

> **整体架构**（Kavita ⇄ 掌阅桥接 ⇄ echopaper/纸间，含端口、四条同步通道、自愈、已知边界）
> 见 [`docs/architecture.md`](docs/architecture.md)。5012 控制台的代码就是本仓 `sync/web.py`。

## 结论（2026-10-21 实测）

之前判断「`is_local_book: true` = 文件只存本地 App, 云端拿不到」是**错的**。

真实情况:
- 文件在 OSS 桶 `service-local-book.oss-cn-beijing.aliyuncs.com`
- object path = `book-prod/<book_file_id>.epub`
- 签名 URL 通过 `file/info` 拿, 参数是 **`book_file_id`**（不是 `book_id`）

`book_id` 和 `book_file_id` 在这几本上恰好同值, 但接口按字段名匹配。之前传错字段拿到 `{"body":{}}` 空响应, 就误判成"云端没有"。

## API 表

WAF 路由前缀 = `/bookstore/...`。没前缀的 `/open/...` 全 404。

| 用途 | 方法 + 路径 | 备注 |
|---|---|---|
| 生成扫码登录二维码 | `GET /bookstore/open/user/qrcode/get` | 返回 code (32位 hex) |
| 轮询扫码结果 | `GET /bookstore/open/user/qrcode/result?code=<code>` | 30059=未授权, 成功=body 带 usr/zysid |
| 用户信息 | `GET /bookstore/open/user/info` | |
| 书架列表 | `GET /bookstore/open/cloud/bookshelf/list` | |
| **文件签名 URL** | `GET /bookstore/open/cloud/storage/file/info?file_ids=<fid>` | **核心接口**, 返回 `sign_url` |

死接口（探过, 不通）:
- `/trade/open/book/asset` → 304002
- `/bookstore/open/book/detail` → `code:1 无此书信息`
- `/bookstore/open/cloud/storage/download/authorize` → 602001 参数错
- `/bookstore/open/book/chapter/web/list` → 602001
- `/bookstore/open/section/list` → 返推荐流, 不是章节列表

正文不需要章节接口 —— 这些书是**整本 EPUB 传书**, 一个 file 对一个 epub, 拿到签名 URL 直接下整个文件。

## 必带参数

所有 `/bookstore/*` 请求自动追加 5 个设备指纹参数, 缺一个返 `code:2`:

```
p29=zye5b814  p2=104583  p3=101010010  p4=501603  p33=com.chaozh.iReaderFree
```

另有 `usr` + `zysid`（用户身份）。指纹参数全局通用, 不是一次性的。

## 凭据

`.auth.json` — usr / zysid / 设备指纹
`.auth-cookies.json` — 浏览器 cookie (`_zypps` 是 PC 站真正的 session cookie)

两者都是 600 权限。**不要提交到 git。**

签名 URL `Expires` 约 24h, 过期重新调 `file/info` 即可。

## 脚本

| 文件 | 作用 |
|---|---|
| `scripts/poll-login2.mjs` | 扫码登录, 存 cookie 到 `/tmp/zy-cookies-real.json` |
| `scripts/shelf-dom.mjs` | 抓 SSR 页面里的 `__NUXT__` → `/tmp/zy-nuxt.json` |
| `scripts/fetch-all.sh` | 从 `__NUXT__` 提 file_id, 逐本下到 `books/` |
| `scripts/verify.py` | 校验 EPUB 完整性 + 打印元数据 |
| `scripts/probe-file.sh` | 裸探接口, 看原始返回 |

书架是 SSR 渲染的, `bookshelf/list` 的 XHR **不发**（只在 Nuxt store 里有）。要拿数据得读 `window.__NUXT__.state.bookshelf.list` / `.bookInfo`。

## 环境

Playwright 在 `~/.picoclaw/workspace/skills/paperecho-receiver/bin/node_modules/playwright`。

node 每次要先 source:

```bash
export NVM_DIR="/vol2/1001/.nvm"
. "$NVM_DIR/nvm.sh"
```

## 输出

`books/*.epub` — 5 本, 239MB

- 狐的故事 (森见登美彦) 363KB
- 剑来（1-54册）完结精校版 (烽火戏诸侯) 31MB
- 路邊的藤井 - 卷01/02/03 (鍋倉夫) 各 ~66MB
---

## Web 控制台 (2026-10-21)

浏览器**不直接连掌阅**。链路:

```
浏览器 ──HTTP──> 127.0.0.1:5012 (web.py, stdlib http.server)
                     │  subprocess
                     ▼
                 zync.py push/pull   ← CLI 跑在 NAS 本地
                     │  urllib
                     ▼
              icloud.zhangyue.com
```

状态**从本地 `state/sync.sqlite` 读**, 不问掌阅。这样绕开了公网可达性、
CORS、以及掌阅风控 —— 浏览器从头到尾只跟本机说话。

### 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 控制台页面 (移动端适配) |
| GET | `/api/state` | 映射 + 上次 push/pull 时间 (直读 sqlite) |
| GET | `/api/log?n=60` | `sync.log` 尾部 |
| POST | `/api/run/push` | 跑 `zync.py push` |
| POST | `/api/run/pull` | 跑 `zync.py pull` |
| POST | `/api/run/status` | 跑 `zync.py status` |
| POST | `/api/run/links` | 跑 `zync.py links` |
| GET | `/api/reader_css` | 阅读样式配置 (规则列表 + 渲染后的 css + 预设 + 文件侧状态) |
| POST | `/api/reader_css` | `{op: save\|reset\|toggle\|delete\|line_height\|patch\|unpatch}` 改配置 / 写进 Kavita 文件 |
| GET | `/api/keyword_mark` | 关键词高亮配置 (目录 / 绑定 / 语料清单 / 每类样式 / 系列列表) |
| GET | `/api/keyword_mark/payload?series=N` | 试算: 这本书实际会下发多少词 (跟前端拿的是同一份) |
| POST | `/api/keyword_mark` | `{op: save\|reset\|toggle\|bind\|unbind}` 改配置 |
| GET | `/api/cloud/books` | 掌阅个人云书架（`is_local_book=true`，无 DRM）+ 本地匹配状态 |
| GET | `/api/cloud/local` | 已下载清单（索引 + 目录里未登记的文件、是否切片版、页数） |
| POST | `/api/cloud/download` | `{key, overwrite?, target?}` 下载；落在 Kavita 库目录里会自动收尾 |
| POST | `/api/cloud/finalize` | `{keys?, paths?, pull?}` 对**已下载**的书重跑收尾（幂等，不重新下载） |
| POST | `/api/cloud/job` | 查下载/收尾任务进度（含每一步 `steps`） |
| POST | `/api/cloud/move` `/api/cloud/local_delete` `/api/cloud/delete` | 搬移 / 删本地 / 删云端条目 |
| GET | `/healthz` | 存活探针 |

### 掌阅云书下载 = 一站式收尾 (2026-10-31)

从 5012 下载个人云书，落点若在 Kavita 库目录，**下载完成那一刻就已经可用**：

```
下载字节 → 原书归档 bridge/books  → 切片版换进库 → sidecar(页↔原书字节)
        → 扫库并等 Chapter.Pages 对上 → book_link 建档 → pull(掌阅进度落到页号)
```

七步由 `epubprep.ensure_ready()` 串起来，**幂等、可从磁盘现状续做**：

- 已经是切片版 + sidecar 在 → 不重建，只补后面缺的步骤
- 切片版但 sidecar 丢了 → 用**原书确定性重建**一次（页映射随之恢复）
- 库里是原件 → 归档 + 切片 + 换库（旧文件留 `state/backup/`）
- 多卷 / 图片型 epub → 不切片，但**必须建档**（卷级映射），否则永远推不动
- 缺原书又缺 sidecar → 明确报错，不瞎猜几何

**pull 策略 `auto`**：只在 Kavita 这本书**还没有任何进度**时才自动 pull（新书就是这种情况）。
已有进度的书不动 —— 避免把读者读了一半的位置按云端旧值往回拨，需要时手动点「从掌阅拉」。

**「补齐收尾」按钮 / `POST /api/cloud/finalize`**：不重新下载，只把缺的步骤补上。
行内「补齐」针对单本；卡片顶部针对索引里全部；`data-path` 也支持下载目录里未登记的文件。
返回的 `steps[]` 会直接展示哪一步没成（例如「pages: Kavita 100 页 / 期望 204」）。

> 历史教训（2026-10-31 万古江河）：`prep_download` 是"从原始文件出发的一次性准备"，
> 扫库那步抛异常（API key 路径被脱敏成 `/home/youruser`）就留下"库里已是切片版、但没有
> sidecar / 没核对页数"的中间态，而它下次看到 `is_edition` 直接 return `"edition"` ——
> 中间态永远补不回来。所以下载、覆盖下载、事后修复现在都走同一个入口 `ensure_ready`。

**本地字节数 ≠ 云端是预期的**：字节数不同只说明本地那份是切片版（为了跟掌阅对齐进度
而重排），不是错误。5012 面板不再对大小差异弹警告，只在卡片顶部说明一句；列表里改用
「已切片 N 页 / 原书」pill 表达状态，原文在 `bridge/books/` 里（算进度用的永远是它）。

单例锁: 同时只允许一个 CLI (push 要写 `kavita.db`, 并发会打架), 冲突返 409。

### 阅读样式增强 (2026-10-26)

5012 的「阅读样式增强 (Kavita)」卡管的是 **Kavita 阅读器的排版 CSS**（目前主要是行间距）:

```
5012 (root) 写 /vol2/1001/kavita-zy-patch/state/reader_css.json
   → 补丁后端 GET :5111/zy/api/reader-css 渲染成 CSS
   → 补丁前端 (zy-kavita-patch.js) 60s 轮询, version 变了就注入 <style id="zy-reader-css">
```

- 规则 = `{id, name, enabled, selector, decls:{prop:value}, important}`，页面里可**增 / 改 / 删**
- 渲染 = `selector { prop: value !important; }`（`!important` 用来压 epub 自带的行内 `line-height`）
- **不改 Kavita 文件、不重启容器**；关掉「启用」立即恢复原排版
- 逻辑在 `sync/sync/reader_css.py`（补丁后端也 import 这一份，单一来源）
- 冒烟: `node /vol2/1001/kavita-zy-patch/src/verify-reader-css.mjs`（真开阅读器量 computed line-height）

**文件侧 patch (可选, 2026-10-26)** —— 同一条链路之外还有一条"硬"的:

```
5012 「Patch 到 Kavita 文件」→ reader_css.patch_to_kavita()
   → 写进 Kavita index.html <head> (<!-- ZY-READER-CSS-BEGIN --> + <style id="zy-reader-css-file">)
```

- 不依赖补丁后端/JS; 幂等 (每次先剥自己那段); 挂载模式**就地覆盖**保留 inode
- 「从文件移除」只删文件侧, 配置与运行时注入不动; 选择记在 `state/reader_css_file.json`,
  `install.sh publish` 重建 index.html 后按它决定是否恢复
- 文件侧已 patch 时, 保存/恢复默认会自动同步过去
- CLI: `python3 sync/reader_css.py {file|patch|unpatch}`

### 关键词高亮 / 阅读器滤镜 (2026-10-30)

把 epub-refiner（5013）导出的 `keywords.json` 在阅读时叠到 Kavita 正文上。
**纯前端滤镜**，不改 Kavita 文件、不走 Kavita 后端、默认模式下不改 DOM：

```
5012 (root) 写 <PATCH_ROOT>/state/keyword_mark.json
   → 补丁后端 GET :5111/zy/api/keyword-mark?series=N  (按书挑语料, 返词表 + 每类样式)
   → 补丁前端 在 .book-content 上用 CSS Custom Highlight API 上色
```

- 逻辑在 `sync/sync/keyword_mark.py`（补丁后端也 import 这一份，单一来源）
- 语料格式两种都认：epub-refiner 的
  `{categories:{id:{name,items:[{term,aliases}]}}}`，
  以及本书库自己的 `{mappings:[{source,target,tags}]}`（`tags[0]` 当分类）
- 选语料：显式 `bind`（`{"series_id": "文件名"}`）优先；否则按书名
  完全相同 / 子串 / 词集合 Jaccard ≥ 0.55 匹配，**同一作者的另一本不会被硬配**
- 词过滤：`min_len`（按字母数字个数）、`skip_digits`（挡 `1981`）、`stop`（默认一串人称/冠词）、
  `only`（只留指定分类）、分类样式 `enabled:false`（该类不下发）
- 冒烟: `python3 -m sync.keyword_mark pick 25` / `payload 25` / `summary`
- 前端调试: Kavita 页面 F12 → `__ZY__.kw()`（`ranges` = 实际高亮段数）

### 启停

```sh
cd /vol2/1001/zhangyue-bridge/sync
./zyweb.sh start|stop|restart|status|log
python3 stop_all.py        # 兜底: 连没写 pidfile 的历史进程一起清
```

systemd 单元: `zyweb.service` (绑 127.0.0.1, 不直接暴露公网)。

### 公网访问

服务**只绑 127.0.0.1**。要外网访问走反代, 别直接改 HOST:

```nginx
location /zy/ {
    proxy_pass http://127.0.0.1:5012/;
}
```

或者用花生壳的端口映射把 `5012` 转到路由器。

**安全提醒**: `ZY_WEB_TOKEN` 默认为空 = 任何能访问到该端口的人都能触发 push/pull
(会改掌阅云端进度)。公网暴露前务必设 token, 之后请求要带 `X-ZY-Token` 头。

### 已修的 bug

`sync.log` 里刷了一整天的:
```
sqlite3.OperationalError: no such column: c.LibraryId
sqlite3.OperationalError: no such column: c.SeriesId
sqlite3.OperationalError: ON CONFLICT clause does not match any PRIMARY KEY or UNIQUE constraint
```
是 17:09 前的旧代码。当前 `kavita_db.py` 的 INSERT 用的是 `s.LibraryId` (Series 表),
已经正确, dry-run 确认 `failed=0`。
---

## 笔记 / 书签 / 阅读时长 双向同步（2026-10-24 起）

CLI：

```sh
cd /vol2/1001/zhangyue-bridge/sync
python3 zync.py notes {sync|pull|push} [--series N] [--dry-run]
python3 zync.py time  {sync|pull|push|backfill|flush|queue} [--day D] [--start D --end D] [--dry-run]
python3 tests/test_notes_time.py
```

关键事实（都实测过，细节见 `sync/notes/05-notes-time-apk-v2.md`）：

* **书身份是文件内容 MD5**，不是设备自增 id → 手机/平板读同一份文件落到同一个桶，
  多设备时长/笔记天然合并。自传书的 `local_bid = <文件MD5 32hex 大写>_local`。
  APK 依据：`od.a` 的身份参数 = `BookItem.mLocalBookId` = `MD5(file) + MD5(path)`
  （`of/a.java:917` 等），服务端只取前 32（文件 MD5）加 `_local` 后缀。
  **边界**：同一本书若被重排/重打包（文件 MD5 变了），时长会落到另一个桶 →
  我们 kpages 重排版只给 Kavita 用，绝不回写掌阅云端。
* **时长读回来是精确的**：`read_report/book?book_id=<MD5>_local` 直接给
  `daybyday_times[].detail[] = {read_day, read_minutes}`（每天每书），
  不再需要按天总量分摊。周级分摊只在取不到逐日数据时兜底（`mode=alloc`）。
* **写入模型照抄 APK（2026-10-24 重做）**：本地队列 `zy_time_queue` = 每 **书 × 每天** 一行、
  值是那一天的 1440 分钟位图（与 APK `ReadTimeInfo.mData` 同构）。读到新分钟按位并集累加；
  上传时按日期分组、**一次请求可带多天**；只有 `code==0` 才删行，失败留队重试，超 30 天丢弃。
  这就是 `od/a.java` 的 `I()` / `q0()`：`enqueue(day)` → `flush()`；
  `time backfill --start --end` 等价于 APK 离线多天累积后的首次上传。
  请求形态 `{usr, data, timestamp, sign:""}`（gzip body，签名留空，服务端不校验）。
* **服务端归账日 = 收到请求那天**（2026-10-24 六次实测，`notes/05 §2.5`）：`data` 里的历史日期 key 与
  `body.timestamp` 都不影响归属 —— 传 20261016/17/18/20/22/23 全部落到上传当天，传未来日期静默丢弃。
  所以队列的实际价值是「同一天内失败重试 + 协议保真」；**跨天离线补录会被服务端并入上线那天**，
  这是服务端行为，客户端无从绕过（除非能签 `sign`，那是 native RSA）。
  分钟位图按并集合并 → 同一天重复推幂等。
* **KEY 大小写敏感**：小写 `<md5>_local` 会落到另一个空桶（踩过一次）。

| 数据 | 掌阅 → Kavita | Kavita → 掌阅 |
|---|---|---|
| 划线 / 批注 | ✅ | ✅（`uploadData` 的 `?` 拼装 bug 2026-10-24 已修，同一个 zysid 就能写）|
| 书签 | ✅ | ✅ 同上 |
| 阅读时长 | ✅ 精确到每天每书 | ✅ 队列 + 位图（幂等；历史 key 会被服务端并入上传日） |

### 进度记录的 bookId 双 key（2026-10-26 晚，修正上面那段）

上一段说的"两套存储"其实只是**表面现象**：`getUserNotebooks` 只是把 App 自己
写的那条记录换个接口暴露出来，而且**只列有笔记的书**。真正的根因是
**云端按 bookId 存了多份互不同步的进度记录**：

| key | 谁在用 |
|---|---|
| `32 hex 大写`（文件内容 MD5） | **App**（`BookHolder.mLocalBookFileMd5`，`kd/e.java:o()` / `q6()`） |
| `64 hex 大写`（MD5+MD5(path)） | 云书架 `book_id` —— 我们以前只写这条 |

实测（剑来，19:08）：往 64 写 → 32 不变；往 32 写 → 32 变。**App 读 32**，
所以以前所有 push 都写进了 App 看不见的那份，验证时读回自己写的 64 又"自证成功"。

* `push`：`app_book_keys()` → 同时写 `[32up, 64up]`
* `pull`：两条都读，取最靠前的那份（无笔记的书也拿得到 App 真实进度）
* `chapterId`：App 用的是**目录项下标**（`getCatalogCount()`，不含封面/制作信息），
  我们以前写 `spine-1`，只在 front=1 的书上碰巧对 → `_catalog_chapter()` 修正
  （剑来 spine 590 / catalog 588，App 记 157 而位置 pre=159）
* `push_decision(meta_stale)`：修元信息不再允许半页级回拨

细节与证据见 `sync/notes/06-progress-key-and-chapterid.md`。

⚠️ App 侧仍然只在**书架刷新**时拉一次进度（每会话每本一次），开书跳转还要求
`core.comparePosition(本地, 云端) < 0`。所以改完进度要下拉刷新书架 / 重开 App。

### 上午那版解释（保留作对照）

掌阅云端把「读到哪」写两个地方，**更新节奏不同**：

| 存储 | 谁写 | 接口 | 原 pull 是否读 |
|---|---|---|---|
| `readProcess` | 我们 push（`readingSource=kavita-bridge`）、App 偶尔写 | `getProgress` / `uploadProcess` | ✅ 唯一来源 |
| `readpostion` / `readpercent` | **App 自己**（带 `deviceName` / `updatetime`） | `getUserNotebooks` | ❌ 漏了 |

实证（剑来，2026-10-26）：

```
getProgress       readPercent 0.27387166  readPosition 155_0_0$##3-0-155-47953-0##$
getUserNotebooks  readpercent 0.27755222  readpostion  158_0_0$##3-0-158-35920-0##$   updatetime 更新 32 小时
```

App 已读到 27.755%（剑来15 第二章），而 5012 反复 pull 只能算出 27.387% →
Kavita 书架永远 27.4%，用户在 App 里看到 27.7%。

**修法**：`sync._zy_notebook_progress()` + `_best_zy_percent()` —— 两份取**更靠前**的
那份（App 那份才是读者真实位置，decode 后与 `getProgress` 同一套字节坐标，
percent 逐位吻合）。用在两处：

* `pull_once` → 决定写进 Kavita 的页号 / 页内 xpath
* `push_once` 的防回退基线 → 否则 App 读到 27.7% 时我们还以为掌阅只到 27.4%，
  会把进度往回拨

`getUserNotebooks` 只列**有笔记的书**，其余书自然退回 `getProgress`。

---

## 仓库结构（2026-10-24 纳入 git）

本目录整体是一个 git 仓库，远端 = `git@github.com:shinemoon/kavita-zy-patch.git`，
分支 `zhangyue-bridge`（与补丁仓的 `main` 互不相干）。

| 路径 | 内容 |
|------|------|
| `sync/` | 同步核心（push/pull、kpages 重排版、cloud_books 云书、5012 控制台 web.py） |
| `scripts/` | 一次性探针脚本（扫码、书架、进度、上传试探，多为历史考古用） |
| `tools/` | 小工具（kapi.py 等），`node_modules/` 不入库 |
| `research/` | 抓包/反编译笔记（web_ireader_com、cmd_zhushou_app） |
| `sync/notes/` | 关键结论存档（readPercent 公式、totalCount 语义、pull 不可见成因） |

### 不入库

`state/`（运行态 + 备份 epub）、`books/`、`apk/`、`ghidra-proj/`、`ghidra-out/`、
`tools/node_modules/`、`.auth.json`、`.auth-cookies.json`。

掌阅凭据只留在本机 `/vol2/1001/zhangyue-bridge/.auth.json`（`usr` / `zysid` / 指纹 p29…p33）。

### 探针脚本已脱敏

`scripts/` 里原先硬编码的真实 `usr` / `zysid` 已替换为 `__ZY_USR__` / `__ZY_ZYSID__`。
要复用这些脚本，从 `.auth.json` 取值填回（或自行改成读 env）。不要把真实值提交回去。
