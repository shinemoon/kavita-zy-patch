# Kavita ⇄ 掌阅桥接 ⇄ echopaper 架构说明

> 写于 2026-10-26。以当时实际运行状态为准（端口/映射/限制均已实测）。
> 三个主体 + 两个外部队列端（掌阅云、纸间书摘）。

---

## 1. 四个角色

| 角色 | 形态 | 位置 | 职责 |
|---|---|---|---|
| **Kavita** | Docker 容器，`:5000` | 库 `/vol1/1001/APPS/kavita/`，DB `kavita.db` | 主阅读器 + 书库。唯一"读完能记笔记"的地方 |
| **掌阅桥接** | Python（`sync` 包 + CLI） | `/vol2/1001/zhangyue-bridge/` | 跟掌阅云双向同步：进度 / 划线书签 / 阅读时长 |
| **Kavita 补丁** | 前端 JS + 后端 API + 控制台 | `/vol2/1001/kavita-zy-patch/` | 给 Kavita 加按钮与自动钩子；5012 是唯一操作面板 |
| **echopaper** | Python HTTP 服务 | `~/.picoclaw/workspace/skills/paperecho-receiver/` | 书摘数据的汇聚/归档/分发端（M5Paper 墨水屏 + 纸间书摘） |

两个外部端：

- **掌阅云**（`api-pc.zhangyue.com` / `icloud` / `uc.ireader.com`）—— 真正在读的是手机/平板上的掌阅 App。
- **纸间书摘**（手机 App，`/send`）—— 另一个书摘仓库，只收推送。

---

## 2. 端口与进程

```
5000  Kavita（容器）            ← 人在这里读书
5111  补丁后端 API              ← 前端 JS 调（auto/pull | auto/push | reader-css）
5012  补丁控制台 web            ← 人点按钮的地方（推/拉/云书/映射/体检）
8080  echopaper device 口       ← M5Paper 推 `/api/sync`
8081  echopaper admin UI        ← 浏览/检索/导出书摘
```

`5012` 与 `5111` 都是 root 的 systemd unit，代码改动由 `heal.sh`（timer 每 3 分钟）比对 mtime 后自动 restart。
`8080/8081` 是用户态进程，手动 `paperecho_server.py start|stop`。

---

## 3. 总图

```
                       ┌──────────────────────────┐
        手掌阅 App  ───► │  掌阅云                    │
        (手机/平板)      │  · 进度 getProgress        │
                       │  · 进度 getUserNotebooks    │
                       │  · 划线 getDataVer/upload   │
                       │  · 时长 read_report /report │
                       └──────────┬───────────────┘
                                  │ zysid + 设备指纹
                    ┌─────────────▼──────────────┐
                    │  掌阅桥接 sync 包            │
                    │  zpct · kpages · sync        │
                    │  zy_notes · zy_time          │
                    │  cloud_books · mapping       │
                    └───┬──────────────────┬─────┘
                        │ 直写/直读          │ 5012 控制台
                        │ kavita.db         │
              ┌─────────▼─────────┐   ┌─────▼──────────────┐
              │   Kavita           │◄─►│  补丁 5111 后端     │
              │   5000             │   │  auto/pull|push    │
              │  · Series/Chapter  │   │  reader-css        │
              │  · Progresses      │   └─────▲──────────────┘
              │  · Annotation      │         │ 进书 → pull
              │  · ReadingSession  │   ┌─────┴──────────────┐
              └─────────┬──────────┘   │ Kavita 前端补丁 JS  │
                        │ 读 DB        │ (挂载进 wwwroot)    │
                        │              └────────────────────┘
        ┌───────────────┼──────────────────────┐
        │               │                      │
   kavita_merge     kv_time              zy_notes/kv_time
   (annotation)     (session)            (双向写回)
        │               │
        ▼               ▼
  ┌─────────────────────────────────┐
  │  echopaper  data/paperecho/      │
  │  aggregate.jsonl (书摘 2621)     │
  │  books.jsonl     (书 712)        │
  │  <日期>/<时间>_<id>/ deck        │
  │  events.jsonl                    │
  └────────┬────────────────┬───────┘
           │                │
     8080 /api/sync     8081 admin
           │                │
    M5Paper 墨水屏      浏览 / 检索 / 书票
                            │
                     ┌──────▼───────┐
                     │ 纸间书摘 App  │  ← 浏览器当桥推 /send
                     │ (手机 :8080) │     (含 preciseReadingDurations)
                     └──────────────┘
```

---

## 4. 四条同步通道

### 4.1 阅读进度（Kavita ⇄ 掌阅，双向）

**触发**：Kavita 前端补丁 JS 监听路由与 `reader/progress` 请求。
进书 → `POST /zy/api/auto/pull`（120s 节流，位置变了才 reload）；存进度 → 去抖 8s 后 `auto/push`。

**口径（这是全系统最关键的一个定义）**

- 掌阅 `readPercent` = **字节位置 / 全书字节**（`Σ bytes(spine[0..ch]) + 章内偏移`）。float 0–1。
- Kavita 显示 = `PagesRead / Chapter.Pages`，而 **epub 的一页 = 一篇 spine 文档**，进度与续读位置绑在同一个数上。
- 结论：想让百分比精确，**只能改"页"本身**。因此桥接会把 epub 重排成"等字节切片版"（`kpages`）：
  - 狐的故事 9 篇 → 100 页；剑来 590 篇 → 3000 页（每页 ≈14KB，粒度 0.033%）。
  - 切片规则：按标签边界切、跨文档续页时重开未闭合包裹元素、所有 href/TOC/NCX 重写。
  - **原书字节永不改动**（`readPercent` 的分母就是它），原书归档 `bridge/books/`，sidecar 记每页字节区间。
  - 换库后必须 `pull` 一次重映射 `PagesRead`，否则 163/3000 这种假进度会留在界面上。
- pct → 页号用 **floor（含该字节的页）**，绝不用 round；页号 → pct 用页起点字节。

**双存储陷阱**：掌阅云有两份进度，更新节奏不同 ——
`getProgress`（桥接 push 写的）与 `getUserNotebooks.readpercent`（App 自己写的）。
`_best_zy_percent()` 取两者更靠前的那个，pull 与 push 的防回退基线都用同一口径，
否则会"修了 pull 引入 push 回拨"。

**回退闸**：`push_decision()` 只在真前进时写。回退超过半页 → `regress` 跳过；掌阅已读完不降级。

### 4.2 划线 / 书签（双向）

- **位置编码**：`<ch+1>_0_0$##3-0-<ch+1>-<章内字节偏移>-0##$` —— 与进度同一套字节坐标。
- **Kavita → 掌阅**（`zy_notes.push`）：读 `AppUserAnnotation`/`AppUserBookmark` → `uploadData`。
  指纹 `sig` = 章节+正文+批注+章名，内容不变不重推；云端 `marktime` 更新则本地让步。
  **删除只动 `source='kv'` 的行** —— 否则会把 App 里自己记的笔记一起删掉。
- **掌阅 → Kavita**（`zy_notes.pull`）：`getUserDataVer` 给每条的 `uniquecheck`(=`<book32>_<posS><posE>`) + `marktime`。
  正文靠**位置回原书切**（`bridge/books/` 的原件，不是库里的切片版！），去标签 + unescape。
  零长度选择（点一下留的标）用所在段落开头 200 字撑上下文。
- **章名**取 epub 目录（NCX 两级：卷 + 章），不是文件名。位置落在 `spine[ch+1]`，所以标签索引要 `ch+1`。
- **拿不到**：`remark`（批注正文）与云端章名 —— 只在死通道 `downloadDataV2` 里。

### 4.3 阅读时长（三向）

```
掌阅云 read_report/book ──► zy_time.pull ──► Kavita ReadingSession(IsGenerated=1)
                                                    │
                                                    ├──► kv_time.export ──► deck reading_time.json
                                                    │                        / bundle JSON
                                                    │
Kavita ReadingSession ──► kv_time.import ◄──────────┘  (反向写回, 可 purge)
                                                    │
                                                    └──► 纸间 /send 的 preciseReadingDurations
```

- **时长不在任何列里**，只能 `EndTime - StartTime`。Kavita 时间戳 **7 位小数（100ns）**，
  `strptime('%f')` 只吃 6 位 → 不截断会整批静默丢。
- `IsActive=1`（还在读）跳过；`IsGenerated=1`（桥接/纸间合成写入）保留，那是真实时间。
- **掌阅 → 本机**：`read_report/book?book_id=<文件内容MD5 32hex 大写>_local` 直接给逐日每书分钟。
  书身份 = **文件内容 MD5**，不是设备 id —— 所以手机和平板读同一份文件天然合并。
- **本机 → 掌阅**：`zy_time_queue` 表（书 × 天 × 分钟位图）按日分组上传，成功才删行。
  服务端对历史日期**一律记到"收到那天"**（已实测密钥/时间戳/多日期都无效），所以只写今天。
- echopaper 侧幂等靠**秒级 epoch 比对 + 计数**（不能 set 去重，库里有同秒多条会话）。

### 4.4 书摘（三条来源 → 一个汇聚）

| 来源 | 入口 | 落点 |
|---|---|---|
| Kavita 划线/批注 | `kavita_merge.py`（手动 / 24h 节流自动） | `aggregate.jsonl` + 独立 `kavita-merge` deck |
| M5Paper 墨水屏 | `POST :8080/api/sync`（或 `begin/batch/end`） | 当天 deck + `aggregate.jsonl` |
| 纸间（回流） | admin `import-kavita` / 手工 | 同上 |

- 跨源去重键 = `(bookId, content)`；Kavita 的 series 命中现有 `books.json` 就**复用 int bookId**，
  否则派 `kavita_<sha1[:8]>` —— 只有复用才能跟设备来源的摘录去重。
- **smart_pick.py** 从 aggregate 抽一条（季节/节气/天气/时段/节日/星期多维打分），渲染书票/卡片。
  不许自己写摘录，字段必须真存在。
- **8081 admin**：浏览/检索/高亮、单条删除、书票生成、导出 JSON、Kavita 导入、阅读时长明细/导入导出。
- **→ 纸间书摘**：`/admin/api/export-zhijian` **只造 payload 在浏览器里推**（server 与手机常不同子网）。
  可按书名/作者/时间窗/关键词筛选，附 `preciseReadingDurations`。
  注意纸间 `/send` 拒 `endTime <= startTime`（整本拒），所以未结束/零长会话必须提前剔除。

---

## 5. 关键数据源

| 数据 | 位置 |
|---|---|
| Kavita 主库 | `/vol1/1001/APPS/kavita/kavita.db`（WAL —— 读必须 `sqlite3.backup()`，不能 `cp`） |
| 补丁配置 | `kavita-zy-patch/patch.conf` |
| 前端落地 | `state/wwwroot/{index.html,zy-kavita-patch.js}`（**只读挂载**进容器） |
| 同步映射 | `zhangyue-bridge/state/sync.sqlite` 的 `book_link` |
| 分页 sidecar | `zhangyue-bridge/state/kpages/<series>.json` |
| 原书 / 切片版 | `bridge/books/` 原件、`/fs/1001/ftp/dav/Books` 库内（重排后） |
| 掌阅凭据 | `zhangyue-bridge/.auth.json`（usr/zysid/设备指纹，600，不入 git） |
| echopaper 数据 | `~/.picoclaw/workspace/data/paperecho/`（aggregate / decks / events） |

**映射粒度**：`book_link` 主键 `(kavita_series_id, kavita_chapter_id)`，`-1` = 整本。
一个多卷系列可以挂多本掌阅书（藤井 3 卷 = series 29 的 chapter 35/36/37）。

当前 5 条：狐的故事(26) / 剑来(27) / 路邊的藤井 卷01·02·03(29, 卷级)。

---

## 6. 自愈与自动

- **heal.sh**（systemd timer，3 分钟）：容器在不在 → 不在就 `compose up -d`；补丁注入在不在 →
  不在就重打；代码 mtime 比进程新 → 重启对应 unit。幂等，带 30 分钟去重告警。
- **watch-kavita.py**（可选常驻，5 秒级）：容器 stopped → `docker start`，missing → 重建；
  同时抓 `docker rm/down/prune` 与 `systemctl stop/restart kavita` 的施动者（journald 对普通用户不可读，这是唯一留痕手段）。
- **Kavita unit 语义**：`ExecStop` 必须是 `compose stop`，**不能是 `compose down`** ——
  down 会删容器，而 `docker start` 对已删容器永远失败。
- **节流**：进度 pull 120s / push 去抖 8s；笔记 30 分钟；时长 10 分钟；kavita_merge 24 小时。
- **观察口**：`state/logs/api.log`（前端 `hb` 心跳 + 每次 auto 动作）是第一现场。
  没有 `hb` 行 = 前端钩子没跑，别去查 push/pull 逻辑。

---

## 7. 已知边界

1. **Kavita 没有注解写 API** → 只能直写 `AppUserAnnotation` / `AppUserBookmark` / `ReadingSession`。
2. **掌阅笔记正文通道死**（`downloadDataV2` 恒返空）→ 靠位置切原文，**`remark` 批注拿不到**。
3. **阅读时长只能写"今天"** —— 服务端不认报文里的历史日期。
4. **图片型 epub 不做字节切片** → 页粒度粗（藤井 1/195 ≈ 0.5%）。
5. **同一本书重排/重打包会换 MD5** → 掌阅侧的时长与进度会分家。切片版只服务 Kavita，绝不回写云端。
6. **浏览器缓存**：Kavita `index.html` 无 `Cache-Control`，补丁改完必须**强刷**才能看到新版本。
7. **两个 8080**：echopaper 的 8080 在本机（被动接收）；纸间 App 的 `/send` 是手机端的 8080。
   两套协议、两套角色，别混。

---

## 8. 一句话

**掌阅负责"在哪读"，Kavita 负责"读得好、能留痕"，echopaper 负责"把留痕收好、发出去"。**
桥接的全部工作就是：让两边对同一个字节位置给出同一个数字。
