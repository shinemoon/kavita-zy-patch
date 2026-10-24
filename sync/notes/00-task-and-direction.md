# 调研大纲：掌阅进度同步桥

## 任务一句话
你已购买/下载的掌阅书籍的阅读进度，最好能在不同设备间同步，并能接到本地的 self-host 阅读器。

## 两个候选方向

### A. 反编译安卓掌阅 app（iReader / 掌阅 / 掌阅精选）
工具：jadx / jadx-gui + ghidra，已装好。
能拿到什么：
- 登录协议（OAuth2 或自研 token；通常有 device_id / sign 防伪造）
- 网关域名 + 路径（多套 CDN：static-efe-front-h.zhangyuecdn.com 等已观察到）
- 进度同步端点（猜测形如 `/api/v*/sync/progress` 或 `/api/book/{id}/progress`）
- 章节/目录结构、加密章节密钥来源

合规风险：
- 《掌阅用户协议》第 4-5 条通常禁止反向工程
- 用抓到的 token 仿冒官方 client 连接官方服务器，灰区，看具体绕过方式

### B. 反向官方 web 端（pc.ireader.com / www.ireader.com / m.ireader.com）
工具：浏览器 DevTools network + curl，零装软件。
能拿到什么：
- 直接看到所有 REST/GraphQL 调用
- 进度同步端点的请求/响应结构（明文 JSON）
- 登录态 cookie / token 续期机制
- 章节内容的 CDN URL（已观察到 `bookbk.img.zhangyue01.com` 之类的图床）

合规风险：
- 几乎没有 — 你就是浏览器用户，所有交互走官方接受协议

### C.（不推荐但要提）混搭：用 app 的设备登录 + web 端的开放 API
- 部分厂商对 web 端更宽松，能拿到纯网页 token；app 反编译只是补充协议细节

## 初步评估（待你拍板）

**务实建议：先 B 走通主线**。
- 零装工具，已经拿到登录态 + 书架数据
- 进度同步在 web 端几乎一定有对应端点（web 阅读器要同账号同步）
- 大概率够用 — 你的不满主要是 UI/体验，进度同步 web 协议直接转给本地阅读器即可

**A 什么时候必要**：
- web 端故意隐藏了某些 API（比如不允许 web 翻页/不存进度）
- 你需要逆向官方加密章节（这不是桥，是另起一摊）

**B 的天花板**：
- 部分进度上报逻辑只在 app 触发，web 不报 — 但概率小
- 加密章节 web 拿到的可能不是完整正文 — 看官方是懒渲染还是 CDATA 限定

## B 路线下一步（建议你同意后我开手）
1. 浏览器登录 pc.ireader.com，手动记录几个关键请求：
   - 登录态怎么带的（cookie？Authorization header？）
   - 书架列表 API（已从 SSR 看到 NUXT payload，验证同样 API 返同样数据）
   - 阅读器页：pc.ireader.com/reader/{book_id} 或 /book/{id}/reader
   - 上报进度的 POST/GET
2. curl 复刻这些请求，零浏览器复现 → 本地脚本包
3. 评估：进度同步可程序化后，写最小自宿主（Flask/FastAPI）暴露给本地阅读器用

## 决策点（请你回）
1. **方向首选**：B 还是 A？建议先 B
2. **阅读体验目标**（决定 self-host 的形态）：
   - (a) 仅同步进度 + 跳转，web 端继续看 → 进度一条 REST 接口
   - (b) 把已下载的章回拉到本地 epub/Calibre/Kavita → 桥 + 本地阅读器
   - (c) 重新排版/重渲染 web 内容，离线友好 → 桥 + html 处理
3. **API 抓包授权**：你愿不愿意下次开登录态浏览器给我抓 network？我现在没登录态。
4. **合规底线**：仿冒 client / 抓 token 这事你愿意做，还是只走 web 协议？

回 1+2+3+4 之后我就开手。
