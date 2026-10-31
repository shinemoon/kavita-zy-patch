// 给探针/截图脚本注入 5012 / 5013 的会话 cookie.
//
// 为什么: 2026-10-31 起这两个控制台先要登录 (凭据 = Kavita :5000 的账号密码).
// 这里用 webauth 直接签一个会话, 不碰任何密码 —— 探针验的是 UI 行为,
// 登录门本身由 src/probe-login-gate.mjs 专门验.
//
// 用法:
//   import { addZyCookie } from './zyauth.mjs';
//   const page = await browser.newPage();
//   await addZyCookie(page);        // 传 page / context 都行
//   await page.goto('http://127.0.0.1:5012/');
//
// 5013 (epub-refiner) 用不同签名入口:
//   await addZyCookie(page, { syspath: '/vol2/1001/epub-refiner',
//                             imp: 'from refiner import webauth as w' });
import { execFileSync } from 'child_process';

const BRIDGE = '/vol2/1001/zhangyue-bridge/sync';

export function zyToken({ syspath = BRIDGE, imp = 'import webauth as w', user = 'probe' } = {}) {
  return execFileSync('python3', ['-c', `
import sys; sys.path.insert(0, ${JSON.stringify(syspath)})
${imp}
print(w.make_token(${JSON.stringify(user)}))
`], { encoding: 'utf8' }).trim();
}

export async function addZyCookie(target, opts = {}) {
  const ctx = typeof target.context === 'function' ? target.context() : target;
  const host = opts.host || '127.0.0.1';
  await ctx.addCookies([{
    name: 'zy_webauth', value: zyToken(opts), domain: host, path: '/',
  }]);
  return ctx;
}
