# 全景说明 (2026-10-23 17:20)

## 你的问题: pull 25.7346% 已写 db, 为什么 Kavita UI 没变?

**答: 写进去了, 而且 UI 也已经反映了 —— 但那个值跟之前一样, 所以你看着像"没变"。**

证据链:

| 环节 | 值 | 怎么验的 |
|---|---|---|
| 掌阅云端 | `readPercent=0.257346` `chapterId=5` `readPosition=6_0_0$##3-0-6-0-0##$` `readingSource=app` | `getProgress` 原样返回 |
| 桥接 pull | `geo.solve(0.257346)` → `chapterId=5, f3=0` | zpct 逆函数 |
| 写入 Kavita db | `PagesRead=5` `LastModifiedUtc=2026-10-23 08:22:36` | sqlite 直读 |
| Kavita API | `GET /api/series/26` → `pagesRead:5, latestReadDate:2026-10-23T08:22:36` | 带 X-Api-Key 实打 |
| Kavita UI | 顶部进度条 **56%** (= 5/9), "继续 狐的故事" | Playwright 抓 DOM |

56% = round(5 / 9 * 100) = 55.6%。**Kavita 的进度条是 `pagesRead / chapter.Pages`,
不是 `readPercent`**。

所以 "没变" 的真因: **PagesRead 从 5 → 5**。
16:08 那次 pull 掌阅是 29.844016% → solve → chapterId=5 → 旧代码 `pages_read = spine_idx = 5`
16:21/16:22 那次是 25.7346% → solve → chapterId=5 → 旧代码同样是 5。

**两次算出同一个 5, 所以数值一模一样, 看起来像没生效。**

## +1 修正为什么没体现

`sync.py` 里 `pages_read = zy_chapter_id + 1` 那行是 **16:34:59** 才落盘的
(`stat -c %y sync/sync.py`)。你在 **16:21 / 16:22** 点的 pull 跑的是旧代码
(`pages_read = spine_idx`, 没有 +1)。所以那两次写进去的都是 5 而不是 6。

标尺已用绝对字节位置钉死 (notes/03):

    spine sizes  : [433, 1233, 2978, 2781, 1341, 75425, 80464, 69630, 92866]
    cum          : [433, 1666, 4644, 7425, 8766, 84191, 164655, 234285, 327151]

    掌阅 chapterId=5 f3=0  → 绝对 84191 = cum[5]        → 位置在 spine[6] 开头
    Kavita PR=5 xp=p[15]   → 绝对 11513 落在 spine[5] 区间 (8766, 84191]

**差 1 篇, 确认 +1 正确。**

## 现在该做什么

1. **重启 kavita-zy-patch 的 api_server** —— 它在 10-22 05:56 起的,
   `bridge()` 走 `sys.path.insert` + `from sync import ...`,
   首次 import 后模块缓存在进程里, **新的 `+1` 代码它看不到**。
   这就是"改了 pull 但走 UI 按钮仍旧值"的成因。

2. 跑一次 pull 应该看到 `PagesRead 5 → 6` (UI 56% → 67%)。

3. `tests/test_zpct.py` 的 `test_kavita_pagesread_equals_spine_index` 挂了 ——
   断言写的是"取第一个命中的篇号", 但 xpath 在多篇 spine 里重复
   (狐的故事 `//body/div[1]/p[15]` 命中 [3,5,6,7,8])。判据应改成
   "命中集合**包含** PagesRead", 且 `//body/div[1]/p[15]` 那个断言跟
   标尺书不匹配 (那本书实测 spine[3] 才是 BookScrollId 所在)。