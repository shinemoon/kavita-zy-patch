// 观察 .bookmark-overlay 的"出现时机"和"位置漂移"。
// 用法: node src/probe-bm-timing.mjs [origin] [bookPath]
// 打开漫画阅读器 (incognito, 不写进度), 每 100ms 采样一次 overlay 数量/位置,
// 直到稳定; 然后翻一页再采样。
import { chromium } from 'playwright';
import { execFileSync } from 'child_process';

const ORIGIN = process.argv[2] || 'http://127.0.0.1:5000';
const BOOK = process.argv[3] || '/library/1/series/29/book/36?incognitoMode=true';

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
page.on('pageerror', e => console.log('[pageerror]', e.message));

const SAMPLE = () => {
  const out = { t: Math.round(performance.now()), n: 0, page: null, rows: [] };
  try { out.page = document.querySelector('.book-content') ? 'ok' : 'none'; } catch (e) {}
  const ovs = document.querySelectorAll('.bookmark-overlay');
  out.n = ovs.length;
  for (const ov of ovs) {
    const r = ov.getBoundingClientRect();
    const host = ov.parentElement;
    const img = host ? host.querySelector('img') : null;
    const ir = img ? img.getBoundingClientRect() : null;
    out.rows.push({
      cls: ov.className.replace('bookmark-overlay ', '').replace(' fa-bookmark', ''),
      ov: [r.left, r.top, r.width, r.height].map(Math.round),
      img: ir ? [ir.left, ir.top, ir.width, ir.height].map(Math.round) : null,
      dR: ir ? Math.round(ir.right - r.right) : null,
      dB: ir ? Math.round(ir.bottom - r.bottom) : null,
      pinned: ov.getAttribute('data-zy-bm') ? 1 : 0,
      vis: r.width > 0 && r.height > 0,
    });
  }
  return out;
};

await page.goto(ORIGIN + BOOK, { waitUntil: 'domcontentloaded', timeout: 30000 });

const series = [];
for (let i = 0; i < 40; i++) {
  series.push(await page.evaluate(SAMPLE));
  await page.waitForTimeout(100);
}
console.log('=== 进书后 4 秒采样 (每 100ms) ===');
for (const s of series) {
  const k = s.rows.map(r => `${r.cls[9] || r.cls}@${r.ov[0]},${r.ov[1]} dR${r.dR} dB${r.dB} pin${r.pinned} img${r.img ? r.img[2] + 'x' + r.img[3] : '-'}`).join(' | ');
  console.log(`t=${s.t} n=${s.n} ${k}`);
}

console.log('\n=== 稳定后 3 秒 ===');
for (let i = 0; i < 10; i++) { console.log(JSON.stringify(await page.evaluate(SAMPLE))); await page.waitForTimeout(300); }

// 翻页: 点右下角或按键
console.log('\n=== 按 ArrowRight 翻页 ===');
await page.keyboard.press('ArrowRight');
const after = [];
for (let i = 0; i < 40; i++) { after.push(await page.evaluate(SAMPLE)); await page.waitForTimeout(100); }
for (const s of after) {
  const k = s.rows.map(r => `${r.cls[9] || r.cls}@${r.ov[0]},${r.ov[1]} dR${r.dR} dB${r.dB} pin${r.pinned} img${r.img ? r.img[2] + 'x' + r.img[3] : '-'}`).join(' | ');
  console.log(`t=${s.t} n=${s.n} ${k}`);
}
console.log('url:', page.url());

await browser.close();
