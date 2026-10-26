// 一次性探针: 打印 Kavita 阅读器的 DOM 祖先链 + 关键容器计算样式, 供主题 CSS 定位。
// 用法: node src/probe-reader-dom.mjs [url] [book]
import { chromium } from 'playwright';
import { execFileSync } from 'child_process';

const ORIGIN = process.argv[2] || 'http://127.0.0.1:5000';
const BOOK = process.argv[3] || '/library/1/series/27/book/31';

const acct = JSON.parse(execFileSync('python3', ['-c', `
import sqlite3, json, os, tempfile
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
await page.waitForTimeout(9000);

const out = await page.evaluate(() => {
  const p = document.querySelector('.book-content p');
  const chain = [];
  let n = p;
  while (n && n !== document.documentElement) {
    const cs = getComputedStyle(n);
    chain.push({
      tag: n.tagName.toLowerCase(),
      cls: (n.className || '').toString().slice(0, 90),
      id: n.id || '',
      bg: cs.backgroundColor,
      color: cs.color,
      ff: cs.fontFamily.slice(0, 40),
      w: Math.round(n.getBoundingClientRect().width),
      pad: cs.padding,
      maxW: cs.maxWidth,
    });
    n = n.parentElement;
  }
  const body = getComputedStyle(document.body);
  return { chain, bodyBg: body.backgroundColor, bodyClass: document.body.className };
});
console.log(JSON.stringify(out, null, 1));
await browser.close();
