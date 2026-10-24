import { chromium } from '/home/youruser/.picoclaw/workspace/skills/paperecho-receiver/bin/node_modules/playwright/index.mjs';
import fs from 'fs';

const b = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
const ctx = await b.newContext({
  viewport: { width: 1100, height: 850 },
  userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
});
const p = await ctx.newPage();
await p.goto('https://pc.ireader.com/login', { waitUntil: 'networkidle', timeout: 45000 });
await p.waitForTimeout(3500);

console.log('CLICKING 登录按钮 ...');
const btn = await p.$('text=点击登录');
if (btn) await btn.click();
await p.waitForTimeout(5000);

const qr = await p.evaluate(() => {
  const out = [];
  document.querySelectorAll('img').forEach((i) => { if ((i.src || '').length > 200) out.push(i.src); });
  const cvs = [...document.querySelectorAll('canvas')].map((c) => c.toDataURL());
  return { imgs: out, canvas: cvs, text: document.body.innerText.slice(0, 300) };
});
console.log('IMGS:', qr.imgs.length, 'CANVAS:', qr.canvas.length);
const all = qr.imgs.concat(qr.canvas);
all.forEach((s, i) => {
  if (s.length > 500 && s.startsWith('data:image')) {
    fs.writeFileSync('/tmp/zy-qr-' + i + '.png', Buffer.from(s.split(',')[1], 'base64'));
    console.log('saved /tmp/zy-qr-' + i + '.png  len=' + s.length);
  }
});
console.log('TEXT:', qr.text);
await p.screenshot({ path: '/tmp/zy-login2.png' });
console.log('shot /tmp/zy-login2.png');
await b.close();