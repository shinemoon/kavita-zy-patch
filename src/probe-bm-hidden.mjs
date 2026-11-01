// 验证 v0.4.17: 书签图标被 CSS 隐藏 (display:none), JS 归位逻辑不再跑。
// 用法: node src/probe-bm-hidden.mjs [origin] [bookPath]
import { chromium } from 'playwright';
import { execFileSync } from 'child_process';

const ORIGIN = process.argv[2] || 'http://127.0.0.1:5000';
const BOOK = process.argv[3] || '/library/1/series/29/book/37?incognitoMode=true';

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
const errors = [];
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
page.on('pageerror', e => errors.push(String(e)));
await page.goto(ORIGIN + BOOK, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(13000);

const r = await page.evaluate(() => {
  const list = document.querySelectorAll('.bookmark-overlay');
  const rows = [];
  for (let i = 0; i < list.length; i++) {
    const el = list[i];
    const cs = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    rows.push({ display: cs.display, visibility: cs.visibility,
                box: [rect.width, rect.height].map(Math.round),
                zyStyle: el.getAttribute('data-zy-bm') || '(untouched)' });
  }
  return { ver: (window.__ZY__ && window.__ZY__.ver) || null,
           count: list.length, rows,
           bmOff: window.__ZY__ && window.__ZY__.bm ? window.__ZY__.bm().off : 'n/a' };
});

// 翻一页, 新注入的图标也必须隐藏
await page.keyboard.press('ArrowRight');
await page.waitForTimeout(2500);
const after = await page.evaluate(() => {
  const list = document.querySelectorAll('.bookmark-overlay');
  const rows = [];
  for (let i = 0; i < list.length; i++) {
    rows.push(getComputedStyle(list[i]).display);
  }
  return { count: list.length, displays: rows };
});

console.log(JSON.stringify({ ver: r.ver, before: r, after, errors }, null, 2));
await browser.close();
