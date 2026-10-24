# 掌阅 ↔ Kavita: 划线 / 批注 / 书签 / 阅读时长 的同步机制与可行性

调查日期 2026-10-24。全部结论来自 APK 反编译 + 真凭据只读实测（读写边界标注清楚）。

---

## 1. 掌阅笔记侧（划线 / 批注 / 书签 / 手写）

### 1.1 四类集合

| 字段名 | 含义 | 本地表 | 条目标识 `uniquecheck` |
|---|---|---|---|
| `booknotes` | 划线（原文摘句） | `DBAdapter` → `BookHighLight` | `<bookid>_<positionstart><positionend>` |
| `bookmarks` | 书签 | `DBAdapter` → `BookMark` | `<bookid>_<markpostion>` |
| `bookScaleNotes` | 想法 / 点评（用户在划线或段落上写的评论） | `ug.e` → `idea.bean.o` | 服务端 UUID |
| `bookWriteNote` | 手写 / 涂鸦笔记（remark 是 base64 笔迹数据） | — | 服务端 UUID |

`uniquecheck` 不是哈希，是**字符串拼接**（`hg/e.java:636/640`）：

```java
l(bookid, positionS, positionE) = bookid + SPLIT_KEY + positionS + positionE
m(bookid, position)             = bookid + SPLIT_KEY + position
```

所以客户端可以纯靠位置做去重，不需要服务端给 id。

### 1.2 位置编码（关键）

形如 `107_0_0$##3-0-107-66812-0##$`，五段 `f0..f4`：

- `f0` 恒 `3`（协议版本）
- `f2` = **epub spine 下标（0 基，直接用）**
- `f3` = **该 spine 文件内的原始字节偏移**
- 前缀 `107_0_0` 与 `f2` 冗余

实证（剑来，bookid `70776a64…9526`，划线 summary「哈哈，家乡便是那坛老酒了，这就行去喝去」）：
`positionstart=107_0_0$##3-0-107-66812-0##$` →
`OEBPS/Text/chapter0105.xhtml`（spine[107]）第 66812 字节起，正好就是该 summary 的起始字符。
`positionend` 同法，取到 66860。

结论：**掌阅笔记 → 原书字节偏移，是精确可逆的**（与进度 `readPosition` 同一套坐标，之前反推 readPercent 用的也是 `f3`）。

注意：偏移是相对**原始 epub 文件字节**的。我们库里那份是重排后的切片版，所以映射必须走 sidecar（原书全局字节 ↔ 页号）。

### 1.3 云端接口（`icloud.zhangyue.com/cloud/storage2/safe/*`）

| 动作 | 接口 | 说明 |
|---|---|---|
| 拿索引 | `GET getUserNotebooks` | 每本 `marknum`/`notenum`/`noteWriteNum` + `readpostion`/`readpercent`/`updatetime`，无正文 |
| 拿版本 | `GET getUserDataVer?bookids=a,b` | 每本三组 `{uniquecheck, marktime}` 列表，用于增量判断 |
| 下载 | `POST downloadDataV2` | gzip 请求/响应，按书返回全量 + `delList` |
| 上传 | `POST uploadData?isCheck=1` | gzip，**增量**（客户端自己过滤） |
| 删除 | `POST delMarkOrNote` | gzip，`type` 1=书签 2=划线 3=想法 |

上传请求体（`hg/e.java`、`cloud3/vo/d.java`）：

```json
{"usr":"...","rgt":"<zysid>","books":[{
  "bookbase":{"name","type","readpostion","readpercent","bookid","updatetime","deviceName","marknum","notenum"},
  "bookmarks":[...], "booknotes":[...], "bookScaleNotes":[...], "delList":["<uniquecheck>"...]
}]}
```

划线条目（`BookHighLight.getJSONObject`）：
`uniquecheck, style, color, summary, remark, positionstart, positionend, positionstartl, positionendl, marktime, chapterId, chaptername, paragraphId, paragraphOffset, notesType, versionId`

**body 里没有 sign / timestamp** —— 只有 `usr` + `rgt`，鉴权靠 URL 上的 `usr/zysid/p29/p2/p3/p4` 一套。
实测 `POST uploadData`（`books:[]` 空载荷）→ `{"code":0,"msg":"ok"}`：
**写接口对我们开放，不需要 RSA 私钥。**

下载请求体：

```json
{"usr":"...","rgt":"<zysid>","versionlist":{
  "bookid":"<64hex>",
  "booknotesmd5":"…","bookScaleNotesMd5":"…","bookmarksmd5":"…",
  "booknotesVerList":[],"bookScaleNotesVerList":[],"bookmarksVerList":[]}}
```

`*md5` = `md5(JSON.stringify(本地该集合的 [{uniquecheck, marktime}] 数组))`，空数组 = `md5("[]")` = `670b14728ad9902aecba32e22fa4f6bd`（旧记录误记成 md5("0")）。
响应 `res` = `{bookbase, bookmarks[], booknotes[], bookScaleNotes[], bookWriteNote[], delList[]}`。

### 1.4 合并语义（客户端行为，必须照抄）

- **上传前**（`hg/m.java#p`）：对每个条目比对云端 `marktime`；云端 >= 本地 → 跳过不上传。即"本地更旧的不覆盖云端"。
- **下载后**（`hg/g.java`）：云端优先 —— 同 `uniquecheck` 直接覆盖本地（`updateHighLightByPostion`，无则 insert）；`delList` 里列出的本地条目删除。
- 所以云端是**偏向权威**的一端，客户端只做"补上传"和"照抄下载"。

### 1.5 触发时机

- `BookBrowserFragment` 关书 → 全量 upload
- 阅读界面 `ScheduledExecutorService.scheduleAtFixedRate(1 min)` → 全量 upload
- 笔记编辑保存（`BookNoteListFragment`）→ 单条 upload（`hg/u.java`）
- 打开笔记列表 / `doSynchCloud` → download（1 秒节流）
- 总开关 `ConfigMgr.getReadConfig().mEnableAutoCloud`

---

## 2. 阅读时长

### 2.1 本地表示

`ReadTimeInfo`：**每本书 × 每天** 一条，`mData` = 180 字节位图（1440 分钟，1 bit = 1 分钟），
`w(ts) = hour*60 + minute + (sec>0?1:0) - 1`。
跨天时会拆成两条（`od/a.java#B()`）。另有 `ReadCountInfo`（每章字数位图），上报路径在本版 APK 里是死代码。

### 2.2 上传（写）

```
POST https://uc.ireader.com/postal/reading_time/report?<usr/zysid/p29/...>
gzip({"usr":..., "data":"<json 字符串>", "timestamp":..., "sign":"<RSA-SHA1 base64>"})

data = {"yyyyMMdd":[{"bid","local_bid","read_percent","res","d"}]}
res  = "R"(阅读) / "T"(TTS) / "L" / "C" / "CT"
d    = base64(180 字节分钟位图)
```

`sign` 由 `Security.hash` = JNI `SHA1WithRSA`（APK 内置私钥）。
**实测：服务端不校验 sign** —— 送 `data="@@not-json@@"` + `sign="bogus"`（以及完全不带 sign），
返回的是 `{"code":1,"msg":"invalid character '@' looking for beginning of value"}`，说明签名没被拦就进了 body 解析。

结论：**阅读时长也能写**（但见 §4 的风险评估，不建议）。

### 2.3 读取（关键新发现）

- 账号级：`GET webboot.zhangyue.com/zybook3/app/app.php?ca=ClientApi_Summary.ReadTime&source=other`
  → `{totalTime, weekTimelong:{read,listen,reading_local}}`，无 per-book。
- **per-book：H5 阅读报告的后端接口**（`reading-report.50db55e8.js` 里挖出来的）：

```
GET https://webboot.zhangyue.com/usercenter/user/read_report/statistics
    date_type=week|month|year
    week/month: start_date=YYYY-MM-DD&end_date=YYYY-MM-DD 或 month=YYYY-MM
    year: year=YYYY
```

返回 `body`：
```
read                    区间总分钟
read_avg / incr_percent / over / days_cnt / finish_read_book_cnt / read_book_cnt / notes_cnt
reading_distribution    [{key:"2026-10-23", value:61}]   按天分钟
reading_longest_books   [{book_id, local_book_id, book_name, reading_time, book_type}]  top 10
prefer                 偏好分布
```

- `start_date == end_date == 某天` 时就是"**那一天每本书多少分钟**"：
  `{"date_type":"week","start_date":"2026-10-23","end_date":"2026-10-23"}` → `read=9`, 剑来 8 分钟。
- 逐书逐日的 `/usercenter/user/read_report/book?book_id=` 存在（`reading-detail` 页面在用，
  返回 `daybyday_times[{detail:[{read_minutes}]}]`），但参数还没试通（一律返回空 book_info），
  单天 statistics 已经够用，可暂不依赖它。

实测数据（2026）：全年 6214 分钟，剑来 1260 分钟；10-23 当天 9 分钟。

---

## 3. Kavita 侧

- 注解：`GET /api/Annotation/all?chapterId=` 可用；`POST /api/Annotation`、`POST /api/Bookmark`、
  `POST /api/ReadingSession` 全 **404**（4 个写接口都不存在）→ **只能直写 SQLite**。
- `AppUserAnnotation` 行样例（狐的故事，Edge 阅读器产生的那条）：
  `ChapterId=26, PageNumber=6, XPath=id("chapter04.xhtml")/p[3]/span[1], EndingXPath=同左,
   SelectedText=…, Context=…, SeriesId=22, VolumeId=22, LibraryId=1, HighlightCount=124, Created(+Utc)`
  → XPath 是 Kavita 自己的 epub 语法 `id("<文件名>")/<DOM 路径>`；epub 是"一篇 spine = 一页 = 一个 PageNumber"。
  我们库里的重排版是 `kp/pNNNN.xhtml`，所以 XPath 形如 `id("kp/p0123.xhtml")/div[2]/p[5]`。
- `AppUserBookmark`：`Page, XPath`（结构更简单）。
- 阅读时长：`AppUserReadingSession(StartTime/EndTime/IsActive…)` + `AppUserReadingSessionActivityData`
  （per chapter `PagesRead/WordsRead/StartPage/EndPage`）。当前 55 条 session / 25 条 activity。
- 写入前必须先 `sqlite3.backup()` 读副本（活库 WAL 锁），写直连活库。

---

## 4. 可行性矩阵

| 数据 | 掌阅 → Kavita | Kavita → 掌阅 |
|---|---|---|
| 划线（booknotes） | ✅ 位置可精确反解（已用真实 summary 对字节验证） | ✅ 接口无签名校验；需自造 position/uniquecheck；服务端 upsert 语义待真机验证 |
| 书签（bookmarks） | ✅ 还自带 `markpercent` | ✅ 同上，最简单 |
| 批注 / 想法（bookScaleNotes） | ✅（`summary`+`remark`） | ⚠️ 语义更宽（可挂在段落而非选句），推送前要定规则 |
| 手写笔记（bookWriteNote） | ⚠️ 只有 base64 笔迹，Kavita 无呈现方式 | ❌ |
| 阅读时长 | ✅ 按天 per-book 分钟（top10/天，含 `reading_distribution`） | ⚠️ 可写但会改掌阅统计、不可撤回，**不建议做** |

---

## 5. 建议方案（分阶段，先只读后写）

### 阶段 1：掌阅 → Kavita 注解导入（只读掌阅，零风险）

对每本已映射的书（`state/sync.sqlite` 的 `book_link.zy_book_id`）：

1. `getUserNotebooks` 拿 `notenum/marknum/updatetime` 做廉价 diff，变了才继续。
2. `downloadDataV2` 拿全量（含 `delList`）。
3. 逐条映射：
   - `f2/f3` → 原书全局字节 = `spineStart[f2] + f3`
   - 全局字节 → `kpages` sidecar 页号 `pNNNN`（我们已有 `kpages.page_of_pct`，需补一个按字节的 `page_of_byte`）
   - 在该页 HTML 里按字节定位到元素 → 生成 XPath `id("kp/pNNNN.xhtml")/<path>`
   - `PageNumber = NNNN`，`ChapterId = 该书的 Kavita chapter`，`SelectedText` 优先用云端 `summary`
     （我们自己的切片文本只做校验），`Context` 用所在段落全文。
4. 幂等：新增 `state/sync.sqlite.zy_note_link(uniquecheck, kind, marktime, kavita_id)`；
   `uniquecheck` 已存在且 `marktime` 未变 → 跳过；变了 → update 行。
5. `delList` 里的 uniquecheck → 删对应 Kavita 行（只删我们有登记的，避免误删用户在 Kavita 里手写的注解）。

### 阶段 2：阅读时长 掌阅 → Kavita

1. 每天（或补历史）拉 `statistics?date_type=week&start_date=D&end_date=D`。
2. 每本有映射的书 → 插入一条 `AppUserReadingSession`（`Start/End` 在当天内均分，例如把 N 分钟放在
   该书的典型阅读时段；或简单点：当天 12:00 起 N 分钟），`ClientInfo` 标 `iReader`。
3. 去重表 `zy_time_link(day, zy_book_id)`，已存在则跳过。
4. 局限：只能还原"当天读了 N 分钟"，不能还原真实时段与章级分布 →
   Kavita 的热力图/每日总时长准，时段偏好统计不准。`ActivityData`（章级页数）不伪造，宁缺。

### 阶段 3（可选，需明确授权）：Kavita → 掌阅 划线回推

1. 取 Kavita 侧注解（我们自己导入的除外）→ 反算 `f2/f3`（XPath → 原书字节，我们已有 `zpct.offset_of_xpath`
   反查 + `kpages` 字节段）→ 造 `positionstart/positionend/uniquecheck/marktime`。
2. 单条 `uploadData`，然后 `delMarkOrNote` 撤回做往返验证（在真机 + 云端双向观察）。
3. 阅读时长**不做**回推。

---

## 6. 待拍板

1. 阶段 1 写 Kavita DB（新增注解行）：只写导入行、删除只删有登记的行 —— 这个边界是否可以？
2. 阶段 2 的 session 造法（当天均分 / 固定时段 / 只写总时长）—— 影响 Kavita 统计的可信度。
3. 阶段 3 是否允许写掌阅云端；若允许，是否先做"1 条推送 + 立刻撤回"的实测。
4. 阅读时长是否只做单向（掌阅 → Kavita）。我倾向是：回推会污染掌阅账号统计且没有撤回接口。
