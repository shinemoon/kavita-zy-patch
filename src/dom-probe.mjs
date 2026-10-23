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
const ctx = await browser.newContext({ viewport: { width: 1500, height: 950 } });
await ctx.route('**/api/**', route => {
  const h = { ...route.request().headers(), 'X-Api-Key': acct.apiKey };
  if (/\/api\/Account$/.test(route.request().url())) {
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(ACCOUNT) });
  }
  return route.continue({ headers: h });
});
await ctx.addInitScript(u => {
  localStorage.setItem('kavita-user', JSON.stringify(u));
  localStorage.setItem('kavita-lastlogin', u.username);
}, ACCOUNT);

const page = await ctx.newPage();
await page.goto('http://127.0.0.1:5000/all-series', { waitUntil: 'networkidle', timeout: 45000 });
await page.waitForTimeout(6000);

const dump = await page.evaluate(() => {
  const out = { zy: null, cardTitles: [], pills: 0 };
  out.pills = document.querySelectorAll('.zy-pill').length;
  out.zy = window.__ZY__
    ? {
        ver: window.__ZY__.ver,
        loaded: window.__ZY__.cache.loaded,
        loadErr: window.__ZY__.cache.loadErr,
        links: Object.keys(window.__ZY__.cache.links),
        warns: (window.__ZY__.cache.warns || []).map(w => w.code)
      }
    : 'NO __ZY__';
  document.querySelectorAll('.card-title').forEach(t => {
    out.cardTitles.push({
      cls: t.className,
      html: t.innerHTML.slice(0, 160),
      hasPill: !!t.querySelector('.zy-pill')
    });
  });
  return out;
});
console.log('zy   =', JSON.stringify(dump.zy));
console.log('pills=', dump.pills);
console.log('cardTitles:');
dump.cardTitles.slice(0, 3).forEach(c => console.log('  ', JSON.stringify(c)));
await browser.close();