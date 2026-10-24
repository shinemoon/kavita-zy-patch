import { chromium } from '/home/youruser/.picoclaw/workspace/skills/paperecho-receiver/bin/node_modules/playwright/index.mjs';
import fs from 'fs';

const USR = '__ZY_USR__';
const ZYSID = '__ZY_ZYSID__';

const b = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
const ctx = await b.newContext({
  viewport: { width: 1100, height: 850 },
  userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
});
// pre-seed the identity cookies so the QR binds to this account
await ctx.addCookies([
  { name: 'zysid', value: ZYSID, domain: '.zhangyue.com', path: '/' },
  { name: 'zysid', value: ZYSID, domain: '.ireader.com', path: '/' },
  { name: 'usr', value: USR, domain: '.zhangyue.com', path: '/' },
  { name: 'usr', value: USR, domain: '.ireader.com', path: '/' },
]);

const p = await ctx.newPage();

p.on('response', async (r) => {
  const u = r.url();
  if (!/qrcode|user\/info|bookshelf\/list/.test(u)) return;
  let t = '';
  try { t = (await r.text()).slice(0, 400); } catch {}
  console.log('RESP', r.status(), u.replace(/^https:\/\/api-pc\.zhangyue\.com/, '').slice(0, 110));
  if (t) console.log('    >>', t.replace(/\s+/g, ' ').slice(0, 350));
});

await p.goto('https://pc.ireader.com/login', { waitUntil: 'networkidle', timeout: 45000 });
await p.waitForTimeout(3000);
const btn = await p.$('text=点击登录');
if (btn) await btn.click();
await p.waitForTimeout(4500);

const qr = await p.evaluate(() => {
  const out = [];
  document.querySelectorAll('img').forEach((i) => { const s = i.src || ''; if (s.startsWith('data:image') && s.length > 500) out.push(s); });
  [...document.querySelectorAll('canvas')].forEach((c) => out.push(c.toDataURL()));
  return out;
});
if (qr[0]) {
  fs.writeFileSync('/tmp/zy-qr-new.png', Buffer.from(qr[0].split(',')[1], 'base64'));
  console.log('QR_SAVED /tmp/zy-qr-new.png');
} else console.log('NO_QR');

console.log('POLLING...');
let ok = false;
for (let i = 0; i < 110; i++) {
  await p.waitForTimeout(2000);
  const path = await p.evaluate(() => location.pathname);
  if (path !== '/login') { console.log('LOGGED_IN path=' + path); ok = true; break; }
  if (i === 109) console.log('TIMEOUT');
}

const fin = await ctx.cookies();
fs.writeFileSync('/tmp/zy-cookies-real.json', JSON.stringify(fin, null, 2));
console.log('RESULT ok=' + ok + ' cookies=' + fin.length);
console.log(JSON.stringify(fin.map((c) => ({ n: c.name, d: c.domain, v: c.value.slice(0, 140) })), null, 2));

if (ok) {
  await p.waitForTimeout(3000);
  const body = await p.evaluate(() => document.body.innerText.slice(0, 600));
  console.log('SHELF TEXT:', body);
  await p.screenshot({ path: '/tmp/zy-shelf-ok.png' });
}
await b.close();