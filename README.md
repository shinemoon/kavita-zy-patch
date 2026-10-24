# 掌阅 (iReader) 书架桥接

把掌阅 App「传书到手机」书架里的 EPUB 拉到本地。

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
| GET | `/healthz` | 存活探针 |

单例锁: 同时只允许一个 CLI (push 要写 `kavita.db`, 并发会打架), 冲突返 409。

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
| 划线 / 批注 | ✅ | ⚠️ 协议已通，当前账号写接口返 `600001 authority is error`（需重新扫码刷新 zysid） |
| 书签 | ✅ | 同上 |
| 阅读时长 | ✅ 精确到每天每书 | ✅ 队列 + 位图（幂等；历史 key 会被服务端并入上传日） |

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
