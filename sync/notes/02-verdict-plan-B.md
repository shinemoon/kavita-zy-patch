# B 方案裁决 (2026-10-23)

## B 方案是什么

用 Kavita 侧的 `AppUserProgresses.BookScrollId` (xpath) 反推掌阅侧的
`readPosition`, 用于**反向**同步 (Kavita → 掌阅)。
(正向 掌阅→Kavita 不需要它, 靠 `readPercent` 折页。)

## 裁决: B 方案不可行 —— 但顺带挖出一个真 bug

### 一、B 方案本身: 不可行

`scripts/verdict-b-xpath.py` 跑全部 7 本有 xpath 的书:

| 书 | spine | xpath | 命中几篇 |
|---|---|---|---|
| 海边的卡夫卡 | 8 | `//body/div[1]/p[124]` | **6** |
| 没有色彩的多崎作 | 21 | `//body/p[47]` | **17** |
| 夏帆 | 15 | `//body/div[1]/p[4]` | 6 (PagesRead 那篇没有此 xpath) |
| 九诗心 | 24 | `id("footnote-5-37")` | **0** |
| 紫云村 | 104 | `//body/div[5]/p[18]` | **80** |
| Adrian Mole WMD | 6 | `//body/h3[49]` | 2 |
| 狐的故事 | 9 | `//body/div[1]` | **9** |

两个必要条件都不成立:
1. **xpath 不唯一** — 最多 80 个候选。"p 序号上限"判据全部不收敛
   (紫云村 80 篇里 p 数从 18 到 218 全满足 ≥18)。
2. **Kavita 页序与掌阅字节几何不同源** — 强行用 `PagesRead` 当 spine 下标,
   算出 pct 再乘 `Chapter.Pages` 折回页码, 7 本里 5 本差 2-3 页。

唯一能消歧的独立判据是 `PagesRead` 本身, 而它正是要反推的量 → 循环依赖。

**附带发现**: 九诗心 (PDF 转 epub) 的 xpath 是 `id("footnote-5-37")`,
不是元素路径 → koreader 对非标准 epub 的格式也不统一, 这种书任何方案
都得单独处理。

---

### 二、真 bug: pull 折页用了错误口径 (已修)

诊断 B 方案时顺手验了**正向** pull 的折页公式, 发现它是错的:

    pages_read = round(pct01 * Chapter.Pages)     ← 错

这把"字节几何"当成了"线性页序", 两者完全不同。

实测对照 (狐的故事, `Chapter.Pages=9`, spine=9):

| 口径 | 值 |
|---|---|
| 官方 chapterId | 5 |
| 官方 readPercent | 0.29326823 |
| 官方 f3 | 11752 |
| `round(0.293 * 9)` = **现公式** | **3** ← 错, 29% 读成 33% 页 |
| `geo.solve(pct)` = **正解** | **5** ← 精确还原 |

### 修法

`zpct.BookGeometry.solve(pct01)` —— `percent()` 的逆函数, 走字节几何定位。

### 官方语义的三个坑 (都是测试逼出来的)

1. **chapterId 不等于 spine 下标**。公式里 chapterId 那篇算"已读完",
   f3 落在 `spine[chapterId + 1]` 内:
   `0 <= f3 < sizes[chapterId + 1]`
   狐: ch=5 → cum[5]=84191, f3=11752 → 95943 落在 spine[6] 区间内。

2. **k 的判定用严格不等 `cum[k] > target`**。target 落在
   `(cum[k-1], cum[k]]` 半开区间时 k 是 spine 下标。f3 必须**严格小于**
   本篇长度。

3. **容差必须相对, 不能固定 1 字节**。pct 是 float32 (24 位尾数),
   反解回字节误差上界 `total * 2**-23` (狐 0.039 / 藤03 0.013 字节,
   实测 0.003)。固定 1 字节会在 "f3 = 下一篇末字节" 时越界到 ch+1。

另外 `pct <= 0` 走**未读置零硬规则** — 藤02 官方记 ch=0/f3=0/pct=0.0,
几何算出 0.0049, 那不是误差, 是"未开始"。

### 验证

`sync/tests/test_zpct.py` 12 项全过 (原 9 项 + 新增 3 项):

- `test_solve_recovers_official_chapter_id` — 4 本 App 真实记录
  (含未读的藤02) 的 chapterId 与 f3 **精确还原**
- `test_solve_percent_roundtrip` — 15 组 (chapterId, f3) 往返全中
- `test_solve_monotonic` — 201 个采样点字节位置单调不减

代码: `sync/sync/zpct.py` 加 `solve()` + `import bisect`;
`tests/test_zpct.py` 加 3 项;
`sync/sync/sync.py` pull 折页改走几何 (无 epub 时仍退回旧公式)。

### push 侧无需改

`_push_chapter_from_kavita` 早就是几何法 (1 spine 文档 = 1 页,
`PagesRead` 直接当 spine 下标)。所以 push 无回归, dry-run 确认:
狐 ch=2/pct=0.014, 剑来 ch=589/pct=1.0。

### 未 push

`/vol2/1001/zhangyue-bridge/sync` 本地 git 无 remote。改动留在 working tree。