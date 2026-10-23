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
        └─ fetch → 补丁后端 (host :5111)
                      ├─ /zy/api/status     两侧进度对照
                      ├─ /zy/api/selfcheck  体检 (warning 数据源)
                      ├─ /zy/api/push|pull  调 bridge sync 包
                      └─ /zy/api/link       建映射
                            └─ /vol2/1001/zhangyue-bridge/sync
                                  ├─ sync/kavita_db.py  直读写 kavita.db
                                  └─ sync/zy_client.py   掌阅 cloud API
```

补丁不碰 Kavita 任何一行代码，纯外部注入。

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

免 root 首选 crontab（`deploy/install-cron.sh`：开机 30s 后 + 每 3 分钟巡检）；
有 root 再叠 systemd（`deploy/install-autostart.sh`：进程崩了自动重启）：

| unit / 条目 | 干什么 |
|---|---|
| `kavita-zy-api.service` | 补丁后端 :5111 |
| `kavita-zy-web.service` | 控制台 :5012 |
| `kavita-zy-heal.service` + `.timer` | 每 3 分钟跑 `deploy/heal.sh` |
| crontab `*/3 * * * *` | 同上（免 root 版） |
| crontab `@reboot` | 开机 30s 后跑一次（把 5111/5012 拉起来） |

`deploy/heal.sh` 幂等巡检：容器在跑 → Kavita 可访问 → 前端注入还在
（不在就自动 `reapply.sh` 重打）→ 5111 / 5012 活着（不活就拉起）。结果写
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

./install.sh                # 装 / 升级 (幂等, 重跑即重打)
./install.sh uninstall      # 还原 index.html + 停后端
./install.sh status         # 看装没装
./deploy/reapply.sh         # Kavita 升级后重打 (自动判断要不要打)
bash deploy/heal.sh         # 自愈巡检一次 (容器/注入/5111/5012)
bash deploy/install-autostart.sh    # 装 systemd 自启 + 每 3 分钟巡检 (需 root)

./ctl.sh status             # 后端进程状态
./ctl.sh restart-api        # 改完 api_server.py 后重启
./ctl.sh logs 60            # 看日志
```

## 怎么做到方便 patch

三个设计决定：

**1. 不改镜像，只注入静态文件**
补丁打进容器 overlay 的可写层（`wwwroot/`），镜像本身不动。Kavita 升级不会让补丁跟镜像版本冲突，只是 overlay 被清掉需要重打。

**2. 每次都从原版重建 index.html**
`backups/index.html.orig` 是第一次装时从容器 `docker cp` 出来的真原版。每次 install 都从它出发，先用正则摘掉所有历史注入痕迹，再追加当前版本。所以：
- 改版本号 → 直接重跑 install
- 改标记名 → 直接重跑 install（正则认的是 `ZY-KAVITA-PATCH` 前缀 + `zy-kavita-patch.js` 文件名）
- 装错了 → uninstall 是精确还原，不是「反向正则删除」

**3. 路径自己找，不写死**
`locate_wwwroot()` 扫 `docker inspect` 的 `GraphDriver.Data.UpperDir`，按候选路径逐个试，最后 fallback 到 `find index.html`。镜像换目录结构不会写死失效。

**新增子目录时怎么加：**
前端是一个自包含文件（`src/zy-kavita-patch.js.tmpl`）。加新功能时改这个文件 + 改 `PATCH_VERSION`，然后 `./install.sh`。浏览器按 `?v=版本号` 缓存，改版本号就自动破缓存。

## 排障

| 现象 | 查什么 |
|---|---|
| 页面看不到导航项 | `./install.sh status`；`curl -s $KAVITA_URL/zy-kavita-patch.js \| head -3` |
| 导航项有但徽标空/「离线」 | `./ctl.sh status` 后端是否活；F12 Console 看 fetch 报错 |
| 徽标全不显示 | 大概率没有映射记录。跑 `python3 src/selftest.py` 看 `/zy/api/status` 的 rows |
| warning 条内容不更新 | `localStorage` 里 `zy-warn-dismissed-at` 6h TTL，清掉即可 |
| e2e 测试 | `node src/e2e-check.mjs`（需 NVM + playwright） |

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

改端口后要 `./install.sh` 重装（token 和端口会重新渲染进前端）。

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