# readProcess.totalCount 到底是什么 (2026-10-24 从 APK 反查)

结论先说:

> `totalCount` = **掌阅阅读引擎的「目录章数」(catalogCount)**，
> 即 `readPosition` 里 `chapterId` 的合法范围是 `0 .. totalCount-1`。
> 不是页数，不是 spine 长度，也不是"已下载章节数"。

## 证据链（jadx 反编译，`apk/jadx/sources`）

1. **写入端**：`kd/h.java` 上报进度时
   ```java
   com.zhangyue.ReadComponent.CommonLayer.JniEngine.Book.b bVar = gVar.b;
   int K = bVar != null ? bVar.K() : 0;      // ← totalCount
   new g(bookId, curPositon, percent, K, positionChapIndex, ...).d();
   ```
   `kd/g.java:60` 把它塞进 payload：`readProcessBean.totalCount = String.valueOf(this.A);`

2. **`K()` 的实现**：`.../JniEngine/Book/l.java:310`
   ```java
   public int K() { return this.f48923g.getCatalogCount(); }
   ```
   `com/zhangyue/iReader/JNI/core.java:191` 是 `private native int getCatalogCount(long)` ——
   原生层（libUiControl.so）返回目录条目数，JNI 层只做了转发。

3. **目录条目**：`l.java:318 L(true)` 用 `for (i=0; i<catalogCount; i++) getCatalogItemByPositon(i)`
   拼出 `ChapterItem` 列表 —— 所以 catalog 就是"章"这个维度，
   下标 `0..catalogCount-1` 与 `chapterId` 同域。

4. **同名值也进 BookHolder**：`qc/a.java:532`
   ```java
   this.f74645r.F().mChapterCount = this.f74645r.K();
   ```
   于是书架"第 N 章 / 还剩 N 章"这类文案直接由它决定：
   `pf/z.java:1454 Q0(i10=totalCount, i12=chapterId, ...)`
   ```java
   int i13 = i12 + 1;                         // 显示"第 (chapterId+1) 章"
   ... String.format(f54898f3, i10 - i11)     // 还剩 totalCount-chapterId
   ```
   `R0()`（漫画分支）也是 `i10 - i11`（totalCount - chapterId）。

5. **顺手确认 chapterId 是 0 基**：`bd/h0.java` 取章节文件名时是
   `getSerializedEpubChapPathName(bookId, chapId + 1)`，注释也写 `chapId + 1`。

## 那我们该写多少？——用真实数据反推

App 会自己写这个值（`K()`），所以我们唯一可能错的场景是**我们代写**的时候。
拿 App 亲手写的值当标尺：

| 书 | spine | NCX navPoint | App 记的 totalCount | 说明 |
|----|-------|--------------|--------------------|------|
| 狐的故事 | 9 | 8 | **8** | NCX 首条就是 cover.xhtml，所以"NCX 条数"本身就等于 spine-1 |
| 剑来 | 590 | 588 | **588** | spine-1 = 589 ≠ 588 → **spine-1 是错的** |
| 藤井 卷02 | 193 | 无（epub3） | **192** | 封面被剔 |
| 藤井 卷03 | 193 | 无（epub3） | **192** | 同上 |
| 藤井 卷01 | 195 | 无（epub3） | 194（预测） | 同上 |

判定顺序（`sync/zpct.py: catalog_count()`）：

1. 有 NCX → `navMap` 里 `<content src>` 的条数（有序去重）
2. 没有 NCX → spine 去掉开头最多 3 篇里名字像 cover/titlepage 的文档
3. 都不成立 → 0，调用方退回 `spine-1`

`sizes` 仍是**全 spine**（含封面）——`readPercent` 的分母把封面也算进去了，
这一点与 `totalCount` 是两件不同的事，不要混。

## 我们以前错在哪

- `BookGeometry.total_count` 曾经直接 `len(sizes)-1`：
  剑来会写 589（App 是 588），多 1。
- 卷级映射（多卷系列）走"无几何"兜底路径时，`total_count` 直接取映射表的
  `kavita_chapter_count`（=1），于是把 **卷01 的 totalCount 写成 1**，
  而同时写 `chapterId=1` —— `chapterId` 已经贴住 `totalCount` 的上界，
  书架文案会显示"第 2 章 / 共 1 章"这类自相矛盾的值。

所以现在 `push_once` 用 `_total_count_for()`：

1. **掌阅现有值优先**，只要它自洽（`>= 本轮 chapterId+1` 且 `>= 2`）
   —— App 自己写的最准，我们算的可能差 1~2。
2. 其次用 epub 的目录章数（上面 1/2/3）。
3. 最后才是映射表旧值。

并且 `push_decision(..., meta_stale=)` 允许"进度不变、只修元信息"写一次：
否则 `totalCount=1` 这种错值会因为 `pct` 一致被判 `noop` 而永远修不掉
（回退闸仍然优先，不会借这个口子把掌阅拨回去）。

## 实测（2026-10-24 19:36）

```
路邊的藤井 - 卷01   chapterId 1 → 22   totalCount 1 → 194   pct 0.11738767
剑来              pct 0.27346 → 0.27378714 (页内)          totalCount 588 保留
狐的故事 / 卷03    noop（两边已对齐：0.48991445 / 0.98467165 逐位相同）
卷02              未读，不动
再跑一轮           全部 noop（幂等）
```

## 另一个坑：书名对不上文件名 → 拿不到几何

云书多卷叫 `路邊的藤井 - 卷01`，文件却是 `路邊的藤井/路邊的藤井 卷01.epub`。
`config.find_zy_epub()` 原来只做 `<书名>.epub` 精确匹配 → 三卷全都返回空 →
push 掉进"无几何"分支（`pct = Kavita 百分比`、`chapterId = anchor`、
`totalCount = 映射值`）。修法是分隔符归一 + 递归找子目录。

教训：**"没有几何"不等于"书不存在"**，它只是悄悄把精度降到"章级比例"，
表面上还能跑。改完以后每次 push 都打印 `geo=True/False` 值得盯一眼。

## 复核（2026-10-24 19:45，线上读回）

把预测值写进掌阅后再读回来对账 —— 这一格从"预测"变成"已验证"：

```
卷01  totalCount=194  chapterId=22  pct=0.11738767  pos=23_0_0$##3-0-23-0-0##$
卷03  totalCount=192  chapterId=189 pct=0.98467165  pos=190_0_0$##3-0-190-0-0##$
卷02  totalCount=192  chapterId=0   pct=0.0         （未读，保持 0）
```

三点确认：

1. **卷01 的 194 命中**：spine 195、无 NCX → `catalog_count()` 剔掉开头
   cover 得 194，写进云端后 App 原样存储（它没有回写纠正成别的数）。
   与卷02/03 的「spine-1」同构，说明 epub3 无 nav 时剔封面这条规则是对的。
2. **`chapterId` 必须 ≤ totalCount-1**：卷01 的 22、卷03 的 189 都落在
   上界内；之前 chapterId=1 + totalCount=1 的组合是越界态（"第 2 章 / 共 1 章"）。
3. **幂等**：紧接着再跑 `zync.py push --dry-run`，四本全 `noop`
   （0.489914 / 0.273778 / 0.984672 / 0.117388），没有来回抖。

### 以后再遇到"掌阅那边显示不对"的排查顺序

1. 先读回云端真值（`zy_client.get_progress`），不要看我们自己写的映射表：
   ```
   totalCount / chapterId / readPercent / readPosition 四个一起看
   ```
2. `chapterId >= totalCount` 或 `totalCount == 1` → 几乎必然是"没有几何"的
   兜底路径写进去的，查 `geo=True/False`。
3. 只差 1（比如 589 vs 588）→ 查 `catalog_count()` 有没有走到 NCX 分支。
4. `readPosition` 的 `f0` 不是 3 → 不是 App 写的，是我们伪造的旧记录。
