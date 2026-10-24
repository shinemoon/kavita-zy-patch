// 报掌阅 PC 站二维码 <img> 的真实属性 (dataURI 长度 / 固有尺寸 / CSS 尺寸)
import { chromium } from '/home/youruser/.picoclaw/workspace/skills/paperecho-receiver/bin/node_modules/playwright/index.mjs';

const br = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
const ctx = await br.newContext({
  viewport: { width: 1100, height: 850 },
  userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
});
const p = await ctx.newPage();
await p.goto('https://pc.ireader.com/login', { waitUntil: 'networkidle', timeout: 45000 });
await p.waitForTimeout(3000);
const b = await p.$('text=点击登录');
if (b) await b.click();
await p.waitForTimeout(6000);

const info = await p.evaluate(() => {
  const h = document.querySelector('.qr-code-img');
  const im = h && h.querySelector('img');
  if (!im) return { err: 'no img' };
  const cs = getComputedStyle(im);
  return {
    srcHead: im.src.slice(0, 60),
    srcLen: im.src.length,
    natW: im.naturalWidth,
    natH: im.naturalHeight,
    cssW: cs.width,
    cssH: cs.height,
    cls: im.className,
    parentHTML: h.outerHTML.slice(0, 300)
  };
});
console.log(JSON.stringify(info, null, 1));
await br.close();