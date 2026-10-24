// 抓掌阅二维码会话的真实请求 URL + 参数名, 以及二维码图片本身。
import { chromium } from '/home/youruser/.picoclaw/workspace/skills/paperecho-receiver/bin/node_modules/playwright/index.mjs';
import fs from 'fs';

const b = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
const ctx = await b.newContext({
  viewport: { width: 1100, height: 850 },
  userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
});
const p = await ctx.newPage();

const seen = [];
p.on('request', (r) => {
  const u = r.url();
  if (/zhangyue\.com/.test(u) && /qrcode|qr|login|user/i.test(u)) {
    seen.push(u);
    console.log('REQ ', u.replace(/^https:\/\/api-pc\.zhangyue\.com/, ''));
  }
});
p.on('response', async (r) => {
  const u = r.url();
  if (!/qrcode|qr|login/i.test(u)) return;
  let t = '';
  try { t = (await r.text()).slice(0, 300); } catch {}
  console.log('RESP', r.status(), u.replace(/^https:\/\/api-pc\.zhangyue\.com/, '').slice(0, 120));
  if (t) console.log('    >>', t.replace(/\s+/g, ' ').slice(0, 280));
});

await p.goto('https://pc.ireader.com/login', { waitUntil: 'networkidle', timeout: 45000 });
await p.waitForTimeout(3500);

const btn = await p.$('text=点击登录');
if (btn) { console.log('--- click 登录 ---'); await btn.click(); }
await p.waitForTimeout(5000);

const imgs = await p.evaluate(() => {
  const out = [];
  document.querySelectorAll('img').forEach((i) => {
    const s = i.src || '';
    if (s.startsWith('data:image') && s.length > 400) out.push(s);
  });
  [...document.querySelectorAll('canvas')].forEach((c) => { try { out.push(c.toDataURL()); } catch {} });
  return out;
});
console.log('--- imgs/canvas:', imgs.length, '---');
imgs.forEach((s, i) => {
  if (s.startsWith('data:image')) {
    const f = `/tmp/zyq-${i}.png`;
    fs.writeFileSync(f, Buffer.from(s.split(',')[1], 'base64'));
    console.log('SAVED', f, 'len', s.length);
  }
});
await p.screenshot({ path: '/tmp/zyq-page.png' });
fs.writeFileSync('/tmp/zyq-urls.txt', seen.join('\n'));
console.log('SAVED /tmp/zyq-urls.txt  (' + seen.length + ' urls)');
await b.close();