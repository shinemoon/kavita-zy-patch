// 活更新验证: 页面开着, 改配置, 等 65s 看 style 是否自动换 (不刷新页面)
import { chromium } from 'playwright';
import { execFileSync } from 'child_process';

const ORIGIN = 'http://127.0.0.1:5000';
const BOOK = '/library/1/series/27/book/31';
const TOKEN = execFileSync('bash', ['-lc', 'cat /vol2/1001/kavita-zy-patch/state/token'], { encoding: 'utf8' }).trim();

const acct = JSON.parse(execFileSync('python3', ['-c', `
import sqlite3, json, tempfile, os
src='/vol1/1001/APPS/kavita/kavita.db'; dst=os.path.join(tempfile.mkdtemp(),'k.db')
s=sqlite3.connect(src); d=sqlite3.connect(dst); s.backup(d); d.close(); s.close()
c=sqlite3.connect(dst)
u=c.execute('select Id, UserName, Email from AspNetUsers order by Id limit 1').fetchone()
k=c.execute("select Key from AppUserAuthKey where Name='opds' limit 1").fetchone()
print(json.dumps({'id': u[0], 'username': u[1], 'email': u[2], 'apiKey': k[0] if k else ''}))
`], { encoding: 'utf8' }));

const ACCOUNT = { id: acct.id, oidcId: null, username: acct.username, email: acct.email,
  roles: ['Admin', 'Download', 'ChangeRestriction', 'Login'], token: 'e2e-fake-token',
  refreshToken: '', apiKey: acct.apiKey,
  preferences: { theme: { id: 2, name: 'Aquamarine', normalizedName: 'aquamarine',
    fileName: 'aquamarine.css', isDefault: true, description: '', previewUrls: [],
    selector: 'aquamarine', coverImage: '' }, backgroundImageUrls: [] }, readingProfiles: [] };

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
await page.waitForTimeout(8000);

const read = () => page.evaluate(() => {
  const p = document.querySelector('.book-content p');
  return { lh: p ? getComputedStyle(p).lineHeight : null,
           ver: document.getElementById('zy-reader-css')
                ? (document.getElementById('zy-reader-css').textContent.match(/v(\d+)/) || [])[1] : null };
});
console.log('before:', JSON.stringify(await read()));

const r = await fetch('http://127.0.0.1:5012/api/reader_css', {
  method: 'POST', headers: { 'Content-Type': 'application/json', 'X-ZY-Token': TOKEN },
  body: JSON.stringify({ op: 'line_height', value: '1.55' }) });
const j = await r.json();
console.log('saved version', j.version, JSON.stringify(j.items[0].decls));

for (const t of [20, 40, 70]) {
  await page.waitForTimeout(t === 20 ? 20000 : 20000);
  console.log('+' + t + 's:', JSON.stringify(await read()));
}
await browser.close();
