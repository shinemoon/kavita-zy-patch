// 解我们生成的 QR SVG, 验证 encode 的正确性。
// 优先用 BarcodeDetector (Chromium 有), 没有就 dump 像素矩阵肉眼/算法验。
import fs from 'fs';

const svg = fs.readFileSync(process.argv[2], 'utf8');
const { chromium } = await import(
  '/home/youruser/.picoclaw/workspace/skills/paperecho-receiver/bin/node_modules/playwright/index.mjs'
);
const br = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
const p = await br.newPage();

const html = `<!doctype html><body style="margin:0;background:#fff">
<img id="q" src="data:image/svg+xml;base64,${Buffer.from(svg, 'utf8').toString('base64')}"
     style="width:420px;image-rendering:pixelated"></body>`;
await p.setContent(html);

const out = await p.evaluate(async () => {
  const img = document.getElementById('q');
  await img.decode();
  if ('BarcodeDetector' in window) {
    try {
      const det = new BarcodeDetector({ formats: ['qr_code'] });
      const r = await det.detect(img);
      return { mode: 'barcodeDetector', value: r.length ? r[0].rawValue : null };
    } catch (e) {
      return { mode: 'barcodeDetector-err', err: String(e && e.message || e) };
    }
  }
  return { mode: 'no-api' };
});
console.log(JSON.stringify(out));

if (out.mode !== 'barcodeDetector') {
  const mat = await p.evaluate(() => {
    const img = document.getElementById('q');
    const c = document.createElement('canvas');
    c.width = 420; c.height = 420;
    const x = c.getContext('2d');
    x.drawImage(img, 0, 0, 420, 420);
    const d = x.getImageData(0, 0, 420, 420).data;
    const rows = [];
    for (let y = 0; y < 420; y += 12) {
      let s = '';
      for (let xx = 0; xx < 420; xx += 12) {
        const i = (y * 420 + xx) * 4;
        s += d[i] < 128 ? '#' : '.';
      }
      rows.push(s);
    }
    return rows;
  });
  console.log(mat.join('\n'));
}
await br.close();