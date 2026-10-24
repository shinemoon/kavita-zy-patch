import { chromium } from '/home/youruser/.picoclaw/workspace/skills/paperecho-receiver/bin/node_modules/playwright/index.mjs';
import fs from 'fs';

const b = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
const ctx = await b.newContext({
  viewport: { width: 1100, height: 850 },
  userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
});
const p = await ctx.newPage();

p.on('response', async (r) => {
  const u = r.url();
  if (!/zhangyue\.com|ireader\.com/.test(u)) return;
  if (/\.(png|jpg|jpeg|gif|css|js|woff2?|svg|ico)/.test(u)) return;
  let t = '';
  try { t = (await r.text()).slice(0, 500); } catch {}
  if (/qrcode|login|user|bookshelf/i.test(u)) {
    console.log('RESP', r.status(), u.slice(0, 150));
    if (t) console.log('    >>', t.replace(/\s+/g, ' ').slice(0, 400));
  }
});

await p.goto('https://pc.ireader.com/login', { waitUntil: 'networkidle', timeout: 45000 });
await p.waitForTimeout(3000);
const btn = await p.$('text=点击登录');
if (btn) await btn.click();
await p.waitForTimeout(4500);

// save the fresh QR
const qr = await p.evaluate(() => {
  const out = [];
  document.querySelectorAll('img').forEach((i) => { if ((i.src || '').startsWith('data:image') && (i.src || '').length > 500) out.push(i.src); });
  [...document.querySelectorAll('canvas')].forEach((c) => out.push(c.toDataURL()));
  return out;
});
if (qr[0]) {
  fs.writeFileSync('/tmp/zy-qr-new.png', Buffer.from(qr[0].split(',')[1], 'base64'));
  console.log('QR_SAVED /tmp/zy-qr-new.png');
} else {
  console.log('NO_QR');
}

// poll until logged in
console.log('POLLING...');
let ok = false;
for (let i = 0; i < 100; i++) {
  await p.waitForTimeout(2000);
  const path = await p.evaluate(() => location.pathname);
  const cookies = await ctx.cookies();
  const named = cookies.map((c) => c.name);
  if (path !== '/login') { console.log('LOGGED_IN path=' + path); ok = true; break; }
  if (cookies.length && i % 3 === 0) console.log('  t=' + i * 2 + 's cookies=' + JSON.stringify(named));
  if (i === 99) console.log('TIMEOUT');
}

const fin = await ctx.cookies();
fs.writeFileSync('/tmp/zy-cookies-real.json', JSON.stringify(fin, null, 2));
console.log('RESULT ok=' + ok + ' cookies=' + fin.length);
console.log(JSON.stringify(fin.map((c) => ({ n: c.name, d: c.domain, v: c.value.slice(0, 100) })), null, 2));
await b.close();