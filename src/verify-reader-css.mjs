// 验证「阅读样式增强」: 打开 Kavita 阅读器, 看 __ZY__.readerCss() 注入结果 +
// .book-content 上实际算出来的 line-height。用法: node src/verify-reader-css.mjs [url]
import { chromium } from 'playwright';
import { execFileSync } from 'child_process';

const ORIGIN = process.argv[2] || 'http://127.0.0.1:5000';
const BOOK = process.argv[3] || '/library/1/series/27/book/31';

const acct = JSON.parse(execFileSync('python3', ['-c', `
import sqlite3, json, shutil, tempfile, os
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
page.on('pageerror', e => errs.push('PAGEERR ' + e.message));
page.on('console', m => { if (m.type() === 'error') errs.push('CONSOLE ' + m.text().slice(0, 160)); });

await page.goto(ORIGIN + BOOK, { waitUntil: 'domcontentloaded', timeout: 30000 });
await page.waitForTimeout(9000);

const out = await page.evaluate(() => {
  const st = document.getElementById('zy-reader-css');
  const p = document.querySelector('.book-content p');
  const cs = p ? getComputedStyle(p) : null;
  return {
    jsVer: window.__ZY__ ? window.__ZY__.ver : null,
    readerCss: window.__ZY__ && window.__ZY__.readerCss ? window.__ZY__.readerCss() : null,
    styleTag: st ? { len: (st.textContent || '').length, head: (st.textContent || '').slice(0, 120) } : null,
    pText: p ? (p.textContent || '').slice(0, 40) : null,
    lineHeight: cs ? cs.lineHeight : null,
    fontSize: cs ? cs.fontSize : null,
    inlineLH: p ? (p.getAttribute('style') || '') : null,
  };
});
console.log(JSON.stringify(out, null, 1));
console.log('errs:', JSON.stringify(errs.slice(0, 6)));
await browser.close();
