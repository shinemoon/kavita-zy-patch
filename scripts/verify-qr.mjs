// 用浏览器 BarcodeDetector 解 QR, 验证我们编的矩阵真能被读出原文.
import fs from 'fs';

const svg = fs.readFileSync(process.argv[2], 'utf8');
const b64 = Buffer.from(svg, 'utf8').toString('base64');
const html = `<!doctype html><body style="margin:0;background:#fff">
<img id="q" src="data:image/svg+xml;base64,${b64}" style="image-rendering:pixelated;width:600px">
<script>
(async () => {
  const img = document.getElementById('q');
  await img.decode();
  if (!('BarcodeDetector' in window)) { document.title = 'NO_API'; return; }
  const det = new BarcodeDetector({ formats: ['qr_code'] });
  try {
    const r = await det.detect(img);
    document.title = 'OK:' + (r.length ? r[0].rawValue : 'EMPTY');
  } catch (e) { document.title = 'ERR:' + e.message; }
})();
</script></body>`;

const { chromium } = await import('/home/youruser/.picoclaw/workspace/skills/paperecho-receiver/bin/node_modules/playwright/index.mjs');
const br = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
const p = await br.newPage();
await p.goto('data:text/html,' + encodeURIComponent(html));
await p.waitForTimeout(3500);
console.log('TITLE:', await p.title());
await br.close();