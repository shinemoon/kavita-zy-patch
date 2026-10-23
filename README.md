# Kavita 掌阅补丁

在 Kavita 里植入掌阅（iReader）书架的进度管理功能。

## 两个入口 (v0.3.0)

补丁**只做导航**, 不做业务 UI。业务全在 5012 控制台 (认证二维码 / 推拉 / 日志 / 改造)。

1. **顶栏右侧** —— 小圆点 (后端活/死) + 图标 + 「掌阅同步」，窄屏只留图标。
   任何宽度都可见，手机上也有。
2. **左侧抽屉** —— 「掌阅同步 · N 本」，点开汉堡菜单时看到。

> v0.3.0 加顶栏入口，是因为 0.2.0 只有抽屉项 —— 手机上抽屉默认收起，
> 用户以为「按钮没了」。另外 index.html 不带 `Cache-Control`，浏览器会
> 缓存旧版，看不到入口时先强刷 (Ctrl/Cmd+Shift+R)，再在 F12 里
> `__ZY__.ver` 确认版本。

## 架构

```
Kavita SPA (容器 :5000)
  └─ index.html 末尾 <script src="zy-kavita-patch.js">   ← 注入点
        ├─ 入口按钮 → 控制台 :5012
        ├─ 读书钩子 (v0.4.0)  ← 见下节「自动同步」
        └─ fetch → 补丁后端 (host :5111)
                      ├─ /zy/api/status     两侧进度对照
                      ├─ /zy/api/selfcheck  体检 (warning 数据源)
                      ├─ /zy/api/push|pull  调 bridge sync 包 (全库)
                      ├─ /zy/api/auto/pull|push  单本 (读书钩子用)
                      ├─ /zy/api/auto/hb   前端心跳 (证明钩子真在跑)
                      └─ /zy/api/link       建映射
                            └─ /vol2/1001/zhangyue-bridge/sync
                                  ├─ sync/kavita_db.py  直读写 kavita.db
                                  └─ sync/zy_client.py   掌阅 cloud API
```

补丁不碰 Kavita 任何一行代码，纯外部注入。

## 前端落地的两种模式 (v0.4.1)

| 模式 | 怎么落地 | 容器重建 / 换镜像 |
|---|---|---|
| **mount（推荐）** | compose 把宿主两个文件只读挂进 `wwwroot` | **不丢**；镜像升级后自愈会自动用新镜像的 `index.html` 重建 |
| copy（旧） | `docker cp` 打进容器 overlay 可写层 | 丢了，靠自愈每 1–3 分钟重打 |

挂载模式要往 `docker-compose.yml` 的 `volumes:` 加两行（**先 `install.sh publish` 生成文件，再 `up -d`**）：

```yaml
    volumes:
      - /vol1/1001/APPS/kavita:/kavita/config
      - /fs/1001/ftp/dav/Books:/books:ro
      - /vol2/1001/kavita-zy-patch/state/wwwroot/index.html:/kavita/wwwroot/index.html:ro
      - /vol2/1001/kavita-zy-patch/state/wwwroot/zy-kavita-patch.js:/kavita/wwwroot/zy-kavita-patch.js:ro
```

要点（都是踩过的）：

1. **顺序不能反**：宿主文件不存在就 `up -d`，docker 会把挂载点建成**目录**，Kavita 直接白屏。
   先 `bash install.sh publish`，再 `docker compose up -d`。
2. **单文件挂载钉的是 inode**。`install`/`cp` 到新文件再 rename 会换 inode，容器里永远是旧内容，
   而 HTTP 一切 200 —— 这就是"补丁更新了但页面还是老版本"的成因。所以：
   - `publish_host` 一律用 `cat > 目标`（同 inode 覆盖）
   - `reapply.sh` 每次比对宿主/容器 md5，不一致就 `docker restart`（restart 会重新解析挂载源）
3. **镜像升级**：挂着的 `index.html` 是旧镜像的，引用的 chunk 哈希在新镜像里不存在 → 白屏。
   `publish_host` 直接从**当前镜像**里 `docker run --rm cat` 取原版重建，`heal.sh` 额外验一个
   `main-*.js` 是否 200，失败即触发重建。
4. 卸载要先从 compose 删掉这两行再重建；容器里是只读挂载，`install.sh uninstall` 会提示而不会硬改。


## 自动同步：读书钩子 (v0.4.0)

目标是让 Kavita 读书时的前进/后退跟掌阅 App 一样自然，不用记着点按钮。

| 时机 | 动作 | 语义 |
|---|---|---|
| 进书 `/library/x/series/S/book/C` | `auto/pull` | 掌阅 → Kavita，**只前进不回退**；位置真变了才 `location.reload()` 落到掌阅那页 |
| 阅读中 Kavita 存进度（`POST api/reader/progress`） | `auto/push` | Kavita → 掌阅，去抖 8s；`push_decision` 闸门照旧，回退/半页内不动 |
| 离开书 / 标签页隐藏 | flush | 立刻把压着的 push 发掉（`sendBeacon`，token 走 query） |
| 系列详情页 | `auto/pull` | 只刷进度条，不重载 |

- 前端启动先读 `GET /zy/api/auto/cfg`，`AUTO_SYNC=0` 就完全不挂钩子（一行代码都不跑）
- 钩子是 `XMLHttpRequest` + `fetch` 的轻包装，只看 URL 含 `reader/progress`；不改 Angular 任何东西
- 防抖/防环：同书 pull 间隔 `AUTO_PULL_GAP`（默认 120s，存 sessionStorage）、重载间隔 10s、
  push 去抖 `AUTO_PUSH_DEBOUNCE`（默认 8s）；pull 写的是 DB 不走 reader，不会触发自己
- 调试：浏览器控制台 `__ZY__.auto()` 看开关 + 最近 24 条动作；`__ZY__.pull(26)` / `__ZY__.push(26)` 手动触发
- 后端 `auto/pull|push` 只处理传进来的 `series_id`，不再全库扫；两个动作互斥（同一把锁，忙则返回 `busy`）
- **看得见** (v0.4.1)：右下角小提示 —— pull 真的动了位置弹「已同步掌阅进度 41 → 49/100」，
  push 真的推成功弹「进度已推送到掌阅」。没动就不弹（否则天天弹等于没弹）
- **心跳** (v0.4.1)：前端加载后 `POST /zy/api/auto/hb {ver,path,on}`，后端按 (版本,路径) 5 分钟节流
  落一条 `hb vX.Y.Z auto=on path=...` 到 `state/logs/api.log`，同时写 `state/last_hb.json`。
  排查"钩子到底跑没跑"先看这两处 —— 没有 = 浏览器根本没加载这一版钩子，别去怀疑 pull/push 逻辑

**多标签 / 多设备**：钩子按标签页独立工作，同一本书的 pull 有 `AUTO_PULL_GAP` 间隔，
所以两台设备同时开着同一本书时只有先到的那台会 pull；push 各自照推（`push_decision` 会挡回退）。

### 页内精度 (v0.4.3)

Kavita 只把「读到第几页」写进 `PagesRead`，页内位置写在 `BookScrollId`（xpath）。
后端 push 会把这条 xpath 还原成**页内字节比例**，再按掌阅的字节口径算 `readPercent`：

```
pct = (页起点字节 + 页内比例 × 页长) / 全书字节
```

所以一页之内读到哪儿都能同步出去，不必等翻页 —— 2026-10-23 用户实测「在 Kavita 读
剑来一晚上，掌阅 pct 不动」，就是只读到页号、页内位置被丢掉导致的。

两个不同的问题别混在一起：

| 现象 | 属于 | 解法 |
|---|---|---|
| 掌阅 pct 一直不动 | 页内精度 / 钩子没跑 / 两侧已对齐 | 看 `sync.log` 里 `(页内)` 与 `noop`；`hb` 行确认钩子 |
| Kavita 里页号半天不跳（% 也不动） | **页太粗**（600 页版的大书一页 ≈ 2.3 万字） | 调大 `MAX_PAGES` 重建 + `zync.py pull` 重映射（SETUP 4.1） |

**三个前提**（排查"钩子好像没生效"时先看这三条）：

1. **页面里得有补丁**：浏览器打开 Kavita 后 `F12 → __ZY__.ver` 应能打印版本号。打印不出来 =
   `zy-kavita-patch.js` 没加载 —— 挂载模式正常不会发生；拷贝模式容器重建后会 404（自愈最多 1–3 分钟重打）。
   此时**刷新一次页面**。加载了但版本号是旧的 = inode 漂移，跑 `deploy/reapply.sh`。
2. **只在浏览器开着 Kavita 网页时工作**：在掌阅 App 里读，Kavita 不知道；下次打开 Kavita 网页
   （进书 / 详情页）才会 pull 一次。
3. **`BASE = http://<当前访问的主机名>:5111`**：所以从哪台设备读，那台设备就要能直连 `5111`。
   走 HTTPS 反代访问 Kavita 会被浏览器按混合内容拦掉（需把 5111 也反代成 https 并改 `src/zy-kavita-patch.js.tmpl` 的 BASE）。

## 5012 控制台：「Kavita 改造」卡

`http://<host>:5012/` 顶部第二张卡片，把「新装一台 Kavita 要做的所有事」收在一处：

- 自检：容器 / 前端资源 / index 注入 / 补丁后端 / token / kavita.db / 映射 / 重排版 /
  掌阅凭据 / **前端资产版本** / **自启** / **自愈** —— 红项后面直接写修法
- 第一行按钮：**一键改造 / 新装适配**（注入 → 巡检 → 装自启 → 自检）、
  **注入 / 重打**（`install.sh install`）、**重启补丁后端**
- 第二行：**立即自检**（跑 `deploy/heal.sh`）、**安装自启**（`sudo … install-autostart.sh`）、
  **关自愈 / 开自愈**（`state/heal.disabled` 开关）
- 第三行：**重排版 dry-run** / **重排版执行** / **体检** / **重新检测**
- 折叠区：**自愈日志**（`state/logs/heal.log` 尾部 60 行）

**新装一台 Kavita 的完整步骤：见 [`docs/SETUP.md`](docs/SETUP.md)。**

## 自启动 + 自愈

免 root 首选 crontab（`deploy/install-cron.sh`：开机 30s 后 + 每 1 分钟巡检）；
有 root 再叠 systemd（`deploy/install-autostart.sh`：进程崩了自动重启）：

| unit / 条目 | 干什么 |
|---|---|
| `kavita-zy-api.service` | 补丁后端 :5111 |
| `kavita-zy-web.service` | 控制台 :5012 |
| `kavita-zy-heal.service` + `.timer` | 每 3 分钟跑 `deploy/heal.sh`（root 版） |
| crontab `*/1 * * * *` | 同上（免 root 版） |
| crontab `@reboot` | 开机 30s 后跑一次（把 5111/5012 拉起来） |

`deploy/heal.sh` 幂等巡检：容器在跑 → Kavita 可访问 → 前端注入还在
（不在就自动 `reapply.sh` 重打）→ 5111 / 5012 活着（不活就拉起）→ 代码比进程新就
重启对应 unit（改了 `src/*.py` 不用等人手动 sudo）。结果写
`state/heal.json`，事件写 `state/logs/heal.log`（512KB 轮转，成功巡检每小时只留一条
heartbeat）。

```bash
bash /vol2/1001/kavita-zy-patch/deploy/install-cron.sh                # 免 root
sudo bash /vol2/1001/kavita-zy-patch/deploy/install-autostart.sh      # 需 root
bash /vol2/1001/kavita-zy-patch/deploy/uninstall-cron.sh
sudo bash /vol2/1001/kavita-zy-patch/deploy/uninstall-autostart.sh
```

> 面板没有免密 sudo 时，「装自启 (systemd)」会失败并把命令原样回显；
> 「装自启 (免 root)」不需要 root，点了就生效。

## 常用命令

```bash
cd /vol2/1001/kavita-zy-patch

bash install.sh             # 装 / 升级 (幂等, 重跑即重打)
bash install.sh publish     # 只把注入版 index.html + 补丁 JS 写到挂载目录 (不动容器)
bash install.sh uninstall   # 还原 index.html + 停后端 (挂载模式会提示改 compose)
bash install.sh status      # 看模式 / asset / 注入 / 后端
bash deploy/reapply.sh      # Kavita 升级后重打 (自动判断要不要打, 含 inode 漂移检测)
bash deploy/heal.sh         # 自愈巡检一次 (容器/注入/5111/5012)

./ctl.sh status             # 后端进程状态
./ctl.sh restart-api        # 改完 api_server.py 后重启
./ctl.sh logs 60            # 看日志
```

## 怎么做到方便 patch

三个设计决定：

**1. 不改镜像，只注入静态文件**
前端只有两个文件：`wwwroot/index.html`（末尾加一行 script）+ `wwwroot/zy-kavita-patch.js`。
两种落法（见「前端落地的两种模式」）：挂载模式把宿主 `state/wwwroot/` 的两个文件只读挂进容器，
容器重建也不丢；拷贝模式 `docker cp` 进 overlay，重建即丢、靠自愈重打。
镜像本身不动，Kavita 升级不会跟镜像版本冲突。

**2. 每次都从原版重建 index.html**
`backups/index.html.orig` 是原版备份 —— 挂载模式下直接从**当前镜像**里 `docker run --rm cat` 取一份，
所以镜像升级后自动跟上新 asset 哈希。每次 install 都从它出发，先用正则摘掉所有历史注入痕迹，再追加当前版本。所以：
- 改版本号 → 直接重跑 install
- 改标记名 → 直接重跑 install（正则认的是 `ZY-KAVITA-PATCH` 前缀 + `zy-kavita-patch.js` 文件名）
- 装错了 → uninstall 是精确还原，不是「反向正则删除」

**3. 路径自己找，不写死**
拷贝模式下 `locate_wwwroot()` 扫 `docker inspect` 的 `GraphDriver.Data.UpperDir`，按候选路径逐个试，
最后 fallback 到 `find index.html`。镜像换目录结构不会写死失效。挂载模式不看 overlay。

**新增子目录时怎么加：**
前端是一个自包含文件（`src/zy-kavita-patch.js.tmpl`）。加新功能时改这个文件 + 改 `PATCH_VERSION`，然后 `./install.sh`。浏览器按 `?v=版本号` 缓存，改版本号就自动破缓存。

## 排障

| 现象 | 查什么 |
|---|---|
| 页面看不到导航项 | `bash install.sh status`；`curl -s $KAVITA_URL/zy-kavita-patch.js \| head -3` |
| 导航项有但徽标空/「离线」 | `./ctl.sh status` 后端是否活；F12 Console 看 fetch 报错 |
| 徽标全不显示 | 大概率没有映射记录。跑 `python3 src/selftest.py` 看 `/zy/api/status` 的 rows |
| **asset 200 但 `__ZY__.ver` 还是旧版本** | 单文件挂载的 inode 漂移：`bash deploy/reapply.sh`（会比对 md5 并 `docker restart` 重挂） |
| **自动同步像没生效** | ① `state/logs/api.log` 里有没有 `hb vX.Y.Z`（没有 = 前端钩子没加载/没跑）；② 有没有 `auto-pull` / `auto-push` 行；③ `push` 行里 `pushed=0 skipped=1` = `push_decision` 挡了（回退或半页内），不是坏了 |
| 页面白屏 / 挂载点变成目录 | 重建时宿主文件不存在 → docker 建了目录。`bash install.sh publish` 后再 `docker compose up -d --force-recreate` |
| warning 条内容不更新 | `localStorage` 里 `zy-warn-dismissed-at` 6h TTL，清掉即可 |
| e2e 测试 | `node src/verify-auto.mjs`（NVM + playwright；会打真后端, 只读/幂等） |

`src/selftest.py` 不依赖浏览器，直接打后端四个接口列问题，最快的诊断入口。

## 体检项

`/zy/api/selfcheck` 返回结构化问题清单，每项带 `level` / `code` / `msg` / `fix`：

| code | level | 含义 |
|---|---|---|
| `kavita_db` / `kavita_db_perm` | error | Kavita 数据库不在或不可读 |
| `zy_auth` / `zy_auth_fields` / `zy_auth_perm` | warn/error | 掌阅凭据缺失/字段不全/权限不对 |
| `bridge` / `bridge_import` | error | bridge 代码不在或 import 失败 |
| `token` | warn | 共享 token 未生成 |
| `no_links` | info | 还没有映射 |
| `unlinked` | info | 读过但没连掌阅的书 |
| `name_mismatch` | warn | **书名对不上，大概率连错书** |

warning 条只上 error + 最多 1 条 warn，info 不上条。

`name_mismatch` 用 `_names_look_related()` 判：剥掉「简体/精校版/卷01/括号」等噪声后比对公共子串。宁可放过不误报。

## 配置

`patch.conf`：

| 键 | 说明 |
|---|---|
| `CONTAINER` | Kavita 容器名 |
| `CONTAINER_WWWROOT` | 容器内 wwwroot 路径（install 会在可写层里自动校正） |
| `KAVITA_URL` | 前端地址 |
| `API_PORT` | 补丁后端端口（**避开已占端口**） |
| `TOKEN_FILE` | 共享 token 存放位置 |
| `BRIDGE_ROOT` | zhangyue-bridge 目录 |
| `AUTO_SYNC` | `1` = 开读书钩子；`0` = 前端完全不挂钩子 |
| `AUTO_PUSH_DEBOUNCE` | 阅读中存进度后等几秒再 push（默认 8） |
| `AUTO_PULL_GAP` | 同一本书两次 auto pull 的最小间隔秒数（默认 120） |

改端口后要 `./install.sh` 重装（token 和端口会重新渲染进前端）。
改 `AUTO_*` 只需重启补丁后端（`systemctl restart kavita-zy-api` 或等 heal 3 分钟内自己发现代码变新（cron 版 1 分钟））。

## 仓库里不含什么

本仓只有代码和文档。以下运行时产物**不入库**（见 `.gitignore`）：

- `state/token` —— 前后端共享 token（install 时生成，600 权限）
- `state/` 下所有运行状态与日志（`heal.json` / `logs/` / 备份）
- `backups/`（原版 index.html 等）
- 掌阅凭据 `.auth.json`（在 bridge 目录，不在本仓）
- Kavita 的 `appsettings.json` / `kavita.db`（脚本只按路径读，密钥不落地）

## 认证

后端认 `X-ZY-Token` 头（前端从模板注入时替换 `__ZY_TOKEN__`），token 文件 600 权限。

不开 `Access-Control-Allow-Credentials` —— 预检请求不带凭据，所以 CORS 无法共享 cookie。前端靠显式 token 头工作。局域网外无 token 打不到接口。