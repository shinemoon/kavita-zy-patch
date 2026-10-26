// 探针: 主题在手机宽度下不溢出 / 卡片居中 / 滚动正常。
// 用法: node src/probe-theme-mobile.mjs [url] [book]
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

async function setTheme(id) {
  const r = await fetch('http://127.0.0.1:5012/api/reader_css', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ op: 'theme', theme: id }),
  });
  return (await r.json()).version;
}

const browser = await chromium.launch({ args: ['--no-sandbox'] });
for (const id of ['feishu', 'console']) {
  const ver = await setTheme(id);
  const ctx = await browser.newContext({ viewport: { width: 430, height: 780 }, deviceScaleFactor: 2 });
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
  const m = await page.evaluate(() => {
    const bc = document.querySelector('.book-content');
    const p = document.querySelector('.book-content p');
    const rc = document.querySelector('.reader-container');
    const r = bc.getBoundingClientRect();
    const rr = rc.getBoundingClientRect();
    const cs = p ? getComputedStyle(p) : null;
    return {
      vw: innerWidth,
      canvas: { x: Math.round(rr.x), w: Math.round(rr.width) },
      card: { x: Math.round(r.x), w: Math.round(r.width), top: Math.round(r.y) },
      pad: getComputedStyle(bc).padding,
      overflowX: document.documentElement.scrollWidth > innerWidth + 1,
      scrollH: document.scrollingElement.scrollHeight,
      pWidth: p ? Math.round(p.getBoundingClientRect().width) : null,
      fs: cs ? cs.fontSize : null, lh: cs ? cs.lineHeight : null,
    };
  });
  await page.screenshot({ path: `/vol2/1001/kavita-zy-patch/build/theme-${id}-mobile.png`, fullPage: false });
  console.log(id, 'v' + ver, JSON.stringify(m));
  await ctx.close();
}
await browser.close();
