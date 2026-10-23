/* 登录态下验证: 补丁 link 是否真出现在侧边栏 (复用 e2e-check.mjs 的登录构造) */
import { chromium } from 'playwright';
import { execFileSync } from 'child_process';

const acct = JSON.parse(execFileSync('python3', ['-c', `
import sqlite3, json
c = sqlite3.connect('file:/vol1/1001/APPS/kavita/kavita.db?mode=ro', uri=True)
u = c.execute('select Id, UserName, Email from AspNetUsers order by Id limit 1').fetchone()
k = c.execute("select Key from AppUserAuthKey where Name='opds' limit 1").fetchone()
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
const ctx = await browser.newContext({ viewport: { width: 1500, height: 950 } });

await ctx.route('**/api/**', route => {
  const h = { ...route.request().headers(), 'X-Api-Key': acct.apiKey };
  if (/\/api\/Account$/.test(route.request().url())) {
    return route.fulfill({ status: 200, contentType: 'application/json',
                          body: JSON.stringify(ACCOUNT) });
  }
  return route.continue({ headers: h });
});
await ctx.addInitScript(u => {
  localStorage.setItem('kavita-user', JSON.stringify(u));
  localStorage.setItem('kavita-lastlogin', u.username);
}, ACCOUNT);

const page = await ctx.newPage();
const errs = [];
page.on('pageerror', e => errs.push('PAGEERROR: ' + e.message));

await page.goto('http://127.0.0.1:5000/', { waitUntil: 'networkidle', timeout: 45000 });
await page.waitForTimeout(6000);

console.log('URL:', page.url());

console.log('--- __ZY__ ---');
console.log(JSON.stringify(await page.evaluate(() => {
  const z = window.__ZY__;
  return z ? { ver: z.ver, console: z.console, health: z.health } : null;
})));

console.log('--- #zy-nav-item ---');
console.log(JSON.stringify(await page.evaluate(() => {
  const e = document.getElementById('zy-nav-item');
  if (!e) return 'NOT PRESENT';
  const r = e.getBoundingClientRect();
  return { href: e.getAttribute('href'), text: (e.textContent||'').trim(),
           visible: r.width > 0 && r.height > 0,
           rect: { x: Math.round(r.x), y: Math.round(r.y),
                   w: Math.round(r.width), h: Math.round(r.height) },
           parentCls: e.parentElement ? e.parentElement.className : null };
}), null, 1));

console.log('--- side nav items ---');
console.log(JSON.stringify(await page.$$eval('a.side-nav-item', as =>
  as.map(a => ({ href: a.getAttribute('href'), text: (a.textContent||'').trim().slice(0,12) }))
), null, 1));

console.log('errors:', errs.slice(0,5).join(' | ') || '(none)');

await page.screenshot({ path: '/vol2/1001/kavita-zy-patch/state/shots/verify-nav.png' });
await browser.close();