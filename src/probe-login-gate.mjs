// 验证 5012 / 5013 的登录门 (复用 Kavita 账号密码).
// 用法: node src/probe-login-gate.mjs
//   机器上没有任何人有 Kavita 密码时也能跑: 脚本用 webauth 自己签一个会话 cookie 注入浏览器,
//   验的是"门是否真的挡住 / 放行 / 退出可用", 不验密码本身 (密码由用户手输).
import { chromium } from 'playwright';
import { execFileSync } from 'child_process';

const PY = process.env.PROBE_PY || 'python3';
const targets = [
  { name: '5012 掌阅桥接', base: 'http://127.0.0.1:5012', title: '掌阅桥接',
    module: 'webauth', syspath: '/vol2/1001/zhangyue-bridge/sync',
    import: 'import webauth as w' },
  { name: '5013 epub-refiner', base: 'http://127.0.0.1:5013', title: 'epub-refiner',
    module: 'refiner.webauth', syspath: '/vol2/1001/epub-refiner',
    import: 'from refiner import webauth as w' },
];

function tokenFor(t) {
  return execFileSync(PY, ['-c', `
import sys; sys.path.insert(0, ${JSON.stringify(t.syspath)})
${t.import}
print(w.make_token('claud'))
`], { encoding: 'utf8' }).trim();
}

const browser = await chromium.launch({ args: ['--no-sandbox'] });
let bad = 0;

for (const t of targets) {
  const ctx = await browser.newContext({ viewport: { width: 1200, height: 900 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));

  // 1) 未登录 -> 登录页
  await page.goto(t.base + '/', { waitUntil: 'domcontentloaded' });
  const loginUrl = page.url();
  const hasUser = await page.locator('input[name=username]').count();
  const hasPw = await page.locator('input[name=password]').count();
  const sub = await page.locator('.sub').first().innerText().catch(() => '');

  // 2) 注入会话 cookie -> 正常页面
  await ctx.addCookies([{ name: 'zy_webauth', value: tokenFor(t), domain: '127.0.0.1', path: '/' }]);
  await page.goto(t.base + '/', { waitUntil: 'domcontentloaded' });
  const authedUrl = page.url();
  const h1 = await page.locator('h1').first().innerText().catch(() => '');
  const logout = await page.locator('a.logout').count();

  // 3) 点「退出」-> 回登录页
  if (logout) await page.locator('a.logout').first().click();
  await page.waitForTimeout(400);
  const afterLogout = page.url();
  const backToLogin = await page.locator('input[name=password]').count();

  const ok = loginUrl.includes('/login')
    && hasUser === 1 && hasPw === 1
    && !authedUrl.includes('/login') && h1.trim().length > 0
    && logout === 1 && afterLogout.includes('/login') && backToLogin === 1;
  if (!ok) bad++;
  console.log(`${ok ? 'PASS' : 'FAIL'} ${t.name}`);
  console.log(`  gate: ${loginUrl}`);
  console.log(`  表单: user=${hasUser} pw=${hasPw} 提示=${JSON.stringify(sub.slice(0, 40))}`);
  console.log(`  登录后: ${authedUrl} h1=${JSON.stringify(h1.slice(0, 30))} 退出键=${logout}`);
  console.log(`  退出后: ${afterLogout} 回登录页=${backToLogin === 1}`);
  if (errors.length) console.log('  pageerror:', errors.slice(0, 3));
  await ctx.close();
}

await browser.close();
console.log(bad ? `\n${bad} 个目标未通过` : '\n全部通过');
process.exit(bad ? 1 : 0);
