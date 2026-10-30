// 验证「关键词高亮」滤镜: 打开 Kavita 阅读器, 看 __ZY__.kw() 的真实命中数。
// 关键断言: highlight 模式下 DOM 里**不应出现**任何 .zy-kw span (纯滤镜),
//           且 CSS.highlights 里挂着按分类的 Highlight 对象。
//
// 用法: node src/probe-keyword-mark.mjs [origin] [seriesId] [bookId] [mode]
import { chromium } from 'playwright';
import { execFileSync } from 'child_process';

const ORIGIN = process.argv[2] || 'http://127.0.0.1:5000';
const SERIES = process.argv[3] || '25';
const BOOK = process.argv[4] || '29';
const MODE = process.argv[5] || '';
const URL = `${ORIGIN}/library/1/series/${SERIES}/book/${BOOK}`;

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

if (MODE) {
  // 让探针自己切一次模式 (走 5012 的 API 太重, 这里用客户端 override)
  await ctx.addInitScript(m => { window.__ZY_KW_FORCE_MODE__ = m; }, MODE);
}

await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 30000 });
await page.waitForTimeout(11000);

const out = await page.evaluate(() => {
  const kw = window.__ZY__ && window.__ZY__.kw ? window.__ZY__.kw() : null;
  const box = document.querySelector('.book-content');
  const txt = box ? (box.textContent || '') : '';
  const hlNames = (window.CSS && window.CSS.highlights) ? [...window.CSS.highlights.keys()] : [];
  const perCat = {};
  hlNames.forEach(n => {
    const h = window.CSS.highlights.get(n);
    const rs = h ? [...h] : [];
    perCat[n] = { ranges: rs.length,
                  sample: rs.slice(0, 3).map(r => r.toString().slice(0, 24)) };
  });
  let hlRules = 0;
  try {
    for (const sh of document.styleSheets) {
      let rules = [];
      try { rules = [...sh.cssRules]; } catch (e) { continue; }
      rules.forEach(r => { if ((r.selectorText || '').indexOf('::highlight(') >= 0) hlRules++; });
    }
  } catch (e) {}
  const sample = (txt.match(/潘多拉|圣诞|母亲|学校|狗|爸爸|阿德里安/g) || []);
  return {
    jsVer: window.__ZY__ ? window.__ZY__.ver : null,
    kw,
    boxChars: txt.length,
    spanCount: document.querySelectorAll('.zy-kw').length,
    styleTag: !!document.getElementById('zy-kw-css'),
    styleBytes: (document.getElementById('zy-kw-css') || {}).textContent?.length || 0,
    highlights: hlNames,
    perCat,
    hlRules,
    sampleHits: sample.slice(0, 8),
  };
});
console.log(JSON.stringify(out, null, 1));
console.log('errs:', JSON.stringify(errs.slice(0, 5)));

await page.screenshot({ path: '/tmp/kw-reader.png', fullPage: false });
await browser.close();
