# ZhangYue ↔ Kavita 进度同步 (scaffold)

直接 sqlite 同步掌阅云端的阅读进度到本地 Kavita db。

## 安装与凭据

落点: `/vol2/1001/zhangyue-bridge/sync/`

依赖:
- Python 3.11+ (std-lib only, 不需要 pip)
- 掌阅凭据 = `/vol2/1001/zhangyue-bridge/.auth.json` (usr/zysid/p29/p2/p3/p4/p33)
- Kavita db 路径: `/vol1/1001/APPS/kavita/kavita.db`

> 掌阅 zysid 是 web 扫码 token, 不是浏览器 cookie. **不要**尝试用 PC 端 cookie 调 cloud API.
> token 失效 (服务端滚动) 时重新扫码, 见 `../scripts/poll-live.mjs`.

## CLI

```bash
cd /vol2/1001/zhangyue-bridge/sync

# 加映射 (首次必须先 link):
python3 zync.py link --kavita-series-id 21 \
  --zy-book-id 70776A64C32C1C065DAB241F3E9F133A55C7D8B38C5C6FBC79271E87E00E9526 \
  --zy-book-name '剑来（1-54册）完结精校版' --kavita-chapter-count 1

# Kavita -> 掌阅 (dry-run 不会真推)
python3 zync.py push --dry-run
python3 zync.py push

# 掌阅 -> Kavita
python3 zync.py pull

# 看两侧实时对齐
python3 zync.py status
python3 zync.py links

# 后台轮询 (push 30s, pull 5min)
python3 zync.py daemon --interval 30
```

## 进度语义

| 字段 | 含义 |
|------|-----|
| `pages_read / total_pages` | 当前章阅读百分比 |
| `total_reads >= 1` | 当前章至少读完一次 |
| `chapter_id` | 掌阅侧私有章号 (字符串, 无 spec); 写到 mapping 表作锚定 |

Kavita `AppUserProgresses.PagesRead = Chapter.Pages` 表示**完全读完一章**.

### 页号口径: 重排版让两边同构 (2026-10-23 定案)

Kavita 对 epub 是 **1 篇 spine 文档 = 1 页**, 书架百分比由前端算
`PagesRead / Chapter.Pages`, reader 也拿 `PagesRead` 直接取
`spine[PagesRead]` —— 百分比和续读位置**绑在同一个数上**。

原书各篇字节数极不均: 狐的故事 9 篇里, 封面/制作信息/简介×2/目录 这 5 篇
只占 **2.7%** 字节却占 **55%** 的页。所以掌阅 48.96% → `round(0.4896×9)=4`
→ 书架 44%, 打开是**目录页**。这不是公式能救的, 是"页"的定义问题。

**解法**: 把库里的 epub 换成"等字节切片版"(`kpages.py` 生成):

- 整本按原始字节均匀切 N 页 (`N = clamp(总字节/4096, 100, 600)`), 切点吸到
  标签边界; 每页重新包成独立 XHTML (合并 head 里的 css/link, 跨文档续页会
  重开未闭合的包裹元素, 链接/TOC/NCX 全部按新页号重写)
- 于是 `PagesRead / N` **就是**掌阅的字节百分比, 误差 ≤ 1 页
- sidecar `state/kpages/<series>.json` 记 `页号 ↔ 原书全局字节区间`,
  pull 用 `page_of_pct()`, push 用 `bytes_of_page()` + `zpct.locate()`
- 原书归档到 `bridge/books/`, 掌阅的 readPercent 口径永远拿它算

实测:

| 书 | 掌阅 | 重排前 Kavita | 重排后 Kavita | 打开位置 |
|----|------|---------------|---------------|----------|
| 狐的故事 | 48.96% | 4/9 = 44% (目录页) | 48/100 = 48% | 该页正文 = 48.96% 处的正文 |
| 剑来 | 27.185% | 160/590 = 27.1% | 163/600 = 27.17% | 同 |

老的 `ZY_PULL_PAGE_MODE` (percent/position) 只对**没有** sidecar 的书生效。

```bash
# 给一本书重新排版并替换库内文件 + 重扫 Kavita
python3 tools/apply_edition.py --series 26 --dry-run   # 只构建自检
python3 tools/apply_edition.py --series 26             # 换库 + 重扫
python3 tools/apply_edition.py --all                   # 所有已映射的书
```

回滚: `state/backup/<书名>.epub.bak-*` 覆盖回 `ZY_EPUB_DIR` 后重扫。

pull 以掌阅为权威: 本地比掌阅靠前也照样覆盖 (只在 pct 解析失败/算成 0 页时跳过)。

## 进度口径: 唯一的权威定义 (2026-10-23)

两套系统的"进度"是**两个不同的量**, 必须先把定义钉死, 再谈换算:

| | 定义 | 粒度 |
|---|---|---|
| 掌阅 `readPercent` | 全书字节位置 / 全书字节数 (0.0-1.0) | 字节 |
| Kavita 书架百分比 | `PagesRead / Chapter.Pages` (前端自己算) | 1 页 |
| Kavita 续读页 | `spine[PagesRead]` — **百分比和位置是同一个数** | 1 页 |
| Kavita 保存 | 当前 `pageNum`; 停在最后一页时写 `pageNum+1` (= 100%) | 1 页 |

我们重排版把每页切成**等字节片** (sidecar `pages[i].s/e`), 所以两边的换算就是:

```
pct      → PagesRead = 含该字节的那一页 (0 基, 向下取整)   ← 位置精确
PagesRead → pct      = 该页起点字节 / 全书字节              ← 位置精确
finished            → PagesRead = N (= 100%)
```

**不要**用 `round(pct*N)`: 48.96% 会被抬到第 49 页, 打开后位置超前最多一页。
代价是 Kavita 显示"读完几页"(48%) 比掌阅的字节比例 (48.96%) 少不到 1 个点 ——
这是两边定义不同, 不是 bug; N 越大差越小 (剑来 600 页 → 0.17 点)。

页内再加 **BookScrollId** (`//body/div[1]/p[23]`): pull 会把掌阅的字节位置换算成
当前页内的元素 xpath, 续读直接落到同一段落, 而不是页首。写之前做反解自检;
Kavita 前端 `scrollTo()` 找不到元素只 `console.warn`, 不会像坏 NCX 那样整章打不开。

### push 只在"真前进"时写

掌阅是读者真正在用的那端, Kavita 是镜像 (落后了 pull 会拉上来)。所以 push 带闸
`push_decision()`: 回退超过半页 → 跳过; 差半页以内 → noop; 掌阅已读完 → 不降级。
默认关闭回退 (`ZY_PUSH_ALLOW_REWIND=1` 才允许, 用于"我在 Kavita 里故意重置")。

没有这道闸时, 每次 push 都把掌阅拨回"当前页开头"—— 48.96% 和 47.98% 之间来回跳。

例外 (`meta_stale`, 2026-10-24): 进度一字不差、但 **`totalCount` / `chapterId`
写错了**, 允许"进度不变"地写一次修元信息, 否则错值永远被 noop 挡住。回退闸仍优先。

### `readProcess.totalCount` 的口径 (apk 反查, 2026-10-24)

`totalCount` = **阅读引擎的目录章数** `catalogCount` (`Book.K()` →
`core.getCatalogCount()`), `chapterId` 的合法范围是 `0..totalCount-1`;
书架"第 N 章 / 还剩 N 章"的文案直接用它。**不是**页数, 也**不是** spine 长度:

| 书 | spine | NCX 条数 | App 记的 totalCount |
|----|-------|----------|--------------------|
| 狐的故事 | 9 | 8 | 8 |
| 剑来 | 590 | 588 | **588** (spine-1 = 589 ✗) |
| 藤井 卷02/03 | 193 | 无 (epub3) | 192 |

所以 `zpct.catalog_count()`: 有 NCX → `navMap` 的 `content` 条数; 没有 → spine 剔掉
封面/扉页文档; 都没有 → 退回 `spine-1`。push 时 `_total_count_for()` **优先保留
掌阅已有的自洽值** (App 写的永远最准), 算出来的只做兜底。
证据与反向测试见 `notes/04-totalcount-semantics.md`。

### 换库前必须自检 (`kpages.validate_edition`)

`build_edition` 写完 zip 立刻校验 manifest / spine / ncx / nav / 正文里的每条内部链接
是否落到真实文件, 有问题直接抛错不换库。2026-10-23 事故就是这么发现的: NCX 的
`<content src="p0001.xhtml"/>` 少了 `kp/` 前缀 (base 目录算错), 文件全在、链接全错,
Kavita 用 VersOne.Epub 解析目录时抛 `content source not found in EPUB manifest`,
整本书"继续阅读"报错跳回详情页。

## 控制台 (5012)

`http://<host>:5012/` — 掌阅二维码认证 / 推拉 / 日志，外加一张 **Kavita 改造** 卡：
补丁自检 9 项 + 注入重打 / 重启补丁后端 / 重排版 dry-run / 重排版执行 / 体检。

容器重建（换镜像）会让前端注入失效，那张卡的红项会直接指出来；
完整 runbook 见 `/vol2/1001/kavita-zy-patch/docs/SETUP.md`。

## 实现关键

- 掌阅接口: `icloud.zhangyue.com/cloud/storage2/safe/getProcess|uploadProcess`
- 必带 7 参数: usr, zysid, p29, p2, p3, p4, p33 (指纹, 全局通用)
- Kavita 0.9.0.2 REST 进度端点不全, 直读 sqlite, WAL mode 走 .backup()
- Chapter 表无 `SeriesId` 列 — 必需 LEFT JOIN Volume 拿 SeriesId
- PK `Id` 单列, 无 UNIQUE(AppUserId, ChapterId) — upsert 走 SELECT/UPDATE/INSERT 两段

## 映射粒度: 整本 vs 卷级

`book_link` 主键是 **(kavita_series_id, kavita_chapter_id)**：

| `kavita_chapter_id` | 含义 | 什么时候用 |
|---|---|---|
| `-1`（`SERIES_LEVEL`） | 整本 —— 一本掌阅书 = 整个 Kavita 系列 | 系列里只有一个文件 |
| 大于 0 | 卷级 —— 一本掌阅书 = 该系列第 N 号 Chapter/卷 | 多卷系列（一个系列 N 个文件） |

为什么必须分：Kavita 的 `PagesRead` 是**卷内**页号，掌阅 `readPercent` 是**整本**
字节比例。一个系列挂三本掌阅书时，三者若共用一条映射行，进度会互相串。

选择顺序（`mapping.resolve` / `push_once` / `pull_once` 一致）：

1. 先按 `(系列, 当前章)` 精确匹配；
2. 没有卷级行才退回系列级 `(系列, -1)`；
3. 两者都没有 → `no link`，不猜、不跨卷兜底（宁可不推，不可推错）。

下载流程自动判定粒度（`epubprep`）：系列里 `series_files` 多于 1 个 → 卷级
（`chapter_for_file` 反查这个 epub 是哪一卷）；单文件 → 系列级。
图片型 / 多卷两种"不重排"的书也走建档，只是跳过切片。

老库（v1 单列主键 `kavita_series_id`）首次打开时自动迁移：老行一律变
`kavita_chapter_id = -1`（保持"整本"语义），多卷书再补卷级行。

手工补卷级映射（`--kavita-chapter-id` 就是 Kavita 的 `Chapter.Id`）：

    python3 zync.py link --kavita-series-id 29 --kavita-chapter-id 36 --kavita-chapter-count 1 --zy-book-id DD2F1C22349AF31D06991F85F62D07057D42CE2F7A1EF527B2727BFF086119DF --zy-book-name '路邊的藤井 - 卷01'

## 已知边界

- 章节锚定目前固定映射表的 `zy_anchor_chapter_id`, 章节推进后 anchor
  不更新 (pull 触发时 update_anchor). 推过去时 chapter_id 用 anchor 字符串,
  percent 反映 Kavita 侧总进度, 不是章节级 — **整章百分比粒度**
- 多卷系列按**卷**各自一条映射（`kavita_chapter_id`）；无卷级行时退回整本。
  app 把 chapterId 当锚（`zy_anchor_chapter_id`），percent 反映该映射行的进度
- 图片型 / 多卷的书没有切片版 → 没有页内精度，粒度就是"页/卷"（图片书约 0.5%/页）
- 没有 EPUB 级别的 diff — 每本 EP 都需要手工 `link` 映射, 没模糊自动匹配

## 待 user 拍板

- daemon 跑: systemd? cron? nohup?
- 每本书 chapter_id 锚定策略: 首次 link 时从掌阅侧 API 读?
- push / pull 频次 vs quota (zy 接口无明确 limit)
- 是否监听 Kavita DB 变更自动 pull (当前只 daemon)

## 掌阅「个人云书籍」管理 (2026-10-24, 重做)

5012 控制台「掌阅个人云书籍」卡片：**只管用户自己上传的书**。

* **列表只列个人云书架**（`is_local_book=true`，自己上传，**无 DRM** —— 狐的故事 /
  剑来 / 路邊的藤井 …）。商店云书（storage2，正版带 DRM）不再展示 —— 那个列表跟
  「管理自己的书」无关，混在一起反而找不到要的那几本。
* 每本可：下载 / 从云端删除；已下载的可搬移、移出清单、删本地文件。
* **已下载的在列表里直接看得出来**（2026-10-24 晚）：「本地」列显示
  `已下载` 徽标 + 本地文件名 / 大小 / 绝对路径；按钮从「下载」变成「覆盖下载」。
  * 覆盖 = **原地写回那个文件**（不再多出 `xxx (1).epub`），云端那份不动。
  * 本地大小 ≠ 云端大小时标 `≠ 云端 xxx（本地已改，可能是重排版版）`，
    点覆盖前会二次确认 —— 狐的故事 / 剑来 在库里是 kpages 重排版版，
    覆盖回原始 epub 会让 Kavita 页数变化、进度映射失效。
  * 本地匹配靠文件名归一化（去空白 / 下划线 / 连字符 / 间隔号），所以
    `路邊的藤井_-_卷01.epub`、`路邊的藤井 卷01.epub` 都能对上同一条云端书；
    下载目录内递归 3 层都算。
* **下载目录只配一个**，界面上直接改（存 `state/cloud_dl_dir.json`，env
  `ZY_CLOUD_DL_DIR` 优先），默认 `/vol2/1001/dav/Books/掌阅`。
  下载与搬移都只能落在这个目录（及其子目录）里，越界直接拒。
  想换目录就改输入框 → 「保存目录」；目录不存在会替你建出来。
* **下载完成自动补 EPUB3 nav**（`tools/fix_epub_nav.py`，见下）：掌阅个人云里
  不少 epub 声明 `package version="3.0"` 却没有 `properties="nav"` 条目，
  Kavita（VersOne.Epub）会抛 `Epub3NavException` 整本读不出来 → 下载时顺手补上。

### 两套链路（都走 api-pc.zhangyue.com，凭据同一套 zysid）

**A. 个人云书架** —— 参数 camelCase（`file_ids`）:

| 用途 | 接口 |
|---|---|
| 书架列表 | `GET /bookstore/open/cloud/bookshelf/list` → `body.current_version` + `body.data[]` |
| 文件签名 | `GET /bookstore/open/cloud/storage/file/info?file_ids=<book_file_id>` → `body[fid].sign_url` |
| 增/删/改 | `POST /bookstore/open/cloud/bookshelf/upsert`（multipart）`usr` + `current_version` + `type` + `data` |

* `type=1` 新增 / `type=2` 删除 / `type=3` 更新（1、2 已实测：插一条 `zy-cli-test`
  立刻出现在列表，再 `type=2` 立刻消失，书架回到 5 本）。
* 服务端按 `current_version` 做**乐观锁**：版本不一致返 `100006 version not equal error`，
  所以删之前必须重读版本（`delete_cloud()` 已做）。
* `data` 元素最小 `{"book_id": "<64hex>", "dtype": 0}`；新增/更新时字段同列表返回的键名。
* 下载：`sign_url` 是 OSS 直链（`service-local-book.oss-cn-beijing.aliyuncs.com/book-prod/…epub`），
  直接 GET 得到**普通可读 epub**（实测 `狐的故事` 371,368 B，18 个 zip 条目）。

**B. 商店云书** —— `icloud.zhangyue.com/cloud/storage2/safe/*`：

| 用途 | 接口 |
|---|---|
| 列表 | `GET /cloud/storage2/safe/getCloudBookListV2` |
| 详情 | `GET /cloud/storage2/safe/getCloudBookInfo?bookId=<id>` → `bookInfo.downloadUrl` |
| 下载二层 | `GET <downloadUrl>` → `{body:{downInfo:[{url}],end}}` |
| 删除 | `GET /cloud/storage2/safe/delBooks?bookIds=<id>[,<id>]` |

下下来的 epub 带 DRM（`META-INF/encryption.xml`），能拿文件但未必能直接读；ebk3 私有格式。

### 上传（往云盘加书）为什么还没做

* `/cloud/bookcase/safe/uploadData` 的 body 是 Thrift 二进制（`uploadDataItems` 字段），
  要补序列化。
* `/bookstore/open/cloud/storage/upload/authorize` 参数名猜不到（任何组合都
  `602001 参数错误`，GET/POST 都一样；PC 前端也没调用它）。
* App 老接口 `/cloud_book/storage/upload/authorize` 需要 `sign`，由 JNI
  `Security.hash(sortedParams)` 生成，本地算不出来。
* 现阶段能做的「增加」只有**恢复已有云文件的书架条目**（`type=1` + 原 `book_file_id`），
  不能把本地新文件推上去。

### 5012 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/cloud/books?refresh=1` | 两源合并列表（`books[].source` = shelf/store）+ 每本本地状态 |
| GET | `/api/cloud/local` | 已下载清单（索引 + 文件是否存在） |
| GET | `/api/cloud/jobs` | 最近下载任务 |
| POST | `/api/cloud/download` | `{key, dest?}` → 后台下载，返回 job；`{key, overwrite:true, target:<下载目录内绝对路径>}` → 原地覆盖；`{prep:false}` → 不重排 |
| POST | `/api/cloud/job` | `{id}` 查任务进度（`nav` 字段 = 补 nav 结果） |
| POST | `/api/cloud/move` | `{key, dest, name?}` 搬移已下载文件 |
| POST | `/api/cloud/local_delete` | `{key, delete_file?}` 移出清单 / 连文件一起删 |
| POST | `/api/cloud/delete` | `{keys:[...], confirm:"yes"}` 删云端（`shelf:` 走 upsert，`store:` 走 delBooks） |
| POST | `/api/cloud/roots_add` / `roots_remove` | `{path}` 维护下载目录白名单 |

`key` 形如 `shelf:<book_id>` 或 `store:<bookid>`；老参数名 `bookid` 仍兼容。

索引 `state/cloud_books.json`：`"shelf:<id>" -> {path,size,name,author,downloaded_at,moved_at}`。
搬移只改文件 + 索引，不动云端；删云端不会删本地文件。


### 下载即重排（2026-10-24）

结论：**要跟掌阅对进度的书，重排不是事后修补，而是入库前的准备**。所以落盘落在
Kavita 库目录里时，「下载」一条流水线做完：

```
下载 → 原书归档 bridge/books/ → 建切片版 (kpages) → 换进库 → 扫库 → 建档 → pull
```

为什么不能"先入库、事后补"：Kavita 的 epub 页粒度 = spine 文档，而书架百分比
（`PagesRead / Pages`）跟"继续阅读"的位置是**同一个数**。原书 spine 常常只有
5-20 篇，封面/版权/目录占前几篇 —— 掌阅 48.96% 就会落成 Kavita `4/9 = 44%`，
而且点"继续阅读"打开的是目录页。中间态就是"看着在同步，其实对不上"。

两条硬约束：

1. **原书字节必须干净**。补 nav 之类的修补只作用在切片版上（`kpages` 会把
   "声明 3.0 却没有 nav"的书 opf 降级成 2.0，见 `opf_version_downgraded`），
   原书保持下载时的字节 —— readPercent 是**字节**比例，往原书里塞 100KB nav
   就是 0.15% 的进度漂移。
2. **不做字节切片的两种书，也必须建档**：
   - 多卷系列（路邊的藤井 三卷）里 Kavita 的 PagesRead 是**卷内**页号，掌阅是
     整本 readPercent → 不重排，但按卷建**卷级映射**（见"映射粒度"节）后照常
     push/pull。
   - 图片型 epub（一图一页，`kpages.image_book_reason`）→ 保持原样，同样建档。

   只重排"一系列一个文件 + 有正文"的书。

任务里有 `prep` 字段（`/api/cloud/job`）：`ok` / `edition` / `skipped`（带 `why`）/
`no_series` / `error`。**失败不动库里的文件**：先建 tmp，`validate_edition` 过才
`os.replace`，换之前旧文件备份进 `state/backup/`。

手动补做（不重新下载）：

```bash
cd /vol2/1001/zhangyue-bridge/sync
python3 -m sync.epubprep --file 狐的故事.epub              # 默认扫库 + pull
python3 -m sync.epubprep --file 某书.epub --no-rescan --no-pull
```

实测（狐的故事 371KB）：下载 → 切片 100 页 → 扫库 100 页 → pull `48.99% → 49/100`，
全程 2 秒。测试：`python3 tests/test_epubprep.py`（10 项，造"3.0 无 nav"样本验降级）。


## `tools/fix_epub_nav.py` — 补 EPUB3 nav（Kavita 读不出来的第一类原因）

掌阅个人云里下的 epub（尤其 Kmoe 出来的漫画）常写 `package version="3.0"`，
manifest 里却只有 EPUB2 的 NCX（`media-type="application/x-dtbncx+xml"`），
没有 `properties="nav"` 的条目。Kavita 用 VersOne.Epub 解析，见到 3.0 就**强制**
要 nav 文档 → `Epub3NavException: NAV item not found in EPUB manifest` →
`Unable to parse any meaningful information out of file` → 书在书架里根本不出现。

```bash
# 单本 / 整个目录（dry-run 先看）
python3 tools/fix_epub_nav.py "/vol2/1001/dav/Books/掌阅/路邊的藤井" --dry-run
python3 tools/fix_epub_nav.py "/vol2/1001/dav/Books/掌阅" --backup-dir ../state/backup/nav-bak

# 修完让 Kavita 重扫
curl -X POST -H "x-api-key: $KAVITA_API_KEY" \
     'http://127.0.0.1:5000/api/library/scan?libraryId=1&force=false'
```

* 生成 nav 时优先用 NCX 里已有的标题（`第 002 頁` 之类），没有就退化成 `第 N 页`。
* `package version="2.0"` 的**不动**：EPUB2 走 NCX，塞 `properties="nav"` 反而不合规。
* zip 重写保留 `mimetype` 在首条且 stored；其余条目原样复制。
* 5012 的「下载」完成后会自动跑一次（`nav` 字段回显 `fixed` / `skip` / `skip(...)`）。
* 网页预览能打开 ≠ Kavita 能打开：浏览器对孤立标签宽容，epub 解析器不宽容。
