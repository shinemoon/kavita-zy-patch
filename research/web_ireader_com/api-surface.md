# 掌阅 pc.ireader.com 官方 Web 端 API 表

来源: 逆向 `https://pc.ireader.com/_nuxt/37032d8.js` 里的 axios 路由表 (模块 50)。
抓包时间: 2026-10-21 12:0x。所有路径均以 `https://api-pc.zhangyue.com` 为 base。

## 网关
- 前端站点: `https://pc.ireader.com` (Nuxt 2 SSR)
- API 网关: `https://api-pc.zhangyue.com`
- 静态资源 / 图床: `static-efe-front-h.zhangyuecdn.com`, `bookbk.img.zhangyue01.com`
- 埋点: `logv2.ireader.com` (sensorlog), `apmplus.volces.com` (火山 APM)

## 公共 query 参数
所有 `/bookstore/open/*` 请求带这一组 device 指纹参数:
```
usr=  & zysid=  & p29=zye5b814 & p2=104583 & p3=101010010 & p4=501603 & p33=com.chaozh.iReaderFree
```
- `p33` = app 包名 (iReaderFree = 掌阅免费版)
- `usr` / `zysid` = 登录后拿到的用户标识, 登录前为空
- `p29/p2/p3/p4` = 渠道/设备/rom 版本等固定指纹 (web 端硬编码)

## user — 登录与用户信息
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/bookstore/open/user/qrcode/get` | 取扫码登录 code, 返回 `{"code":0,"body":"<32位hex>"}` |
| GET | `/bookstore/open/user/qrcode/result?code=<code>` | 轮询扫码结果。未授权返 `{"code":30059,"msg":"随机码未授权"}`; 授权后返回用户标识 |
| GET | `/bookstore/open/user/info` | 当前登录用户信息 |

## bookshelf — 书架 / 云存储 / 进度
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/bookstore/open/cloud/bookshelf/list` | 拉书架 |
| POST | `/bookstore/open/cloud/bookshelf/upsert` | 加入/更新书架 |
| POST | `/bookstore/open/cloud/storage/upload/authorize` | 云存储上传授权 (URL 签名) |
| POST | `/bookstore/open/cloud/storage/download/authorize` | 云存储下载授权 (URL 签名) |
| GET | `/bookstore/open/book/details` | 批量取封面/简介 (batch) |
| GET | `/bookstore/open/cloud/book/getProgress` | **批量取阅读进度** |
| GET | `/bookstore/open/cloud/storage/file/info` | 云存储文件信息 (下载 epub 原文件用) |

## reader — 阅读核心
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/bookstore/open/book/detail` | 单本书详情 |
| GET | `/bookstore/open/book/chapter/web/list` | **章节目录列表** |
| GET | `/nsr/chapter-content` | **章节正文内容** (注意: 不在 /bookstore 前缀下, 是独立网关路径) |
| GET | `/trade/open/book/asset` | 书籍资产信息 |
| GET | `/trade/open/browser/billing/batch_gear` | 批量计费档位 |
| GET | `/trade/open/browser/billing/template/batch` | 批量购买模板 |
| GET | `/trade/open/browser/billing/template/book` | 整本购买模板 |
| GET | `/trade/open/browser/billing/template` | 单章购买模板 |
| POST | `/trade/open/order/pay/batch` | 批量下单 |
| POST | `/trade/open/order/pay/book` | 整本下单 |
| GET | `/trade/open/browser/order/pay/qr` | 下单二维码 |
| GET | `/trade/open/browser/order/pay/qr_result` | 支付结果轮询 |

## 阅读行为上报 (进度同步的核心)
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/bookstore/open/cloud/book/progress` | **上报阅读进度** ← 桥接的关键写接口 |
| POST | `/bookstore/open/read/time` | **上报阅读时长** ← 用于统计/防盗版 |

## billboard — 运营位
| GET | `/bookstore/open/section/list` | 首页运营位/推荐位 |

## 结论 (对你的目标)
1. **书架**: `cloud/bookshelf/list` ✅
2. **进度拉取**: `cloud/book/getProgress` ✅
3. **进度上报**: `cloud/book/progress` (POST) ✅
4. **章节目录**: `book/chapter/web/list` ✅
5. **章节正文**: `/nsr/chapter-content` ✅ ← 这是最关键的一个, 拿到它等于拿到全书文本
6. **下载原版 epub**: `cloud/storage/file/info` + `cloud/storage/download/authorize` ✅ (若你在 App 里下过原书)

→ 你要的"拉书架 + 进度 + 直接阅读正文, 渲染自己写"完全可行。官方 web 端把所有能力都开了。

## 下一步
- 登录: 用掌阅 App 扫一次二维码 → 拿 usr/zysid → 后续请求带上
- 然后逐个试这些端点, 看返回结构和字段