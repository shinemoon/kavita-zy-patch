// 抓掌阅 PC 站的登录二维码, 抽成模块矩阵 (0/1 文本)。
// 这是终极参照: iReader App 能扫的 QR 就是这个库编的。
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
  const u = r.url();
  if (/qrcode\/get/.test(u)) {
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
await p.waitForTimeout(5000);

console.log('qrcode/get codes:', JSON.stringify(codes));

// 二维码可能画在 canvas 或 img(dataURI)。逐个试, 用模块中心采样。
const res = await p.evaluate(() => {
  const out = [];
  const cands = [];
  document.querySelectorAll('canvas').forEach((c) => {
    if (c.width > 40 && c.height > 40) cands.push({ kind: 'canvas', el: c });
  });
  document.querySelectorAll('img').forEach((im) => {
    const s = im.src || '';
    if (s.startsWith('data:image') && s.length > 300) cands.push({ kind: 'img', el: im, src: s });
  });

  for (const cd of cands) {
    let cvs = null;
    if (cd.kind === 'canvas') {
      cvs = document.createElement('canvas');
      cvs.width = cd.el.width; cvs.height = cd.el.height;
      cvs.getContext('2d').drawImage(cd.el, 0, 0);
    } else {
      const im = new Image();
      // 同步不行, 交给外层; 这里只用已加载的
      if (!im.complete) continue;
      cvs = document.createElement('canvas');
      cvs.width = im.naturalWidth; cvs.height = im.naturalHeight;
      cvs.getContext('2d').drawImage(im, 0, 0);
    }
    const w = cvs.width, h = cvs.height;
    if (!w || w !== h) continue;
    const x = cvs.getContext('2d');
    const d = x.getImageData(0, 0, w, h).data;
    const isDark = (px, py) => {
      const i = (py * w + px) * 4;
      return (d[i] + d[i + 1] + d[i + 2]) / 3 < 128;
    };
    // 二维码尺寸必为 4k+17。找 quiet zone 内模块边长。
    // 策略: 假设 size, border=4, 试各 size 看左上 finder 7x7 是否匹配。
    for (let size = 21; size <= 57; size += 4) {
      const total = size + 8;
      const scale = w / total;
      if (scale <= 0) continue;
      // 采样中心点
      let ok = true;
      const rows = [];
      for (let r = 0; r < size && ok; r++) {
        let line = '';
        for (let c = 0; c < size; c++) {
          const cx = Math.floor((c + 4 + 0.5) * scale);
          const cy = Math.floor((r + 4 + 0.5) * scale);
          if (cx >= w || cy >= h) { ok = false; break; }
          line += isDark(cx, cy) ? '1' : '0';
        }
        rows.push(line);
      }
      if (!ok) continue;
      // 校验 finder: 左上 7x7 是 1111111/1000001/1011101/...
      const F = ['1111111', '1000001', '1011101', '1011101', '1011101', '1000001', '1111111'];
      let good = true;
      for (let r = 0; r < 7 && good; r++) {
        if (rows[r].slice(0, 7) !== F[r]) good = false;
      }
      if (good) {
        out.push({ kind: cd.kind, size, rows });
        break;
      }
    }
  }
  return out;
});

console.log('decoded matrices:', res.length);
if (!res.length) {
  await br.close();
  process.exit(1);
}
const best = res[0];
fs.writeFileSync(outFile, best.rows.join('\n'), 'utf8');
console.log('size =', best.size, '-> saved', outFile);
console.log(best.rows.join('\n'));
await br.close();