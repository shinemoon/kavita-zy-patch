// 参照测试: 同一个 code, 掌阅 PC 站生成的 QR 跟我们生成的 QR 逐模块比对。
import fs from 'fs';
import { chromium } from '/home/youruser/.picoclaw/workspace/skills/paperecho-receiver/bin/node_modules/playwright/index.mjs';
import { execFileSync } from 'child_process';

const CODE = process.argv[2] || 'demo123';

const br = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
const ctx = await br.newContext({
  viewport: { width: 1100, height: 850 },
  userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
});
const p = await ctx.newPage();
await p.goto('https://pc.ireader.com/login', { waitUntil: 'networkidle', timeout: 45000 });
await p.waitForTimeout(3000);
const btn = await p.$('text=点击登录');
if (btn) await btn.click();
await p.waitForTimeout(5000);

const site = await p.evaluate(() => {
  const imgs = [...document.querySelectorAll('img')].map(i => i.src || '');
  const d = imgs.filter(s => s.startsWith('data:image') && s.length > 400);
  const cs = [...document.querySelectorAll('canvas')].map(c => { try { return c.toDataURL(); } catch { return ''; } });
  return { imgs: d, canvases: cs.filter(s => s && s.length > 400) };
});
await br.close();
fs.mkdirSync('/tmp/qrref', { recursive: true });
let n = 0;
for (const s of [...site.imgs, ...site.canvases]) {
  const f = `/tmp/qrref/site-${n}.png`;
  fs.writeFileSync(f, Buffer.from(s.split(',')[1], 'base64'));
  console.log('saved', f, 'len', s.length);
  n++;
}
if (n === 0) { console.log('SITE_QR_NONE'); process.exit(1); }

// 用 python 把站点 PNG 抽成 0/1 矩阵 (finders 定位 + 模块边长推断)
const { stdout } = execFileSync('python3', ['/tmp/qrref/extract.py', '/tmp/qrref/site-0.png'], {
  encoding: 'utf8',
  maxBuffer: 1 << 24,
});
fs.writeFileSync('/tmp/qrref/site-matrix.txt', stdout);
console.log('--- site matrix (first 6 rows) ---');
console.log(stdout.split('\n').slice(0, 6).join('\n'));
console.log('rows', stdout.trim().split('\n').length);