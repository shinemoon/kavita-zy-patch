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

const browser = await chromium.launch();

for (const [w, h, tag] of [[1500, 950, 'desktop'], [430, 900, 'mobile']]) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h } });
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
  await page.goto('http://127.0.0.1:5000/all-series', { waitUntil: 'networkidle', timeout: 45000 });
  await page.waitForTimeout(4000);
  const info = await page.evaluate(() => {
    const t = document.getElementById('zy-top-item');
    const n = document.getElementById('zy-nav-item');
    const rr = e => { if (!e) return null; const r = e.getBoundingClientRect();
      const cs = getComputedStyle(e);
      return { w: Math.round(r.width), h: Math.round(r.height), x: Math.round(r.x), y: Math.round(r.y),
               disp: cs.display, vis: cs.visibility, op: cs.opacity }; };
    return { ver: window.__ZY__ && window.__ZY__.ver, top: rr(t), nav: rr(n),
             topText: t ? t.textContent.trim() : null };
  });
  await page.screenshot({ path: `/tmp/zy-${tag}.png` });
  console.log(tag, JSON.stringify(info), 'errs=', JSON.stringify(errs));
  const tt = await page.$('#zy-top-item');
  if (tt) await tt.screenshot({ path: `/tmp/zy-top-${tag}.png` }).catch(() => {});
  await ctx.close();
}
await browser.close();
