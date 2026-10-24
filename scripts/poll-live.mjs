import { chromium } from '/home/youruser/.picoclaw/workspace/skills/paperecho-receiver/bin/node_modules/playwright/index.mjs';
import fs from 'fs';

const WAIT = Number(process.argv[2] || 300); // seconds
const b = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
const ctx = await b.newContext({
  userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
  viewport: { width: 1440, height: 900 },
});
const p = await ctx.newPage();

// log EVERY xhr/fetch so we learn the real endpoints
p.on('request', (r) => {
  const u = r.url();
  if (/zhangyue|ireader/.test(u) && /open|user|login|qrcode|token/.test(u)) {
    console.log('REQ', r.method(), u.slice(0, 160));
  }
});

await p.goto('https://pc.ireader.com/login', { waitUntil: 'networkidle', timeout: 30000 });
await p.waitForTimeout(1200);
const btn = await p.$('button:has-text("登录")');
if (btn) { await btn.click(); await p.waitForTimeout(4000); }

const el = await p.$('.qr-code-img img, .qr-code-img canvas, img.qr-code-img, canvas.qr-code-img');
if (el) {
  const src = await el.evaluate((e) => e.tagName === 'CANVAS' ? e.toDataURL() : (e.src || e.getAttribute('src') || ''));
  if (String(src).startsWith('data:image')) {
    fs.writeFileSync('/tmp/zy-qr2.png', Buffer.from(String(src).split(',')[1], 'base64'));
    console.log('QR_SAVED /tmp/zy-qr2.png');
  }
} else {
  await p.screenshot({ path: '/tmp/zy-qr2.png' });
  console.log('QR_SAVED(shot) /tmp/zy-qr2.png');
}
console.log('NOW_POLLING', WAIT);

// poll inside the page's own session
const deadline = Date.now() + WAIT * 1000;
let last = '';
while (Date.now() < deadline) {
  let r = null;
  try {
    r = await p.evaluate(async () => {
      const m = document.querySelector('.__QR_URL__');
      return null;
    });
  } catch {}
  // generic: look for zysid cookie or logged-in DOM
  const cookies = await ctx.cookies();
  const zy = cookies.find((c) => /zysid|session|token/i.test(c.name));
  const dom = await p.evaluate(() => {
    const t = document.body.innerText || '';
    const m = t.match(/(昵称|用户名|欢迎|退出登录|我的账户)/);
    return { hit: m ? m[1] : null, len: t.length };
  }).catch(() => ({ hit: null, len: 0 }));
  const line = JSON.stringify({ cookie: zy ? zy.name + '=' + zy.value.slice(0, 40) : null, dom });
  if (line !== last) { console.log('STATE', line); last = line; }
  if (zy) {
    console.log('LOGIN_OK cookie=' + zy.name + '=' + zy.value);
    fs.writeFileSync('/tmp/zy-cookies.json', JSON.stringify(cookies, null, 2));
    await b.close();
    process.exit(0);
  }
  await p.waitForTimeout(2500);
}
console.log('TIMEOUT');
await b.close();