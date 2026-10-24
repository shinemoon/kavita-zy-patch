// 抓掌阅 PC 站真实登录二维码, 抽成 0/1 模块矩阵。
// 定位: .img-container.qr-code-img 里的 <img> (src 是 dataURI, 120x120)。
//
// 用法: node qrsite-matrix.mjs [输出文件]
import fs from 'fs';
import { chromium } from '/home/youruser/.picoclaw/workspace/skills/paperecho-receiver/bin/node_modules/playwright/index.mjs';

const outFile = process.argv[2] || '/tmp/site-matrix.txt';

const br = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
const ctx = await br.newContext({
  viewport: { width: 1100, height: 850 },
  userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
});
const p = await ctx.newPage();

const codes = [];
p.on('response', async (r) => {
  if (/qrcode\/get/.test(r.url())) {
    try {
      const j = await r.json();
      if (j && j.body) codes.push(String(j.body).replace(/"/g, ''));
    } catch {}
  }
});

await p.goto('https://pc.ireader.com/login', { waitUntil: 'networkidle', timeout: 45000 });
await p.waitForTimeout(3000);
const btn = await p.$('text=点击登录');
if (btn) await btn.click();
await p.waitForTimeout(6000);

const res = await p.evaluate(() => {
  const holder = document.querySelector('.qr-code-img') ||
                 document.querySelector('[class*="qr-code"]');
  if (!holder) return { err: 'no .qr-code-img holder' };
  const img = holder.querySelector('img');
  if (!img) return { err: 'no img inside holder' };

  const c = document.createElement('canvas');
  c.width = img.naturalWidth || 120;
  c.height = img.naturalHeight || 120;
  const x = c.getContext('2d', { willReadFrequently: true });
  x.fillStyle = '#fff';
  x.fillRect(0, 0, c.width, c.height);
  x.drawImage(img, 0, 0, c.width, c.height);
  const w = c.width, h = c.height;
  const d = x.getImageData(0, 0, w, h).data;
  const dark = (px, py) => {
    const i = (py * w + px) * 4;
    return (d[i] + d[i + 1] + d[i + 2]) / 3 < 140;
  };

  const F = ['1111111', '1000001', '1011101', '1011101', '1011101', '1000001', '1111111'];
  for (let size = 21; size <= 57; size += 4) {
    const total = size + 8;                  // 4 模块 quiet zone
    if (Math.abs(w / total - h / total) > 0.5) continue;
    const scale = w / total;
    const rows = [];
    for (let r = 0; r < size; r++) {
      let line = '';
      for (let col = 0; col < size; col++) {
        const cx = Math.min(w - 1, Math.floor((col + 4 + 0.5) * scale));
        const cy = Math.min(h - 1, Math.floor((r + 4 + 0.5) * scale));
        line += dark(cx, cy) ? '1' : '0';
      }
      rows.push(line);
    }
    let good = true;
    for (let r = 0; r < 7 && good; r++) if (rows[r].slice(0, 7) !== F[r]) good = false;
    if (good) return { size, w, h, scale, rows, srcLen: (img.src || '').length };
  }
  return { err: 'no finder match', w, h };
});

if (res.err) {
  console.error('ERR:', res.err, res.w, res.h);
  await br.close();
  process.exit(1);
}
console.log('codes:', JSON.stringify(codes));
console.log('image', res.w + 'x' + res.h, 'scale', res.scale.toFixed(3), 'size', res.size);
fs.writeFileSync(outFile, res.rows.join('\n'), 'utf8');
console.log('saved', outFile);
await br.close();