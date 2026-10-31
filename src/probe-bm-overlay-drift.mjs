// 量"书签图标"是否贴在图片右下角; 改视口/翻页后再量, 看它是否漂到页面中间。
// 用法: node src/probe-bm-overlay-drift.mjs [origin] [book]
import { chromium } from 'playwright';
import { execFileSync } from 'child_process';

const ORIGIN = process.argv[2] || 'http://127.0.0.1:5000';
const BOOK = process.argv[3] || '/library/1/series/29/book/36';

const acct = JSON.parse(execFileSync('python3', ['-c', `
import sqlite3, json, tempfile, os
src='/vol1/1001/APPS/kavita/kavita.db'
dst=os.path.join(tempfile.mkdtemp(),'k.db')
s=sqlite3.connect(src); d=sqlite3.connect(dst); s.backup(d); d.close(); s.close()
c=sqlite3.connect(dst)
u=c.execute('select Id, UserName, Email from AspNetUsers order by Id limit 1').fetchone()
k=c.execute("select Key from AppUserAuthKey where Name='opds' limit 1").fetchone()
print(json.dumps({'id': u[0], 'username': u[1], 'email': u[2], 'apiKey': k[0] if k else ''}))
`], { encoding: 'utf8' }));
const ACCOUNT = {
  id: acct.id, oidcId: null, username: acct.username, email: acct.email,
  roles: ['Admin', 'Download', 'ChangeRestriction', 'Login'],
  token: 'e2e-fake-token', refreshToken: '', apiKey: acct.apiKey,
  preferences: { theme: { id: 2, name: 'Aquamarine', normalizedName: 'aquamarine',
    fileName: 'aquamarine.css', isDefault: true, description: '',
    previewUrls: [], selector: 'aquamarine', coverImage: '' },
    backgroundImageUrls: [] },
  readingProfiles: []
};

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
await ctx.route('**/api/**', route => {
  const hd = { ...route.request().headers(), 'X-Api-Key': acct.apiKey };
  if (/\/api\/Account$/.test(route.request().url())) {
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(ACCOUNT) });
  }
  return route.continue({ headers: hd });
});
await ctx.addInitScript(u => {
  localStorage.setItem('kavita-user', JSON.stringify(u));
  localStorage.setItem('kavita-lastlogin', u.username);
}, ACCOUNT);
const page = await ctx.newPage();
await page.goto(ORIGIN + BOOK, { waitUntil: 'domcontentloaded', timeout: 30000 });
await page.waitForTimeout(12000);

const MEASURE = () => {
  const rows = [];
  const overlays = document.querySelectorAll('.bookmark-overlay');
  for (const ov of overlays) {
    const or = ov.getBoundingClientRect();
    const parent = ov.parentElement;
    const img = parent ? parent.querySelector('img') : null;
    const ir = img ? img.getBoundingClientRect() : null;
    const pr = parent ? parent.getBoundingClientRect() : null;
    rows.push({
      ov: [or.left, or.top, or.width, or.height].map(Math.round),
      img: ir ? [ir.left, ir.top, ir.width, ir.height].map(Math.round) : null,
      parent: pr ? [pr.left, pr.top, pr.width, pr.height].map(Math.round) : null,
      // 图标右下角 vs 图片右下角 的偏差
      dRight: ir ? Math.round(ir.right - or.right) : null,
      dBottom: ir ? Math.round(ir.bottom - or.bottom) : null,
      inParent: !!(pr && or.left >= pr.left - 2 && or.top >= pr.top - 2 && or.right <= pr.right + 2 && or.bottom <= pr.bottom + 2),
    });
  }
  return rows;
};

console.log('=== 1400x900 初始 ===');
console.log(JSON.stringify(await page.evaluate(MEASURE), null, 1));

// 改视口 (相当于窗口缩放 / 旋转屏幕)
await page.setViewportSize({ width: 900, height: 1000 });
await page.waitForTimeout(3000);
console.log('\n=== 视口缩到 900x1000 后 (未重新注入?) ===');
console.log(JSON.stringify(await page.evaluate(MEASURE), null, 1));

await page.setViewportSize({ width: 1400, height: 900 });
await page.waitForTimeout(2500);
console.log('\n=== 回到 1400x900 后 ===');
console.log(JSON.stringify(await page.evaluate(MEASURE), null, 1));

// 翻页
await page.keyboard.press('ArrowRight');
await page.waitForTimeout(3500);
console.log('\n=== 翻到下一页后 ===');
console.log(JSON.stringify(await page.evaluate(MEASURE), null, 1));

// 全屏/缩放切换 (点阅读器设置里的 fit 或直接 ctrl+滚轮)
await page.keyboard.press('Control+=');
await page.waitForTimeout(2000);
console.log('\n=== ctrl+= 浏览器缩放后 ===');
console.log(JSON.stringify(await page.evaluate(MEASURE), null, 1));
await browser.close();
