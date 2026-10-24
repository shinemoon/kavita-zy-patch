// 用 jsQR 解我们从掌阅 PC 站抓下来的矩阵, 看它到底装了什么。
// 用法: node qrdec-site.mjs <matrix-txt>
import fs from 'fs';
import { chromium } from '/home/youruser/.picoclaw/workspace/skills/paperecho-receiver/bin/node_modules/playwright/index.mjs';

const rows = fs.readFileSync(process.argv[2], 'utf8').split('\n').filter(Boolean);
const n = rows.length;
const border = 4;
const scale = 6;

// 画成 HTML table -> canvas, 再用 jsQR 解
let html = `<!doctype html><body style="margin:0;background:#fff"><canvas id=c width=${(n + border * 2) * scale} height=${(n + border * 2) * scale}></canvas>`;
html += `<script>
const rows=${JSON.stringify(rows)}, n=${n}, border=${border}, scale=${scale};
const c=document.getElementById('c'), x=c.getContext('2d',{willReadFrequently:true});
x.fillStyle='#fff'; x.fillRect(0,0,c.width,c.height);
x.fillStyle='#000';
for(let r=0;r<n;r++)for(let col=0;col<n;col++){ if(rows[r][col]==='1') x.fillRect((col+border)*scale,(r+border)*scale,scale,scale); }
</script></body>`;

const jsqrSrc = fs.readFileSync(
  '/vol2/1001/zhangyue-bridge/tools/node_modules/jsqr/dist/jsQR.js',
  'utf8'
);

const br = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
const p = await br.newPage();
await p.setContent(html);
const out = await p.evaluate(
  async ([src]) => {
    const jsQR = (0, eval)(src + ';jsQR');
    const c = document.getElementById('c');
    const d = c.getContext('2d').getImageData(0, 0, c.width, c.height);
    const r = jsQR(d.data, d.width, d.height);
    return r ? { data: r.data, version: r.version } : null;
  },
  [jsqrSrc]
);
console.log(JSON.stringify(out, null, 1));
await br.close();