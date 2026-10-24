import { chromium } from '/home/youruser/.picoclaw/workspace/skills/paperecho-receiver/bin/node_modules/playwright/index.mjs';

const b = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
const ctx = await b.newContext({
  userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
  viewport: { width: 1440, height: 900 },
});
const p = await ctx.newPage();

await p.goto('https://pc.ireader.com/login', { waitUntil: 'networkidle', timeout: 30000 });
await p.waitForTimeout(2000);

const btn = await p.$('button:has-text("登录")');
if (btn) { await btn.click(); await p.waitForTimeout(4000); }

// capture the qrcode element: canvas / img / svg
const info = await p.evaluate(() => {
  const out = [];
  document.querySelectorAll('canvas, img, svg').forEach((e) => {
    const r = e.getBoundingClientRect();
    if (r.width > 60 && r.height > 60 && r.width < 500) {
      out.push({
        tag: e.tagName,
        w: Math.round(r.width), h: Math.round(r.height),
        cls: (e.className && e.className.baseVal !== undefined ? e.className.baseVal : e.className || '').toString().slice(0, 80),
        src: (e.src || '').slice(0, 300),
        parentCls: (e.parentElement?.className || '').toString().slice(0, 80),
      });
    }
  });
  return out;
});
console.log('=== QR ELEMENTS ===');
console.log(JSON.stringify(info, null, 2));

// element screenshot of the whole login card
const card = await p.$('.login-box, .login-container, .login-modal, [class*="login"]');
if (card) {
  await card.screenshot({ path: '/tmp/zy-qr-card.png' });
  console.log('CARD SHOT: /tmp/zy-qr-card.png');
}

await p.screenshot({ path: '/tmp/zy-login-full.png', fullPage: false });
await b.close();