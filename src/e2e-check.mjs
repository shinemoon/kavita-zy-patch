import { chromium } from 'playwright';
import { execFileSync } from 'child_process';
import fs from 'fs';

const shots = '/vol2/1001/kavita-zy-patch/state/shots';
fs.mkdirSync(shots, { recursive: true });

const acct = JSON.parse(execFileSync('python3', ['-c', `
import sqlite3, json
c = sqlite3.connect('file:/vol1/1001/APPS/kavita/kavita.db?mode=ro', uri=True)
u = c.execute('select Id, UserName, Email from AspNetUsers order by Id limit 1').fetchone()
k = c.execute("select Key from AppUserAuthKey where Name='opds' limit 1").fetchone()
print(json.dumps({'id': u[0], 'username': u[1], 'email': u[2], 'apiKey': k[0] if k else ''}))
`], { encoding: 'utf8' }));

console.log('user =', acct.username);

const ACCOUNT = {
  id: acct.id,
  oidcId: null,
  username: acct.username,
  email: acct.email,
  roles: ['Admin', 'Download', 'ChangeRestriction', 'Login'],
  token: 'e2e-fake-token',
  refreshToken: '',
  apiKey: acct.apiKey,
  preferences: {
    theme: {
      id: 2, name: 'Aquamarine', normalizedName: 'aquamarine',
      fileName: 'aquamarine.css', isDefault: true, description: '',
      previewUrls: [], selector: 'aquamarine', coverImage: ''
    },
    backgroundImageUrls: []
  },
  readingProfiles: []
};

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1500, height: 950 } });

// Kavita 后端认 X-Api-Key (AppUserAuthKey 里 'opds' 那条)。
// 前端只会发 Bearer JWT, 这里给每个 /api/ 请求补上真 key。
await ctx.route('**/api/**', route => {
  const h = { ...route.request().headers(), 'X-Api-Key': acct.apiKey };
  if (/\/api\/Account$/.test(route.request().url())) {
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(ACCOUNT)
    });
  }
  return route.continue({ headers: h });
});
await ctx.addInitScript(u => {
  localStorage.setItem('kavita-user', JSON.stringify(u));
  localStorage.setItem('kavita-lastlogin', u.username);
}, ACCOUNT);

const page = await ctx.newPage();
const errs = [];
page.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
page.on('pageerror', e => errs.push('PAGEERROR: ' + e.message));

async function visit(url, name, wait = 4500) {
  await page.goto(url, { waitUntil: 'networkidle', timeout: 45000 });
  await page.waitForTimeout(wait);
  await page.screenshot({ path: `${shots}/${name}.png` });
}

await visit('http://127.0.0.1:5000/', '01-home', 6000);
console.log('url         =', page.url());
console.log('login_form  =', await page.locator('input[type=password]').count());
console.log('nav_item    =', await page.locator('#zy-nav-item').count());
console.log('pills       =', await page.locator('.zy-pill').count());
console.log('banner      =', await page.locator('#zy-banner').count());
if (await page.locator('#zy-banner').count()) {
  console.log('banner_txt  =', (await page.locator('#zy-banner').innerText()).slice(0, 200).replace(/\n/g, ' '));
}

if (await page.locator('#zy-nav-item').count()) {
  await page.click('#zy-nav-item');
  await page.waitForTimeout(3500);
  await page.screenshot({ path: `${shots}/02-modal.png` });
  console.log('modal_rows  =', await page.locator('#zy-table tbody tr').count());
  console.log('modal_txt   =', (await page.locator('#zy-body').innerText()).slice(0, 400).replace(/\n/g, ' | '));
  const diag = page.locator('#zy-body [data-act=diagnose]');
  if (await diag.count()) {
    await diag.click();
    await page.waitForTimeout(2500);
    await page.screenshot({ path: `${shots}/02b-diagnose.png` });
    console.log('diagnose    =', (await page.locator('#zy-body').innerText()).slice(0, 500).replace(/\n/g, ' | '));
  }
  await page.keyboard.press('Escape');
  await page.waitForTimeout(500);
}

await visit('http://127.0.0.1:5000/all-series', '03-all-series', 6000);
console.log('all-series pills =', await page.locator('.zy-pill').count());
console.log('all-series nav   =', await page.locator('#zy-nav-item').count());

await visit('http://127.0.0.1:5000/series/21', '04-series21', 5500);
console.log('series21 pills =', await page.locator('.zy-pill').count());
if (await page.locator('.zy-pill').count()) {
  console.log('pill_txt =', await page.locator('.zy-pill').first().innerText());
}

console.log('ERRORS =', errs.length);
errs.slice(0, 12).forEach(e => console.log('  !', e.slice(0, 180)));
await browser.close();