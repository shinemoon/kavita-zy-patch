import { chromium } from '/home/youruser/.picoclaw/workspace/skills/paperecho-receiver/bin/node_modules/playwright/index.mjs';
import fs from 'fs';

const pps = fs.readFileSync('/tmp/zy-pps.txt', 'utf8').trim();

const b = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
const ctx = await b.newContext({
  userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
  viewport: { width: 1600, height: 1000 },
});
await ctx.addCookies([
  { name: '_zypps', value: pps, domain: '.ireader.com', path: '/' },
  { name: 'zysid', value: '__ZY_ZYSID__', domain: '.zhangyue.com', path: '/' },
  { name: 'zysid', value: '__ZY_ZYSID__', domain: '.ireader.com', path: '/' },
]);

const p = await ctx.newPage();
const api = [];
p.on('response', async (r) => {
  const u = r.url();
  if (!/zhangyue\.com|ireader\.com/.test(u)) return;
  if (/\.(png|jpg|jpeg|gif|css|js|woff2?|svg|ico|map)/.test(u)) return;
  let txt = '';
  try { txt = (await r.text()).slice(0, 700); } catch {}
  api.push({ u, s: r.status(), t: txt });
  console.log('RESP', r.status(), u.slice(0, 160));
  if (txt) console.log('    ', txt.replace(/\s+/g, ' ').slice(0, 400));
});

await p.goto('https://pc.ireader.com/bookshelf', { waitUntil: 'networkidle', timeout: 45000 });
await p.waitForTimeout(4000);

console.log('\n=== BODY TEXT ===');
console.log((await p.evaluate(() => document.body.innerText)).slice(0, 1200));

console.log('\n=== clickable book elements ===');
const items = await p.$$eval('[class*=book], li, .card, a', (els) =>
  els.map((e) => ({ cls: (e.className || '').toString().slice(0, 40), txt: (e.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 50), href: e.getAttribute && e.getAttribute('href') }))
    .filter((x) => x.txt && /狐的故事|剑来|藤井/.test(x.txt)).slice(0, 20)
);
for (const it of items) console.log('  ', JSON.stringify(it));

await p.screenshot({ path: '/tmp/zy-shelf3.png', fullPage: false });
console.log('\nshot /tmp/zy-shelf3.png ; api calls =', api.length);
fs.writeFileSync('/tmp/zy-api.json', JSON.stringify(api, null, 2));
await b.close();