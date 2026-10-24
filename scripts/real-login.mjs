import { chromium } from '/home/youruser/.picoclaw/workspace/skills/paperecho-receiver/bin/node_modules/playwright/index.mjs';
import fs from 'fs';

const b = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
const ctx = await b.newContext({
  userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
  viewport: { width: 1200, height: 900 },
});
const p = await ctx.newPage();

// show what the real login API returns
p.on('response', async (r) => {
  const u = r.url();
  if (!/zhangyue\.com|ireader\.com/.test(u)) return;
  if (/\.(png|jpg|jpeg|gif|css|js|woff2?|svg|ico)/.test(u)) return;
  let t = '';
  try { t = (await r.text()).slice(0, 600); } catch {}
  console.log('RESP', r.status(), u.slice(0, 160));
  if (t) console.log('    >>', t.replace(/\s+/g, ' ').slice(0, 500));
});

await p.goto('https://pc.ireader.com/login', { waitUntil: 'networkidle', timeout: 45000 });
await p.waitForTimeout(4000);

// pull the QR out of the page
const qr = await p.evaluate(() => {
  const imgs = [...document.querySelectorAll('img, canvas')].map((e) => e.src || e.toDataURL());
  return { imgs: imgs.filter(Boolean), text: document.body.innerText.slice(0, 400) };
});
console.log('\nQR IMGS:', qr.imgs.length);
for (const i of qr.imgs) console.log('  ', i.slice(0, 160));
console.log('\nTEXT:', qr.text);

// dump the QR src so it can be shown / decoded
fs.writeFileSync('/tmp/zy-qr-list.txt', qr.imgs.join('\n'));

// poll for a couple minutes so the user's phone scan shows up
console.log('\n=== POLLING for scan (up to 150s) ===');
for (let i = 0; i < 50; i++) {
  await p.waitForTimeout(3000);
  const cookies = await ctx.cookies();
  const names = cookies.map((c) => c.name);
  const cur = await p.evaluate(() => location.pathname);
  if (cur !== '/login') {
    console.log('!!! NAVIGATED TO', cur);
    break;
  }
  if (i % 5 === 0) {
    console.log('  t=' + (i * 3) + 's path=' + cur + ' cookies=' + JSON.stringify(names));
  }
}

const fin = await ctx.cookies();
console.log('\n=== FINAL COOKIES ===');
console.log(JSON.stringify(fin.map((c) => ({ n: c.name, d: c.domain, v: c.value.slice(0, 120) })), null, 2));
fs.writeFileSync('/tmp/zy-cookies-real.json', JSON.stringify(fin, null, 2));
await p.screenshot({ path: '/tmp/zy-login.png' });
await b.close();