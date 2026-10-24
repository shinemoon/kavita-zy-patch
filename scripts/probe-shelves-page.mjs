import { chromium } from '/home/youruser/.picoclaw/workspace/skills/paperecho-receiver/bin/node_modules/playwright/index.mjs';

const b = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
const ctx = await b.newContext({
  userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
  viewport: { width: 1440, height: 900 },
});
const p = await ctx.newPage();

// Capture every XHR/JSON call to zhangyue
const apiCalls = [];
p.on('response', async (r) => {
  const u = r.url();
  if (!/zhangyue|ireader/.test(u)) return;
  if (/\.(png|jpg|jpeg|gif|css|js|woff|svg|ico)/.test(u)) return;
  const ct = r.headers()['content-type'] || '';
  if (!/json|octet|binary|stream/.test(ct) && !/chapter|book|download|content|section|read/.test(u)) return;
  let body = '';
  try { body = (await r.text()).slice(0, 200); } catch {}
  apiCalls.push({ url: u, status: r.status(), ct, bodyPreview: body });
});
p.on('request', (r) => {
  const u = r.url();
  if (!/zhangyue|ireader/.test(u)) return;
  if (/\.(png|jpg|jpeg|gif|css|js|woff|svg|ico)/.test(u)) return;
  console.log('REQ', r.method(), u.slice(0, 200));
});

await ctx.addCookies([
  { name: 'zysid', value: '__ZY_ZYSID__', domain: '.zhangyue.com', path: '/', secure: true, sameSite: 'None' },
]);

console.log('=== goto bookshelf (with zysid cookie) ===');
await p.goto('https://pc.ireader.com/bookshelf', { waitUntil: 'domcontentloaded', timeout: 30000 });
await p.waitForTimeout(6000);

const txt = (await p.evaluate(() => document.body.innerText)).slice(0, 1000);
console.log('BODY:\n', txt);

console.log('=== looking for a book link ===');
const links = await p.$$eval('a, [role=link]', (els) =>
  els.map((e) => ({ href: e.getAttribute('href') || '', txt: (e.innerText || '').trim().slice(0, 60) }))
    .filter((x) => x.href && /book|read|chapter|detail/i.test(x.href)).slice(0, 20)
);
for (const l of links) console.log('  LINK', l.href.slice(0, 100), '|', l.txt);

console.log('=== captured API calls:', apiCalls.length);
for (const c of apiCalls) {
  console.log('  ', c.status, c.url.slice(0, 150));
  if (c.bodyPreview) console.log('     →', c.bodyPreview.slice(0, 180));
}

await p.screenshot({ path: '/tmp/zy-shelf-shot.png', fullPage: false });
console.log('SCREENSHOT /tmp/zy-shelf-shot.png');
await b.close();