import { chromium } from '/home/youruser/.picoclaw/workspace/skills/paperecho-receiver/bin/node_modules/playwright/index.mjs';
import fs from 'fs';

const b = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
const ctx = await b.newContext({
  userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
  viewport: { width: 1440, height: 900 },
});
const p = await ctx.newPage();

// capture the raw qrcode/get response so we know the code
let code = null;
p.on('response', async (r) => {
  if (r.url().includes('/user/qrcode/get')) {
    try { const j = await r.json(); code = j.body; } catch {}
  }
});

await p.goto('https://pc.ireader.com/login', { waitUntil: 'networkidle', timeout: 30000 });
await p.waitForTimeout(1500);
const btn = await p.$('button:has-text("登录")');
if (btn) { await btn.click(); await p.waitForTimeout(5000); }

const el = await p.$('.qr-code-img img, .qr-code-img canvas, img.qr-code-img, canvas.qr-code-img');
console.log('CODE:', code);

if (el) {
  const src = await el.evaluate((e) => e.tagName === 'CANVAS' ? e.toDataURL() : (e.src || e.getAttribute('src')));
  console.log('SRC_PREFIX:', String(src).slice(0, 60));
  console.log('SRC_LEN:', String(src).length);

  if (String(src).startsWith('data:image')) {
    const b64 = String(src).split(',')[1];
    fs.writeFileSync('/tmp/zy-qr.png', Buffer.from(b64, 'base64'));
    console.log('SAVED: /tmp/zy-qr.png');
  } else {
    // canvas or plain url -> screenshot the element at high dpi
    await el.screenshot({ path: '/tmp/zy-qr.png', scale: 'device' });
    console.log('SAVED(shot): /tmp/zy-qr.png');
  }
} else {
  console.log('NO QR ELEMENT');
  console.log(await p.evaluate(() => document.querySelector('.qr-code')?.outerHTML?.slice(0, 800)));
}

fs.writeFileSync('/tmp/zy-code.txt', String(code || ''));
console.log('CODE_SAVED: /tmp/zy-code.txt');
await b.close();