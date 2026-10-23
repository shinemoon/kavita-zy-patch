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
| 5111 | 补丁后端 | **浏览器要能直连**（页面里的 fetch 打它，不是反代） |
| 5012 | 桥接控制台 | 掌阅认证 / 推拉 / **Kavita 改造** |

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

1. 容器没跑 → `docker start`
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
| 有导航项，徽标空白/「离线」 | 自检 `补丁后端` 是否红 → 「重启补丁后端」；F12 看 5111 请求 |
| 5111 报 `bad token` | `state/token` 与页面里 `?v=` 一起重渲染：重跑 install |
| 徽标全不显示 | 没映射记录：`selftest.py` 看 `/zy/api/status` 的 rows |
| 「安装自启」点了报 root | 面板没有免密 sudo → 手跑 `sudo bash deploy/install-autostart.sh`（一次） |
| 自检红「自愈巡检」 | 点「立即自检」看输出；`state/logs/heal.log` 有失败原因 |
| 「继续阅读」报错跳回详情页 | 书内链接坏了：重跑 `apply_edition.py --series N`（构建即自校验）；或回滚 `.bak` |
| **自动 push/pull 像没生效** | 按顺序看三处：① `bash install.sh status` 里 `mode=`；② `state/logs/api.log` 有没有 `hb vX.Y.Z`（没有 = 浏览器没加载这一版钩子 → 强刷 / 查 inode 漂移）；③ 有没有 `auto-pull` / `auto-push` 行，`pushed=0 skipped=1` 是 `push_decision` 主动挡（回退或半页内），不是故障 |
| asset 200 但 `__ZY__.ver` 是旧版 | 单文件挂载 inode 漂移：`bash deploy/reapply.sh`（比对 md5 + `docker restart` 重挂） |
| 页面白屏 / 挂载点是目录 | 重建时 `state/wwwroot/` 里文件不存在 → docker 建成目录。`bash install.sh publish` 后 `docker compose up -d --force-recreate` |
| 进度显示 44% 打开是目录 | 该书还没重排版 → 做第 4 步 |
| 两侧差值 < 1 个点 | 正常，页粒度导致，不是 bug |
