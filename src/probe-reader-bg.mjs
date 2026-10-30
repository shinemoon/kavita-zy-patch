// 考察 Kavita 阅读器背景层: 祖先链 computed bg + 命中的 background 规则(含 !important)
// 用法: node src/probe-reader-bg.mjs [origin] [bookPath]
import { chromium } from 'playwright';
import { execFileSync } from 'child_process';

const ORIGIN = process.argv[2] || 'http://127.0.0.1:5000';
const BOOK = process.argv[3] || '/library/1/series/27/book/31';

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
await page.goto(ORIGIN + BOOK, { waitUntil: 'domcontentloaded', timeout: 30000 });
await page.waitForTimeout(9000);

const out = await page.evaluate(() => {
  const res = { chain: [], rules: [], sheets: [], classes: {} };

  // 1. 祖先链: 从第一个 .book-content 段落往上爬到 html
  const p = document.querySelector('.book-content p') || document.querySelector('.book-content');
  let el = p;
  const seen = [];
  while (el && el.nodeType === 1 && seen.length < 20) {
    const cs = getComputedStyle(el);
    seen.push({
      tag: el.tagName.toLowerCase(),
      cls: (el.className || '').toString().slice(0, 90),
      id: el.id || null,
      bgColor: cs.backgroundColor,
      bgImage: cs.backgroundImage === 'none' ? null : cs.backgroundImage.slice(0, 60),
      color: cs.color,
      w: Math.round(el.getBoundingClientRect().width),
      h: Math.round(el.getBoundingClientRect().height),
    });
    el = el.parentElement;
  }
  res.chain = seen;

  // 2. 阅读器相关的顶层容器类名(看有哪些候选选择器)
  ['reader-container', 'book-content', 'book-container', 'reader', 'epub'].forEach(k => {
    res.classes[k] = document.querySelectorAll('.' + k).length;
  });
  res.readerLike = Array.from(document.querySelectorAll('[class*=reader], [class*=book-content]'))
    .slice(0, 12).map(e => e.tagName.toLowerCase() + '.' + (e.className || '').toString().slice(0, 80));

  // 3. 遍历所有样式表, 收集带 background 的规则
  for (const sh of Array.from(document.styleSheets)) {
    let href = sh.href ? sh.href.split('/').pop() : 'inline(#' + (sh.ownerNode && sh.ownerNode.id || '?') + ')';
    let rules;
    try { rules = sh.cssRules; } catch (e) { res.sheets.push({ href, err: 'CORS' }); continue; }
    let cnt = 0;
    for (const r of Array.from(rules || [])) {
      if (!r.style) continue;
      const txt = r.cssText || '';
      if (!/background/i.test(txt)) continue;
      cnt++;
      res.rules.push({
        sheet: href,
        sel: (r.selectorText || '').slice(0, 160),
        important: /!important/.test(txt),
        bg: r.style.backgroundColor || '',
        bgImg: (r.style.backgroundImage || '').slice(0, 50),
        color: r.style.color || '',
      });
    }
    res.sheets.push({ href, rules: (rules || []).length, bgRules: cnt });
  }

  // 4. 只留跟阅读器相关的 (选择器命中 .book-content / .reader-container)
  res.rulesHit = res.rules.filter(r =>
    /book-content|reader-container|brtheme/.test(r.sel) &&
    (r.important || /white|#fff|black|#000/i.test(r.bg))
  ).slice(0, 40);
  delete res.rules;
  return res;
});

console.log(JSON.stringify(out, null, 1));
await page.screenshot({ path: '/tmp/kv-reader-bg.png' });
await browser.close();
