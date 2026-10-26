// 验证阅读主题: 逐个切换 5012 的主题, 打开 Kavita 阅读器截图 + 量计算样式。
// 用法: node src/verify-reader-theme.mjs [url] [book] [theme...]
import { chromium } from 'playwright';
import { execFileSync } from 'child_process';
import { writeFileSync } from 'fs';

const ORIGIN = process.argv[2] || 'http://127.0.0.1:5000';
const BOOK = process.argv[3] || '/library/1/series/27/book/31';
const THEMES = process.argv.slice(4).length ? process.argv.slice(4) : ['default', 'feishu', 'console'];
const OUT = new URL('../build/', import.meta.url).pathname;

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
  const j = await r.json();
  if (!j || j.error) throw new Error('setTheme ' + id + ' failed: ' + JSON.stringify(j).slice(0, 200));
  return j.version;
}

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const results = [];
for (const id of THEMES) {
  const ver = await setTheme(id);
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
  page.on('pageerror', e => errs.push('PAGEERR ' + e.message));
  await page.goto(ORIGIN + BOOK, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(9000);
  const m = await page.evaluate(() => {
    const bc = document.querySelector('.book-content');
    const p = document.querySelector('.book-content p');
    const rc = document.querySelector('.reader-container');
    const h1 = document.querySelector('.book-content h1, .book-content h2, .book-content h3');
    const st = document.getElementById('zy-reader-css');
    const cs = p ? getComputedStyle(p) : null;
    const bs = bc ? getComputedStyle(bc) : null;
    return {
      cssVer: st ? (st.textContent || '').match(/v(\d+)/)?.[1] : null,
      cssHasTheme: st ? /theme: (\w+)/.exec(st.textContent || '')?.[1] : null,
      readerBg: rc ? getComputedStyle(rc).backgroundColor : null,
      cardBg: bs ? bs.backgroundColor : null,
      cardW: bc ? Math.round(bc.getBoundingClientRect().width) : null,
      cardPad: bs ? bs.padding : null,
      fontFamily: cs ? cs.fontFamily.slice(0, 46) : null,
      color: cs ? cs.color : null,
      lineHeight: cs ? cs.lineHeight : null,
      fontSize: cs ? cs.fontSize : null,
      hColor: h1 ? getComputedStyle(h1).color : null,
      pText: p ? (p.textContent || '').slice(0, 24) : null,
    };
  });
  const shot = OUT + 'theme-' + id + '.png';
  await page.screenshot({ path: shot, fullPage: false });
  results.push({ id, version: ver, ...m, shot, errs: errs.slice(0, 3) });
  await ctx.close();
}
await browser.close();
writeFileSync(OUT + 'theme-results.json', JSON.stringify(results, null, 1));
console.log(JSON.stringify(results, null, 1));
