// 用 pc.ireader.com 真实使用的 qrcode 库 (qrcode-generator) 解我们编的 SVG,
// 验证往返: 我们 encode 的文本 == 库 decode 出来的文本.
// BarcodeDetector 在 headless shell 里没有, 所以用库自带的 decode? 没有。
// 改用另一条路: 让 qrcode-generator 编同一个文本, 比对两��矩阵是否一致。
import fs from 'fs';

const WANT = process.argv[2] || 'web_login:demo123456';

const { chromium } = await import('/home/youruser/.picoclaw/workspace/skills/paperecho-receiver/bin/node_modules/playwright/index.mjs');
const br = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
const p = await br.newPage();

// 加载 pc 站同款 qrcode-generator (85f7eb5 chunk)
await p.goto('https://pc.ireader.com/login', { waitUntil: 'domcontentloaded', timeout: 45000 });
await p.waitForTimeout(2500);

const ref = await p.evaluate(async (want) => {
  // chunk 里的库挂在 webpack 上, 直接找 toDataURL 的模块
  const canvas = document.createElement('canvas');
  document.body.appendChild(canvas);
  // pc 站登录按钮会生成二维码, 抓它的 canvas 数据
  const btn = document.querySelector('text=点击登录');
  return { hasBtn: !!btn, canvasOk: !!canvas.getContext };
}, WANT);
console.log('site:', JSON.stringify(ref));

await p.goto('https://pc.ireader.com/login', { waitUntil: 'networkidle', timeout: 45000 });
await p.waitForTimeout(3000);
const b2 = await p.$('text=点击登录');
if (b2) await b2.click();
await p.waitForTimeout(5000);

const siteQR = await p.evaluate(() => {
  const imgs = [...document.querySelectorAll('.qr-code-img img')];
  if (!imgs.length) return null;
  return imgs[0].src;
});
console.log('site qr data-url len:', siteQR ? siteQR.length : 'none');
if (siteQR) fs.writeFileSync('/tmp/site-qr.txt', siteQR);
await br.close();