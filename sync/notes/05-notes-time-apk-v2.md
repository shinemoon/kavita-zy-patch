# 掌阅 APK 侧: 划线 / 批注 / 书签 / 阅读时长的实现与同步机制 (v2, 2026-10-24 夜)

上一份 `note-and-time-sync.md` 是"能看到什么接口"的清单。这一份补 **APK 内部实现**:
数据怎么落地、状态怎么算、同步怎么合并、双向各自缺什么。

反编译源码: `/vol2/1001/zhangyue-bridge/apk/jadx/sources` (jadx 1.5.0, com.zhangyue.iReader 6.7)

---

## 1. 划线 (booknotes) 的完整生命周期

### 1.1 结构 (BookHighLight, `JNI/runtime/BookHighLight.java`)

```
color          int     DEFAULT_COLOR = -36352 (0xFFFF7200 橙)
positionS/E    String  "107_0_0$##3-0-107-66812-0##$"
positionSL/EL  long    字符序号 (e.A0=positionstartl / e.y0=positionendl)
summary        String  选中的原文
remark         String  用户批注 (空 = 纯划线)
style          0 或 marktime
chapterName    String  章节名 (chaptername)
mIdea          -> chapterId / paragraphId(double) / paragraphOffset / notesType / versionId
unique         String  = bookid + "_" + positionS + positionE   (e.l())
```

`getType()`: remark 空 → 0 (纯划线), 非空 → 1 (划线+批注)。
`getIdeaType()`: 0 → 1, 1 → 2 (这两个数字就是 delMarkOrNote 里的 `type`)。

### 1.2 position 编码的第五段 f4 恒 0, f0 恒 3, f2 = spine 下标, f3 = **该 spine 文件内原始字节偏移**

跟 readPercent 用的是同一套字节坐标 (见 `01-readpercent-formula.md`), 所以
"划线位置 ↔ 全书字节偏移" 一步可换: `global = spineStart[f2] + f3`。

### 1.3 本地 -> 云端 (上传, `hg/m.java` + `hg/f.java` + `cloud3/vo/d.java`)

```
GET  /cloud/storage2/safe/getUserDataVer?bookids=<bid[,bid]>   -> 云端每本三组 [{uniquecheck, marktime}]
POST /cloud/storage2/safe/uploadData?isCheck=1   (gzip)
     {"usr":..., "rgt":<zysid>, "books":[{
        "bookbase":{"name","type","readpostion","readpercent","bookid","updatetime","deviceName","marknum","notenum"},
        "bookmarks":[...], "booknotes":[...], "bookScaleNotes":[...], "delList":[...]}]}
```

**合并语义 (关键, 客户端一侧)** — `hg/m.java#p()`:
* 先按 `bookids` 拉云端版本表 (uniquecheck -> marktime)
* 逐条本地条目: **云端 marktime >= 本地 marktime → 不上传** (云端不比本地旧就不推)
* 本地 `delList` 里的 uniquecheck 若云端还有, 一并带上 (删除传播)
* 上传成功后本地把这些标记为已同步

=> 上传是**增量 + 本地较新者胜**; 我们伪造条目时只要 `marktime = now()` 就一定会被推。
=> 服务端不验签名 (URL 上 usr/zysid/p29.. 即凭据, body 里只有 usr/rgt), `books:[]` 空载荷返 `{"code":0,"msg":"ok"}`。

### 1.4 云端 -> 本地 (下载, `hg/t.java` + `hg/g.java` + `cloud3/vo/g.java`)

```
POST /cloud/storage2/safe/downloadDataV2   (gzip)
  {"usr":..,"rgt":..,"versionlist":{ "<bid>": {
      "booknotesmd5": <md5(JSON([{uniquecheck,marktime}]))>,
      "bookScaleNotesMd5": .., "bookmarksmd5": ..,
      "booknotesVerList": [], "bookScaleNotesVerList": [], "bookmarksVerList": [] }}}
应答: {"status":0, "msg":"downloadDataProcessNew finished!", "fromType":"epub", "res":{
      "bookbase":{...}, "booknotes":[...], "bookmarks":[...], "bookScaleNotes":[...],
      "bookWriteNote":[...], "delList":[...]}}
```

* md5 是**本地该集合版本表的校验和**, 服务端据此做增量
* 客户端 "全量恢复" 分支 (`hg/r.java`) 直接把三组 md5 写死成常量
  `670b14728ad9902aecba32e22fa4f6bd` (`hg/e.f70151f`), VerList 留空 —— 该常量不是
  md5("[]")(d7517139…) 也不是 md5("0")(cfcd2084…), 属于服务端约定的 "我什么都没有" 哨兵
* **下载合并 (hg/g.java)**: 云端条目按 uniquecheck 覆盖本地 (`updateHighLightByPostion`, 无则 insert);
  云端 `delList` 里的 uniquecheck 删本地。**云端是权威端, 客户端只补传 + 照抄。**
* 渲染需要的字段: positionstart/end, positionstartl/endl, color, summary, remark, marktime,
  chapterId, chaptername, paragraphId, paragraphOffset, notesType。缺 idea 相关字段只影响"想法"展示。

### 1.5 删除 (`url_app.CLOUD_DEL` = /cloud/storage2/safe/delMarkOrNote, `cloud3/vo/f.java`)

```
POST  (gzip) {"usr":..., "rgt":..., "delItems":[
        {"type": <1=纯划线 2=划线+批注 3=想法>, "bookid": <本地 bookid>, "uniquecheks":["..."]}]}
```

注意键名少一个 c (`uniquecheks`), 是服务端的拼写, 不能"修正"。

### 1.6 触发时机 (决定"什么时候能看到对方的数据")

* 关书 → 全量 upload; 阅读页 `scheduleAtFixedRate(1 min)` → upload
* 编辑笔记保存 → 单条 upload (`hg/u.java`)
* 打开笔记列表 / 手动同步 → download (1s 节流)
* 全局开关 `ConfigMgr.getReadConfig().mEnableAutoCloud`

=> 不是实时。我们自己推上去的条目, 用户下次打开笔记列表/关书才会看到。

---

## 2. 阅读时长的完整生命周期

### 2.1 本地表示 (`od/a.java` + `ReadTimeInfo`)

* 粒度: **每本书 × 每天** 一条, `mFormatDate = yyyyMMdd`
* `mData` = base64(180 字节) = 1440 分钟位图
* 位序 (`j0()` / `w()`): 位下标 = `hour*60 + minute - 1`, **字节内 LSB 优先**
  (bit0 = 该分钟块的第 1 分钟), 1 = 读过
* 跨越午夜会拆成两条 (前一天 + 后一天)
* 另有 `ReadCountInfo` (每章字数位图), 本版 APK 里上报路径是死代码

### 2.2 上报 (`od/a.java#q0` / `z` / `I`)

```
POST https://uc.ireader.com/postal/reading_time/report   (gzip)
  {"usr":..., "data":"<json string>", "timestamp":..., "sign":"<RSA-SHA1>"}
  data = {"20261024":[{"bid":<int 本地书号>,"local_bid":"<md5>_local",
                       "read_percent":0.27,"res":"R","d":"<base64 180B>"}]}
  res: R=阅读 T=TTS L=? C=? CT=?
```

* 上传成功后**本地删除这些 ReadTimeInfo 行** → 本地上报是**增量**
* 服务端按 (usr, 日, 书) **按位或**合并 → 同一分钟重复上报不该翻倍 (待实测, 见 §4)
* `sign` 服务端不校验 (伪造 sign / 不带 sign 都进了 body 解析)

### 2.3 拉回 —— **逐书逐日可以拿到** (2026-10-24 修正)

早期结论"云端只能给周每书 + 天总量"是探测不到位。正确接口:

```
GET https://webboot.zhangyue.com/usercenter/user/read_report/book?book_id=<KEY>
KEY = 自传书: <文件MD5 32hex 大写>_local   商店书: <数字 bookid>
→ body: {read, read_progress, read_days_cnt, first_read_date, book_info{name,cover,...},
         daybyday_times:[{read_month:"2026-10", read_time:"513",
                          detail:[{read_day:"24", read_minutes:16}, ...]}]}
```

* `detail[]` = **每天每书分钟**, 精确原始值 (不是分摊)
* **KEY 大小写敏感**: 小写 `<md5>_local` 会落到另一个几乎空的桶 (实测大写 1268 分钟 / 小写 2 分钟)
* `?bookid=` 报"参数错误", 只认 `book_id`
* `read_report/statistics?date_type=week|month|year` 仍可用, 但周榜只 top10,
  且 `reading_longest_books[]` 里带 `local_book_id` (= `<md5>_local`), 用来自证身份
* `zy_time.pull()` 现在主路径走这条路 (mode=`book`), 只有取不到逐日数据的书才退回周级分摊
  (mode=`alloc`/`exact`), 并在 report 里分开计数

### 2.4 书身份 = 文件内容 MD5 → 多设备天然合并 (2026-10-24 验证)

* 自传书 `bid=0`, 服务端按 `local_bid` 记账; `local_bid` 的基座是 **文件内容 MD5**
  (`hg.e.k(BookItem)`: `mBookID!=0 ? 数字id : core.getFileMD5(mFile)`; 云书架 book_id = `MD5(file)` + 32 hex)
* 所以手机/平板读同一份文件 → 同一个桶, 时长自然合并; **不是**设备自增 id
  (APK 里另有 `mLocalBookId = MD5(file)+MD5(path)` 用于本地去重, 但云端书身份用 MD5 本体)
* 实测: 用 `<MD5>_local` 推 1 分钟, `read_report/book` 的当天分钟数 +1,
  周榜 `reading_longest_books` 里同名书同步增长 → 书级确实入账

### 2.5 写入只能"当天" (2026-10-24 实测, 重要)

| 试验 | 结果 |
|---|---|
| data key `20261020`, `timestamp`=now | 落到 **10-24** (上传日) |
| data key `20261021`, `timestamp`=now | 落到 **10-24** |
| data key `20261020`, `timestamp`=2026-10-20 12:00 | 仍落到 **10-24** |

→ 服务端按"收到请求的那一天"归日, 日期 key / body.timestamp 都不生效。
**结论: 历史回填协议层不可能**, Kavita→掌阅 只能当天推 (每小时/按需增量均可)。
另: 服务端按分钟位图求并集, 同一天重复推同一批分钟不翻倍 (幂等), 已验证 (8→15→16 分钟逐次 +1/+7 不重复)。

---

## 3. 双向可行性 (重新评估)

| 数据 | 掌阅 → Kavita | Kavita → 掌阅 | 备注 |
|---|---|---|---|
| 划线 | ✅ 位置可精确反解 (字节坐标 + kpages sidecar) | ✅ uploadData 无签名门槛 (待真机确认 upsert) | 删除双向: delMarkOrNote / 本地表登记 |
| 书签 | ✅ 自带 markpercent | ✅ 同上, 字段最少 | 最简单的一类 |
| 批注(想法) | ✅ summary+remark (remark 挂在划线上) | ⚠️ 若 Kavita 注释无对应选区, 只能挂到"划线+remark"形态 | 不造 bookScaleNotes, 统一走 booknotes+remark |
| 手写笔迹 | ⚠️ base64 笔迹 | ❌ | 不做 |
| 阅读时长 | ✅ **按天每书精确** (`read_report/book`) | ⚠️ 可写但要**当天**, 不能补历史 (§2.5) | 见 §2.3~§2.5 |
| 阅读进度 | ✅ 已做 (readPercent 公式) | ✅ 已做 (push) | — |

**结论: 双向都能做到"分钟"级精度** —— 写(掌阅)用分钟位图; 读(掌阅)用
`read_report/book` 的逐日 `read_minutes`。唯一不对称是**写只能写当天**, 历史回填做不到 (§2.5)。
(上面 2.3 那一版"按天每书不可得"的结论已在 2026-10-24 推翻, 见 §2.3 新内容。)

---

## 4. 阅读时长的归属策略 (2026-10-24 更新: 不再需要分摊)

`read_report/book` 打通后, **掌阅 → Kavita 的时长是精确值** (mode=`book`),
T1/T2 的分摊方案降级为"接口取不到该书的逐日数据时的兜底":

* **主路径 `book`** — `read_report/book?book_id=<MD5>_local` → 每天每书分钟, 直接落 Kavita 会话。
* **兜底 `alloc`** — 该书 key 取不到数据 (比如书被重新打包导致内容 MD5 变了, 见藤井三卷)
  时, 退回周级 `reading_longest_books` 按天总量比例分摊, 并在 report 里标 `mode=alloc`。
* **兜底 `exact`** — 兜底里若整周只有一本有映射的书, 天总量全归它。

**写方向限制**: 只能写"当天"(见 §2.5) + 位图并集幂等 → Kavita→掌阅 必须
当天跑 (auto 钩子/定时), 不能补历史。两端口径因此天然不对称, 但都不是估算:
读回来的每天每书数是掌阅自己的原始值。

---

## 5. 幂等 / 冲突 (双向必须有)

两侧都要有 link 表, 否则回灌会自己打自己:

```
zy_note_link(kind, zy_book_id, zy_key, kavita_series_id, kavita_chapter_id,
             kavita_annotation_id, marktime, source, updated_at)
    kind: note | bookmark
    zy_key: uniquecheck (掌阅侧身份)
    source: 'zy' (来自掌阅) | 'kv' (来自 Kavita)
zy_time_link(day, zy_book_id, kavita_series_id, minutes, source, mode)
```

* 推送时只推 `source='kv'` 或 marktime 比上次同步更新的行 → 不把掌阅自己刚同步下来的再推回去
* 删除: 掌阅 `delList` → 删 Kavita 对应行 (只删登记过的); Kavita 删了 (行消失) →
  对该 uniquecheck 发 `delMarkOrNote`
* marktime = 毫秒时间戳, 两侧都用它做 LWW

---

## 6. 实测记录 (本次)

* `getUserNotebooks` ✅ (gzip 应答! `res` 是列表, bookid 为 **32 hex 小写** = 云书 64 hex 的前半段)
* `getUserDataVer?bookids=` ✅ 返回三组版本表 (剑来: notes 17 / bookmarks 3)
* `downloadDataV2` ⚠️ **本次全部返 200 + `{"msg","fromType","status":0}` 无 `res`**,
  换 md5 (哨兵/空数组/随机/大写) 与 Content-Type 都一样; 再连发几次后变 403 (疑似风控)。
  → 上一份文档里"实测拿到 17 条"的记录**未能复现**, 按"待复测"处理。
* `read_report/statistics` ✅ (需完整 7 天窗口)
* `read_report/book` ❌ (参数名对, 返空)

待办: 等风控冷却后复测 downloadDataV2; 若长期 403, 走 App 侧"另存为文本"或旧接口
(`/zybk/api/note/index?act=GetList`, `/v1/download` 在 ebook.ugc.corp3g.cn)。

---

## 7. 本轮实测 (2026-10-24 夜) —— 双向各自能到哪一步

代码: `sync/sync/zy_notes.py` (划线/书签) + `sync/sync/zy_time.py` (时长)
CLI: `python3 zync.py notes {sync|pull|push} [--series N] [--dry-run]`
     `python3 zync.py time  {sync|pull|push} [--day D] [--start D --end D] [--dry-run]`
测试: `python3 tests/test_notes_time.py` (20 项全过, 含剑来真实数据往返)

### 7.1 笔记上传 (Kavita → 掌阅)

| 动作 | 结果 |
|---|---|
| `uploadData?isCheck=1` + 1 条合成划线 | **成功**: `{"code":0,"msg":"ok"}`, 之后 `getUserDataVer` 从 17 条变 18 条, 新 uniquecheck 在列 ✓ |
| 同一次 payload 里 `delList` 也含该 uniquecheck | **失败** `600001 authority is error!` (两者不能共存, 不要"边删边建") |
| `delMarkOrNote` type=1 | 返回 ok 但**不删** |
| `delMarkOrNote` type=2 | 返回 `{"status":0,"res":"success"}` 且条目**真的消失** (18→17) ✓ |
| 复测 (插入→删除之后的任何 upload, 包括 `books:[]` 空载荷) | 一律 `600001 authority is error!` |

→ 结论: **写通道的协议是对的** (已经用真账号做过一次插入 + 一次删除, 用户原有的 17 条一条没少),
  但账号的写权限随后被服务端拒了 (`authority is error`)。读接口同时正常, 说明不是登录失效。
  最可能是风控 (短时间多次写/删) 或写接口需要 `zysid` 重新握手。**下一步: 重新扫码登录刷新 zysid 后再试。**

### 7.2 笔记下载 (掌阅 → Kavita)

* `getUserNotebooks` ✅ (gzip), `getUserDataVer` ✅ (return 17 notes / 3 bookmarks for 剑来)
* `downloadDataV2` **全部返回 `{"status":0,"msg":"downloadDataProcessNew finished!"}`, 没有 `res`**
  —— 试过: 哨兵 md5 / md5("[]") / 随机 md5 / 大写 / 带 `bookids` 参数 / 带 `zyeid` / 三种 Content-Type
  / 完整镜像 VER 结构 / `getUserDataVer` 原样 md5。全部为空。
* 客户端代码路径 (`hg/t.g()` → `hg/g.java`) 确认请求结构与我们的实现一致,
  所以**不是"我们拼错了"**, 更像服务端按设备增量 (它认为这台设备已经是最新的)。
* 影响: `zy_notes.pull()` 会把这种情况记进 `report["empty"]` (`cloud_notes: 17`) 而**不静默**,
  等下载通道打通即可直接落 Kavita 注解表 (位置→页→xpath 链路已用剑来真实笔记验证:
  `chapterId 106 / f3 66812` → 页 568 → `//body/div[1]/p[19]` → 页内含原文, 反向也能精确回原 position)。

### 7.3 阅读时长 (Kavita → 掌阅) ✅ 打通

```
POST https://uc.ireader.com/postal/reading_time/report  (gzip)
{"usr":..., "data":"<json>", "timestamp":"<字符串毫秒>", "sign":""}
data = {"20261024":[{"bid":0,"local_bid":"<32hex>_local",
                     "read_percent":0.2735,"res":"R","d":"<base64 180B 位图>"}]}
```
* **`timestamp` 必须是字符串** (数字会报 `cannot unmarshal number into ... of type string`)
* `sign` 留空可以通过 (服务端不校验)
* 实测: 推 2 分钟 (03:00-03:01) → 本周报表 `read 121→123`, 当天 `9→11` ✓
* **但**: 该书周榜分钟数没变 (剑来仍 30) —— 说明 `bid` 才是**按书归属**的键, `bid=0` 只记进账号总量。
  掌阅的数字 bookid 是**设备本地 DB 自增 id**, 云端 API 拿不到 (bookshelf 只有 64hex),
  所以目前只能做到"账号级精确 + 书级不明"。要在书级生效, 需要从真机 DB 捞一次 bookid 映射。

### 7.4 阅读时长 (掌阅 → Kavita) ✅ 可跑, 但只能分摊

* `read_report/statistics?date_type=week&start_date&end_date` **必须给完整周一~周日**, 否则全 0
* 返回: 每天账号总分钟 (精确) + 该周每书分钟 (top10, 周级)
* `read_report/book?book_id=` 参数名对但返回空 book_info → 单书按天拿不到
* 实现: `zy_time.pull(start, end)` 按"该书周分钟 × 当天总量/周总量"分摊 (mode=alloc, sum 守恒),
  整周只有一本时直接标 mode=exact; 分摊不了的量记进 `unattributed_minutes`。
* 实测 2026-10-19~25: 天总量 `{19:9,21:11,22:31,23:61,24:9}`, 剑来 30 / 狐 14, 未归属 77 分钟。

### 7.5 双向边界 (写进结论)

| 项 | 掌阅→Kavita | Kavita→掌阅 |
|---|---|---|
| 划线 | 协议就绪, 等服务端肯给 downloadDataV2 数据 | 协议验证过 (插入+删除都做过真机验证), 当前被服务端 600001 拦, 等重新登录 |
| 书签 | 同上 | 未实推 (type=1 待验) |
| 阅时 | 可按天精确 + 按周书级 + 分摊到天 | 账号级已验证; **书级需要设备本地数字 bookid** |

### 7.6 建议顺序

1. 用户在 App 里**重新扫码登录** (刷新 zysid), 然后跑 `zync.py notes push --dry-run` 确认能通
2. 从真机拉一次 `books` 表 (`/data/data/.../zhangyue.db` 的 bookid ↔ 书名) —— 有了它阅时才能书级归属
3. 笔记下载再测: 换一个新设备 id, 或在真机上"导出笔记"触发一次服务端增量
