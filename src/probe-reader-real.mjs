// 用真实 /api/Account 响应(不伪造) 打开阅读器, 看用户实际 book theme / 底色
import { chromium } from 'playwright';
import { execFileSync } from 'child_process';

const ORIGIN = 'http://127.0.0.1:5000';

const acct = JSON.parse(execFileSync('python3', ['-c', `
import sqlite3, json, tempfile, os, urllib.request
src='/vol1/1001/APPS/kavita/kavita.db'
dst=os.path.join(tempfile.mkdtemp(),'k.db')
s=sqlite3.connect(src); d=sqlite3.connect(dst); s.backup(d); d.close(); s.close()
c=sqlite3.connect(dst)
key=c.execute("select Key from AppUserAuthKey where Name='opds' limit 1").fetchone()[0]
req=urllib.request.Request('http://127.0.0.1:5000/api/Account', headers={'x-api-key': key})
d=json.loads(urllib.request.urlopen(req, timeout=20).read())
d['token']='e2e-fake-token'
print(json.dumps({'apiKey': key, 'account': d}))
`], { encoding: 'utf8', maxBuffer: 8 * 1024 * 1024 }));

const ACCOUNT = acct.account;

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
await ctx.route('**/api/**', route => {
  const hd = { ...route.request().headers(), 'X-Api-Key': acct.apiKey };
  return route.continue({ headers: hd });
});
await ctx.addInitScript(u => {
  localStorage.setItem('kavita-user', JSON.stringify(u));
  localStorage.setItem('kavita-lastlogin', u.username);
}, ACCOUNT);

const page = await ctx.newPage();
await page.goto(ORIGIN + '/library/1/series/26/book/30', { waitUntil: 'domcontentloaded', timeout: 30000 });
await page.waitForTimeout(11000);

const out = await page.evaluate(() => {
  const rc = document.querySelector('.reader-container');
  const bc = document.querySelector('.book-content');
  const p = Array.from(document.querySelectorAll('.book-content p')).find(e => (e.textContent || '').trim().length > 20);
  const themes = [];
  for (const sh of Array.from(document.styleSheets)) {
    const id = sh.ownerNode && sh.ownerNode.id;
    if (id && /^brtheme/.test(id)) {
      let txt = '';
      try { txt = Array.from(sh.cssRules).map(r => r.cssText).join('\n'); } catch (e) { txt = 'cors'; }
      themes.push({ id, txt: txt.slice(0, 2500) });
    }
  }
  return {
    bodyCls: document.body.className,
    readerBg: rc ? getComputedStyle(rc).backgroundColor : null,
    bookContentBg: bc ? getComputedStyle(bc).backgroundColor : null,
    pColor: p ? getComputedStyle(p).color : null,
    // book-content 用的变量
    vars: rc ? {
      brbg: getComputedStyle(document.body).getPropertyValue('--brtheme-bg-color').trim(),
      bartx: getComputedStyle(document.body).getPropertyValue('--brtheme-text-color').trim(),
    } : null,
    themes,
  };
});
console.log(JSON.stringify(out, null, 1));
await page.screenshot({ path: '/tmp/kv-real-reader.png' });
await browser.close();
