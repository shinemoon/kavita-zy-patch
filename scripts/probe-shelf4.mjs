import { chromium } from '/home/youruser/.picoclaw/workspace/skills/paperecho-receiver/bin/node_modules/playwright/index.mjs';
import fs from 'fs';

const pps = fs.readFileSync('/tmp/zy-pps.txt', 'utf8').trim();
const COOKIE = '_zypps=' + pps + '; zysid=__ZY_ZYSID__';

const b = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
const ctx = await b.newContext({
  userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
  viewport: { width: 1600, height: 1000 },
  extraHTTPHeaders: { Cookie: COOKIE },
});
const p = await ctx.newPage();
const api = [];
p.on('response', async (r) => {
  const u = r.url();
  if (!/zhangyue\.com|ireader\.com/.test(u)) return;
  if (/\.(png|jpg|jpeg|gif|css|js|woff2?|svg|ico|map)/.test(u)) return;
  let txt = '';
  try { txt = (await r.text()).slice(0, 900); } catch {}
  api.push({ url: u, status: r.status(), body: txt });
  console.log('RESP', r.status(), u.slice(0, 170));
  if (txt) console.log('    >>', txt.replace(/\s+/g, ' ').slice(0, 500));
});

await p.goto('https://pc.ireader.com/bookshelf', { waitUntil: 'networkidle', timeout: 45000 });
await p.waitForTimeout(5000);

const body = await p.evaluate(() => document.body.innerText);
console.log('\n=== BODY (first 900) ===\n', body.slice(0, 900));

// Look for the shelf books
const info = await p.evaluate(() => {
  const out = { links: [], vue: null };
  document.querySelectorAll('a').forEach((a) => {
    const h = a.getAttribute('href') || '';
    const t = (a.innerText || '').trim().replace(/\s+/g, ' ');
    if (h) out.links.push({ href: h.slice(0, 120), txt: t.slice(0, 60) });
  });
  const app = document.querySelector('#__nuxt');
  if (app && app.__vue__) {
    try { out.vue = JSON.stringify(app.__vue__.$store.state).slice(0, 1500); } catch (e) { out.vue = 'err ' + e.message; }
  }
  return out;
});
console.log('\n=== LINKS ===');
for (const l of info.links.slice(0, 25)) console.log('  ', l.href, '|', l.txt);
console.log('\n=== VUE STORE ===\n', info.vue);

await p.screenshot({ path: '/tmp/zy-shelf4.png' });
fs.writeFileSync('/tmp/zy-api.json', JSON.stringify(api, null, 2));
console.log('\nshot /tmp/zy-shelf4.png api=' + api.length);
await b.close();