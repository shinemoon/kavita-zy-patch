// 验证: 图片阅读器的书签图标被钉回图片右下角内侧, 缩放/翻页后仍贴角。
// 用法: node src/verify-bm-pin.mjs [origin] [book]
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
const errs = [];
page.on('pageerror', e => errs.push('PAGEERR ' + e.message.slice(0, 120)));

await page.goto(ORIGIN + BOOK, { waitUntil: 'domcontentloaded', timeout: 30000 });
await page.waitForTimeout(13000);

const snap = async (label) => {
  const d = await page.evaluate(() => (window.__ZY__ && window.__ZY__.bm) ? window.__ZY__.bm() : 'no __ZY__.bm');
  console.log('\n### ' + label);
  console.log(' jsVer = ' + await page.evaluate(() => window.__ZY__ && window.__ZY__.ver));
  if (!d || !d.rows) { console.log(' ' + JSON.stringify(d)); return; }
  console.log(' fixed=' + d.fixed + ' removed=' + d.removed + ' count=' + d.count);
  for (const r of d.rows) console.log('  ' + JSON.stringify(r));
};

await snap('加载后 (1400x900)');

await page.setViewportSize({ width: 900, height: 1000 });
await page.waitForTimeout(2500);
await snap('视口 900x1000');

await page.setViewportSize({ width: 1400, height: 900 });
await page.waitForTimeout(2000);
await snap('回到 1400x900');

await page.keyboard.press('ArrowRight');
await page.waitForTimeout(4000);
await snap('翻到下一页');

// 点一下图标本身: 应该只切换实心/空心, 不跑位
const before = await page.evaluate(() => window.__ZY__.bm());
const clickable = await page.$('.bookmark-overlay');
if (clickable) {
  await clickable.click({ timeout: 4000 }).catch(() => {});
  await page.waitForTimeout(2500);
  await snap('点击图标 (加/取消书签) 后');
}

console.log('\nERRORS: ' + (errs.length ? errs.join(' | ') : 'none'));
await browser.close();
