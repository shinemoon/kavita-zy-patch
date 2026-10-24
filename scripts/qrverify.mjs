// 用 jsQR 解我们生成的 QR SVG -> 验证 encode 正确 (往返测试)。
// 用法: node qrverify.mjs <svg-file> [期望原文]
import fs from 'fs';

const { chromium } = await import(
  '/home/youruser/.picoclaw/workspace/skills/paperecho-receiver/bin/node_modules/playwright/index.mjs'
);

const file = process.argv[2];
const want = process.argv[3];
const svg = fs.readFileSync(file, 'utf8');

const br = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
const p = await br.newPage();
await p.setContent(
  `<!doctype html><body style="margin:0;background:#fff">` +
    `<img id="q" src="data:image/svg+xml;base64,${Buffer.from(svg, 'utf8').toString('base64')}"` +
    ` style="width:420px;height:420px;image-rendering:pixelated"></body>`
);
const dataUrl = await p.evaluate(async () => {
  const img = document.getElementById('q');
  await img.decode();
  const c = document.createElement('canvas');
  c.width = 420; c.height = 420;
  const x = c.getContext('2d');
  x.fillStyle = '#fff';
  x.fillRect(0, 0, 420, 420);
  x.drawImage(img, 0, 0, 420, 420);
  return c.toDataURL('image/png');
});
await br.close();

const buf = Buffer.from(dataUrl.split(',')[1], 'base64');
// 手搓 PNG 解码太麻烦 —— 借 sharp? 没有。改走 <canvas> 里直接 jsQR (jsQR 要 Uint8ClampedArray)。
// 所以在页面内跑 jsQR, 页面里注入 jsQR 源码。
const jsqrSrc = fs.readFileSync(
  '/vol2/1001/zhangyue-bridge/tools/node_modules/jsqr/dist/jsQR.js',
  'utf8'
);
const br2 = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
const p2 = await br2.newPage();
await p2.setContent(
  `<!doctype html><body><img id="q" src="data:image/svg+xml;base64,${Buffer.from(svg, 'utf8').toString('base64')}"` +
    ` style="width:420px;height:420px;image-rendering:pixelated">`
);
const got = await p2.evaluate(
  async ([src]) => {
    const jsQR = (0, eval)(src + ';jsQR');
    const img = document.getElementById('q');
    await img.decode();
    const N = 600;
    const c = document.createElement('canvas');
    c.width = N; c.height = N;
    const x = c.getContext('2d', { willReadFrequently: true });
    x.fillStyle = '#fff'; x.fillRect(0, 0, N, N);
    x.drawImage(img, 0, 0, N, N);
    const d = x.getImageData(0, 0, N, N);
    const r = jsQR(d.data, d.width, d.height);
    return r ? r.data : null;
  },
  [jsqrSrc]
);
await br2.close();

if (want) {
  console.log(got === want ? `PASS  ${want}` : `FAIL\n  want: ${want}\n  got : ${got}`);
  process.exit(got === want ? 0 : 1);
}
console.log('decoded:', JSON.stringify(got));