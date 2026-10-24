# 从零到可用：给一台（新装的）Kavita 装改造

这套改造的目标只有一个：**Kavita 的进度与掌阅对齐，并且能像掌阅原生那样随手用**。
本文件是 runbook；设计原因见 `../README.md`，进度口径的定义见
`/vol2/1001/zhangyue-bridge/sync/README.md`。

---

## 0. 前提检查

| 项 | 要求 | 检查 |
|---|---|---|
| Kavita 容器 | 名字 `kavita`（可在 `patch.conf` 改） | `docker ps --format '{{.Names}}'` |
| 配置卷 | `/vol1/1001/APPS/kavita` → `/kavita/config` | `docker inspect kavita` |
| 书库卷 | `/fs/1001/ftp/dav/Books` → `/books` | 同上 |
| 宿主权限 | 跑改造的用户（示例里写作 `youruser`）能 `docker`、能读写 `kavita.db` | 见下 |
| 桥接仓 | `/vol2/1001/zhangyue-bridge` | `ls sync/zync.py` |

DB 权限（一次性）：

```bash
setfacl -m u:youruser:rw /vol1/1001/APPS/kavita/kavita.db
setfacl -m u:youruser:rwx /vol1/1001/APPS/kavita   # 目录，WAL 要建 -wal/-shm
```

> 容器重建后 DB 仍是同一份（卷），ACL 通常保留；若自检里 `kavita.db 可读写` 变红，重跑上面两条。

端口：

| 端口 | 谁 | 说明 |
|---|---|---|
| 5000 | Kavita | 页面 |
| 5012 | 桥接控制台 | 掌阅认证 / 推拉 / **Kavita 改造**；同时反代 `/zy/api/*` → 5111（v0.4.6）。**对外只需要它** |
| 5111 | 补丁后端 | 只给本机和 5012 用；浏览器**不必**能直连（能直连更快，断开会自动走 5012 反代） |

> 只有一台机器 + 一个公网端口映射时：映射 5000（Kavita）与 5012（控制台兼 API 入口）即可，5111 不用放行。

---

## 1. 装改造（一键）

**先决定前端怎么落地**（两种都能用，推荐挂载）：

| 模式 | 做法 | 容器重建/换镜像 |
|---|---|---|
| **mount（推荐）** | compose 加两行只读挂载 | 不丢；镜像升级后自愈自动重建 |
| copy | 什么都不加，`install.sh` 自己 `docker cp` | 丢，靠自愈每 1–3 分钟重打 |

挂载模式一次性步骤（**顺序别反**，宿主文件不存在就重建 → docker 会把挂载点建成目录 → 白屏）：

```bash
cd /vol2/1001/kavita-zy-patch
bash install.sh publish          # 生成 state/wwwroot/{index.html,zy-kavita-patch.js}
```

然后编辑 Kavita 的 `docker-compose.yml`，在 `volumes:` 里加：

```yaml
      - /vol2/1001/kavita-zy-patch/state/wwwroot/index.html:/kavita/wwwroot/index.html:ro
      - /vol2/1001/kavita-zy-patch/state/wwwroot/zy-kavita-patch.js:/kavita/wwwroot/zy-kavita-patch.js:ro
```

```bash
docker compose up -d             # 重建
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5000/zy-kavita-patch.js   # 200
bash /vol2/1001/kavita-zy-patch/install.sh status                                   # mode=mount
```

> 本机实际落点：`/vol1/1001/APPS/kavita/docker-compose.yml`（backup：`docker-compose.yml.bak-20261023`）。
> 单文件挂载钉 inode —— 以后只在宿主用 `install.sh publish`（它用 `cat >` 同 inode 覆盖）；
> 若手贱用了 `install`/`mv`，`deploy/reapply.sh` 会比 md5 并 `docker restart` 修好。

打开 `http://<host>:5012/`，找到 **Kavita 改造** 卡片：

- 看自检（容器 / 前端资源 / index 注入 / 补丁后端 / token / kavita.db / 映射 / 重排版 / 掌阅凭据 / **资产版本** / **自启** / **自愈**）
- 红项后面直接写了修法
- **一键改造 / 新装适配** = 注入 + 重启后端 + 装自启 + 自检（新装机点这一个就够）
- **注入 / 重打** = `install.sh install`（幂等，随时可重跑）

CLI 等价：

```bash
cd /vol2/1001/kavita-zy-patch
bash install.sh install      # 注入 + 生成 token + 起 5111 (挂载模式会写 state/wwwroot)
bash install.sh publish      # 只写挂载目录, 不碰容器
bash install.sh status       # kavita / asset / index / mode / api
bash install.sh uninstall    # 精确还原 + 停后端 (挂载模式会提示改 compose)
```

验证：

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5000/zy-kavita-patch.js   # 200
curl -s http://127.0.0.1:5000/ | grep -c zy-kavita-patch.js                          # 1
```

刷新 Kavita 页面（**强刷** Ctrl/Cmd+Shift+R）→ 两个入口出现：

| 位置 | 形态 | 备注 |
|---|---|---|
| 顶栏右侧 | 小圆点 + 图标 + 「掌阅同步」 | 任何宽度都有；窄屏只留图标 |
| 左侧抽屉 | 「掌阅同步 · N 本」 | 手机需先点开汉堡菜单 |

> **看不到入口？** 99% 是 `index.html` 被浏览器缓存（Kavita 的 index 不带
> `Cache-Control`）。强刷一次即可。仍不行 → 看控制台自检里
> 「index.html 已注入」和「前端资产版本」两项，红的话点 **注入 / 重打**。
> 想确认浏览器加载的版本：Kavita 页面 F12 → 控制台输入 `__ZY__.ver`。

---

## 1.5 自启动与自愈

两条路，任选一条（都幂等）：

| 方式 | 需要 | 好处 | 命令 |
|---|---|---|---|
| **crontab**（推荐先装） | 普通用户 | 开机 + 每 1 分钟巡检 | `bash deploy/install-cron.sh` |
| **systemd** | root | 进程崩了自动重启（`Restart=always`） | `sudo bash deploy/install-autostart.sh` |

面板上对应「装自启 (免 root)」和「装自启 (systemd)」两个按钮。
两个都装也没冲突：巡检脚本自己判断进程在不在，不会重复拉起。

crontab 版写的是当前用户的受管块（卸载 `bash deploy/uninstall-cron.sh`）：

```cron
# >>> kavita-zy (managed by install-cron.sh) >>>
@reboot /bin/sleep 30; /bin/bash /vol2/1001/kavita-zy-patch/deploy/heal.sh
*/1 * * * * /bin/bash /vol2/1001/kavita-zy-patch/deploy/heal.sh
# <<< kavita-zy <<<
```

systemd 版是三个 unit：`kavita-zy-api`（5111）、`kavita-zy-web`（5012）、
`kavita-zy-heal.timer`（每 3 分钟 `deploy/heal.sh`）。脚本会先把手工 `nohup` 起的
5111/5012 停掉（端口让给 systemd），再 `daemon-reload` + `enable` + `start`。

**自愈做什么**（`deploy/heal.sh`，幂等，每次跑一遍）：

1. 容器**不存在** → `docker compose up -d` 重建（`docker start` 救不了已删容器）；
   容器存在但停着 → `docker start`
2. Kavita 打不开 → 最多等 60s，超时记异常
3. `zy-kavita-patch.js` 404 或 index 里没注入 → 自动 `deploy/reapply.sh` **重打补丁**
4. 5111 健康检查失败 → 起 unit（没装 unit 就 `ctl.sh start`）
5. 5012 `/healthz` 失败 → 起 unit（没装就 `zyweb.sh start`）

结果写 `state/heal.json`（`ok / problems / actions`），事件写 `state/logs/heal.log`
（512KB 轮转，成功巡检只每小时记一条 heartbeat，不刷屏）。

```bash
bash /vol2/1001/kavita-zy-patch/deploy/heal.sh      # 手跑一次
cat  /vol2/1001/kavita-zy-patch/state/heal.json     # 上次结果
tail -20 /vol2/1001/kavita-zy-patch/state/logs/heal.log
crontab -l | grep -A2 kavita-zy                     # cron 版确认
systemctl list-timers kavita-zy-heal.timer          # systemd 版下次时间
```

**临时关掉自愈**（不卸载，巡检直接跳过）：

```bash
touch /vol2/1001/kavita-zy-patch/state/heal.disabled     # 关
rm -f /vol2/1001/kavita-zy-patch/state/heal.disabled     # 开
```

面板上也有「关自愈 / 开自愈 / 立即自检」三个按钮。

**卸载**（数据不动）：

```bash
bash /vol2/1001/kavita-zy-patch/deploy/uninstall-cron.sh              # crontab 版
sudo bash /vol2/1001/kavita-zy-patch/deploy/uninstall-autostart.sh    # systemd 版
```

---

## 2. 掌阅认证

5012 顶部「掌阅认证」→ **刷新二维码** → 掌阅 App 扫（App 内「扫一扫」，码内容是 `web_login:<code>`）。

成功后凭据写 `/vol2/1001/zhangyue-bridge/.auth.json`（含 usr / zysid / p29 / p2 / p3 / p4）。
失效时自检里 `掌阅凭据` 变红，重扫即可。

---

## 3. 建立映射（哪本 Kavita 书 ↔ 哪本掌阅书）

```bash
cd /vol2/1001/zhangyue-bridge/sync
python3 zync.py link \
  --kavita-series-id 26 \
  --zy-book-id '<掌阅 bookId>' \
  --zy-book-name '狐的故事' \
  --kavita-chapter-count 1
```

也可以在 Kavita 系列页点「连掌阅」建映射（前端 → 5111 `/zy/api/link`）。

自检里 `书籍映射` 应显示 `N 本已连掌阅`。

---

## 4. 重排版（让「页」等于「等字节片」）

为什么必须做：Kavita 对 epub 是 **1 篇 spine = 1 页**，百分比 = `PagesRead/页数`，
续读位置 = `spine[PagesRead]`。原书前几篇（封面/版权/目录）字节极少却占很多页，
于是掌阅 48.96% 在 Kavita 只能显示 44% 且打开落在目录页。换成等字节切片版后，
`PagesRead/N` 就等于掌阅的字节百分比。

在 5012 卡片里：

1. **重排版 dry-run** → 只构建 + 自检（校验 manifest / spine / ncx / nav / 正文链接），不换库
2. **重排版执行** → 换库文件 + 调 `/api/series/scan` 重扫

CLI 等价：

```bash
cd /vol2/1001/zhangyue-bridge/sync
python3 tools/apply_edition.py --series 26 --dry-run
python3 tools/apply_edition.py --all
```

产物：

- 库内 epub 被替换（原书归档 `/vol2/1001/zhangyue-bridge/books/<书名>.epub`）
- 旧版备份 `state/backup/<书名>.epub.bak-<ts>`
- 页映射 sidecar `state/kpages/<series>.json`（页 ↔ 原书字节区间）

回滚：把 `.bak-<ts>` 覆盖回书库路径，再 `POST /api/series/scan`。

### 4.1 页数 = 显示粒度（重要，2026-10-23）

一页 = 一段原书字节片，`N = clamp(总字节 / 4096, 100, MAX_PAGES)`，`MAX_PAGES` 默认 **3000**。

`N` 同时决定两件事：Kavita 的显示粒度（`1/N`），和「翻页要读多久才动一格」。

| `N` | 剑来（41.9MB）每页 | 粒度 | 读多久动一格 |
|---|---|---|---|
| 600（旧默认） | 70KB ≈ 2.3 万字 | 0.17% | ≈ 一小时 |
| 3000（现默认） | 14KB ≈ 4700 字 | 0.033% | ≈ 十分钟 |

2026-10-23 事故：剑来还是 600 页版，用户读了一晚上页号一次没翻 → Kavita 记的进度
一直停在 163/600 = 27.2%，掌阅侧 pct 也一格不动。主因是**页太粗**；次因是当时 push
只读页号、页内位置被丢掉（页内精度在 v0.4.3 修好，见第 6 节）。

调参（默认够用，一般不用改）：

```bash
ZY_KPAGES_MAX_PAGES=3000 ZY_KPAGES_TARGET_BYTES=4096 \
  python3 tools/apply_edition.py --series 27
```

**改页数后必须紧接着 pull 一次**，把 `PagesRead` 从旧页号重映射到新页号，否则
Kavita 显示的是「旧页号 / 新总页数」这种假进度（163/3000 = 5.4%）：

```bash
python3 zync.py pull
```

重排版瞬间会把书架百分比打回低位、再被 pull 拉上来，属正常中间态。

---

## 5. 体检

5012 卡片「体检」= `GET :5111/zy/api/selfcheck`，或 CLI：

```bash
python3 /vol2/1001/kavita-zy-patch/src/selftest.py
```

常见项：`name_mismatch`（连错书）、`unlinked`（读过但没连掌阅）、`token`、`kavita_db_perm`。

---

## 6. 日常

| 场景 | 动作 |
|---|---|
| 读完了想同步到 Kavita | Kavita 页「掌阅同步」→ 拉回（或 `zync.py pull`） |
| 在 Kavita 里读到某处，想让掌阅跟上 | 推到掌阅（或 `zync.py push`） |
| 想全自动 | `python3 zync.py daemon --interval 30`（push 30s / pull 5min，**默认没起**） |

push 带闸：回退超过半页不写、半页内 noop、掌阅已读完不降级（`ZY_PUSH_ALLOW_REWIND=1` 才回退）。

**页内精度（v0.4.3）**：Kavita 只把「读到第几页」写进 `PagesRead`，页内位置写在
`BookScrollId`（xpath，如 `//body/div[2]/p[30]`）。push 会把这条 xpath 还原成
**该页内部的字节比例**，再按掌阅的字节口径算 `readPercent` —— 所以一页之内读到
哪里都能反映到掌阅，不必等翻页。

看日志分辨走没走页内精度：

```bash
grep 'push OK' /vol2/1001/zhangyue-bridge/state/logs/sync.log | tail
# ... pct01=0.272836 (页内)   ← 用了 xpath
# ... pct01=0.272000          ← 只到页起点（xpath 缺失/解析不了）
# skip push ... (noop)        ← 两侧已对齐，不用推
```

页内精度救不了「页太粗」的显示问题：`PagesRead` 仍然是整页跳，想让它动得快
就得把 `MAX_PAGES` 调大并重建（见 4.1）。

---

## 7. Kavita 升级 / 容器重建之后

**挂载模式下不需要做什么**：前端两个文件在宿主 `state/wwwroot/`，重建后依旧在。
只有一个例外 —— 镜像升级（`docker pull` 了新版）时，挂着的 `index.html` 引用的 chunk 哈希在新镜像里
不存在，页面会白屏。自愈每 1–3 分钟验一个 `main-*.js`，取不到就自动从**新镜像**里取 `index.html` 重建注入版。

手动一次：

```bash
bash /vol2/1001/kavita-zy-patch/deploy/reapply.sh   # 判断 + 重建 + inode 漂移修复, 幂等
```

**拷贝模式**（没挂载）：容器重建会把补丁连同 overlay 上写层一起清掉 ——
`zy-kavita-patch.js` 404、左栏「掌阅同步」消失。数据层与库文件不受影响（都在卷上）。
装了自启 + 自愈的话 1–3 分钟内自己回来；不想等就 `bash deploy/reapply.sh`。

> 自愈日志：`state/logs/heal.log`；上次结果：`state/heal.json`。
>
> **重打之后，已经开着的浏览器标签页仍然没有钩子**（那一次页面加载时脚本是 404）。
> 手动刷新一次即可（`Ctrl/Cmd+Shift+R` 更保险）。判断方法：F12 控制台敲 `__ZY__.ver`，能打印版本号才是真的加载上了。

---

## 8. 改造清单：改了什么、脏在哪、怎么回滚

| # | 改的东西 | 落在哪 | 容器重建会丢吗 | 回滚 |
|---|---|---|---|---|
| 1 | 前端注入（导航项 / 入口按钮 / 读书钩子） | 挂载模式：宿主 `state/wwwroot/` → 容器 `/kavita/wwwroot/`；拷贝模式：容器 overlay | 挂载：**不丢**；拷贝：**会** | 删 compose 两行 + 重建；或 `bash install.sh uninstall`（拷贝模式） |
| 2 | 补丁后端 | 宿主进程 `:5111`（systemd `kavita-zy-api`） | 不会 | `bash ctl.sh stop` |
| 3 | 进度直写 | `/vol1/1001/APPS/kavita/kavita.db` 的 `AppUserProgresses`（`PagesRead` / `TotalReads` / `BookScrollId`） | 不会 | 备份 db；或在书页手动改回 |
| 4 | 库文件重排 | `/fs/1001/ftp/dav/Books/` 里的 epub 被替换 + `state/kpages/*.json` | 不会 | `state/backup/*.epub.bak-*` 覆盖回 + 重扫 |
| 5 | 映射表 | `/vol2/1001/zhangyue-bridge/state/sync.sqlite`（`book_link`） | 不会 | 删行 |
| 6 | compose 两行挂载 | `/vol1/1001/APPS/kavita/docker-compose.yml` | — | 删掉那两行（备份 `docker-compose.yml.bak-20261023`） |

**Kavita 的代码和镜像一行没改。** 第 1 项只写它自己的静态目录，第 3 项是 SQL，
第 4 项是它本来就该扫的书库文件。

---

## 9. 排障

| 现象 | 查 |
|---|---|
| 页面没有「掌阅同步」 | 先 **强刷** Ctrl/Cmd+Shift+R（index.html 缓存）；再查自检 `前端资源` / `index 注入` / `前端资产版本` 是否红 → 「注入 / 重打」；F12 控制台看 `__ZY__.ver` 与 `document.getElementById('zy-top-item')` |
| 顶栏有、左侧抽屉没有 | 正常：手机/窄屏抽屉默认收起，点汉堡菜单才看到；顶栏入口一直都在 |
| 有导航项，徽标空白/「离线」 | 自检 `补丁后端` 是否红 → 「重启补丁后端」；F12 看请求打到哪个 base（`__ZY__.auto().base`）。若是 `host:5111` 且被浏览器拒（`ERR_CONNECTION_REFUSED`）= 这台设备到 5111 不通：v0.4.6 起会自动改走 `host:5012/zy/api`，仍不通就查 5012 的端口映射/反代 |
| 5111 报 `bad token` | `state/token` 与页面里 `?v=` 一起重渲染：重跑 install |
| 徽标全不显示 | 没映射记录：`selftest.py` 看 `/zy/api/status` 的 rows |
| 「安装自启」点了报 root | 面板没有免密 sudo → 手跑 `sudo bash deploy/install-autostart.sh`（一次） |
| 自检红「自愈巡检」 | 点「立即自检」看输出；`state/logs/heal.log` 有失败原因 |
| **Kavita 打不开，`kavita.service` 却是 active (exited)，日志刷 `No such container`** | 容器被删了（不是停了）。先 `bash deploy/heal.sh` 手跑一次 → 它现在会 `docker compose up -d` 重建；根治见 README「容器没了」一节：`sudo bash /vol1/1001/APPS/kavita/install-system-unit.sh`（ExecStop 从 down 改 stop，停服务不再删容器） |
| 想知道到底是谁删的容器 | `deploy/watch-kavita.py` 常驻：`state/logs/kavita-watch.log` 记录状态突变 + 可疑 docker 命令（pid/ppid/user/PWD）+ 现场快照，`kavita-events.log` 记 die/destroy 及 exitCode |
| 「继续阅读」报错跳回详情页 | 书内链接坏了：重跑 `apply_edition.py --series N`（构建即自校验）；或回滚 `.bak` |
| **自动 push/pull 像没生效** | 按顺序看三处：① `bash install.sh status` 里 `mode=`；② `state/logs/api.log` 有没有 `hb vX.Y.Z`、`base=` 指向哪个地址、里面的 `saves=` 是否随阅读增长（没有 hb = 这一页连不上后端：强刷 / 后端在重启窗口加载 / 设备到 5012+5111 都不通）；③ 有没有 `auto-pull` / `auto-push` 行，`auto-push ... why=noop` 是 `push_decision` 主动挡（已对齐），`why=regress` 是掌阅更靠前不回拨，都不是故障 |
| 钩子整晚一次没动，但 Kavita 存进度正常 | v0.4.4 修掉的典型：页面在 5111 重启窗口加载 → `/auto/cfg` 失败 → 整页无钩子且不重试。强刷到 `?v=0.4.4` 后自动重试 + 失败也会发心跳，`hb` 行里会写 `auto cfg 读取失败 (第N次)` |
| 手机/HTTPS 打开 Kavita，自动同步全静默 | 页面调不到 `http://host:5111`（混合内容/跨网段/没做映射）。v0.4.6 起会自动改走 `host:5012/zy/api`（由控制台反代到 5111），所以只要 5012 能到就没事；两档都不通才需要在反代上加 `/zy/ → 127.0.0.1:5111`。验证：`node src/verify-domain.mjs http://<你的域名>:5000` |
| asset 200 但 `__ZY__.ver` 是旧版 | 单文件挂载 inode 漂移：`bash deploy/reapply.sh`（比对 md5 + `docker restart` 重挂） |
| 页面白屏 / 挂载点是目录 | 重建时 `state/wwwroot/` 里文件不存在 → docker 建成目录。`bash install.sh publish` 后 `docker compose up -d --force-recreate` |
| 进度显示 44% 打开是目录 | 该书还没重排版 → 做第 4 步 |
| 两侧差值 < 1 个点 | 正常，页粒度导致，不是 bug |
| **读了一晚上，掌阅 pct 一格不动** | ① `zync.py push --dry-run` 看 pct 有没有 `(页内)` —— 没有就是 `BookScrollId` 丢了；② 看 Kavita 里 `PagesRead` 变没变（没变 = 浏览器没存进度 / 钩子没加载，查 api.log 的 `hb` 行）；③ 页数太少（600 页的大书一页 2.3 万字）→ 按 4.1 用 `ZY_KPAGES_MAX_PAGES=3000` 重建 + `zync.py pull` 重映射 |
