// 找出 Kavita 界面里"悬浮"的元素 (position:fixed/sticky 且可见), 特别关注图标。
// 用法: node src/probe-float-icons.mjs [origin] [路由...]
import { chromium } from 'playwright';
import { execFileSync } from 'child_process';

const ORIGIN = process.argv[2] || 'http://127.0.0.1:5000';
const ROUTES = process.argv.slice(3);
if (!ROUTES.length) ROUTES.push('/', '/bookmarks', '/library/0');

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

const width = parseInt(process.env.VW || '1400', 10);
const height = parseInt(process.env.VH || '900', 10);

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width, height } });
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

const SCAN = () => {
  const out = { fixed: [], icons: [] };
  const vw = window.innerWidth, vh = window.innerHeight;
  const all = document.querySelectorAll('body *');
  for (const el of all) {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) === 0) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) continue;
    const pos = cs.position;
    const floating = pos === 'fixed' || pos === 'sticky' || pos === 'absolute';
    const inView = r.right > 0 && r.bottom > 0 && r.left < vw && r.top < vh;
    if (floating && inView) {
      // 只看"像浮层"的: absolute 只保留 z-index 高的 / 带图标且脱离文档流的
      const z = parseInt(cs.zIndex, 10);
      const isIcon = !!el.querySelector('i,mat-icon,fa-icon,svg') || /fa-|icon/i.test(el.className || '');
      if (pos !== 'absolute' || isIcon || (!isNaN(z) && z > 100)) {
        out.fixed.push({
          tag: el.tagName.toLowerCase(),
          id: el.id || '',
          cls: (typeof el.className === 'string' ? el.className : '').slice(0, 90),
          pos, z: cs.zIndex,
          rect: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)],
          text: (el.textContent || '').trim().slice(0, 40),
          icon: (el.querySelector('i') || {}).className || '',
          path: (function (n) { const p = []; while (n && n !== document.body && p.length < 4) { p.unshift(n.tagName.toLowerCase() + (n.id ? '#' + n.id : '') + (typeof n.className === 'string' && n.className ? '.' + n.className.split(' ').filter(Boolean).slice(0, 2).join('.') : '')); n = n.parentElement; } return p.join(' > '); })(el),
        });
      }
    }
    // 所有 bookmark 图标
    const cls = typeof el.className === 'string' ? el.className : '';
    if (/fa-bookmark|bookmark/i.test(cls) || /fa-bookmark/.test((el.innerHTML || '').slice(0, 200))) {
      out.icons.push({
        tag: el.tagName.toLowerCase(), id: el.id || '', cls: cls.slice(0, 90), pos, z: cs.zIndex,
        rect: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)],
        text: (el.textContent || '').trim().slice(0, 30),
      });
    }
  }
  return out;
};

for (const path of ROUTES) {
  await page.goto(ORIGIN + path, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(e => errs.push('GOTO ' + path + ' ' + e.message));
  await page.waitForTimeout(4500);
  const data = await page.evaluate(SCAN);
  console.log('\n===== ' + path + '  (viewport ' + width + 'x' + height + ') =====');
  console.log('-- 浮层 --');
  for (const f of data.fixed) console.log(JSON.stringify(f));
  console.log('-- bookmark 相关节点 (' + data.icons.length + ') --');
  for (const f of data.icons.slice(0, 30)) console.log(JSON.stringify(f));
}
if (errs.length) console.log('\nERRORS:\n' + errs.slice(0, 10).join('\n'));
await browser.close();
