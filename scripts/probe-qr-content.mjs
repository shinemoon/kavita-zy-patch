// 查清两件事:
//   1. 掌阅 PC 站把 code 编码成二维码时, 二维码内容是什么字符串
//   2. 前端用的哪个 qrcode 库
import { chromium } from '/home/youruser/.picoclaw/workspace/skills/paperecho-receiver/bin/node_modules/playwright/index.mjs';

const b = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
const ctx = await b.newContext({
  viewport: { width: 1100, height: 850 },
  userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
});
const p = await ctx.newPage();

// 抓所有 js chunk
const scripts = new Set();
p.on('response', (r) => {
  const u = r.url();
  if (/_nuxt\/.*\.js$/.test(u)) scripts.add(u);
});

await p.goto('https://pc.ireader.com/login', { waitUntil: 'networkidle', timeout: 45000 });
await p.waitForTimeout(3000);
const btn = await p.$('text=点击登录');
if (btn) await btn.click();
await p.waitForTimeout(4500);

// 找二维码节点, 连父链一起看 (Vue 会把 props 渲染成 attr 或 __vue__)
const dom = await p.evaluate(() => {
  const out = [];
  document.querySelectorAll('canvas, img[data-src*="data:image"], img[src^="data:image"]').forEach((el) => {
    let chain = [], n = el;
    for (let i = 0; i < 5 && n; i++) {
      chain.push(n.tagName + (n.className ? '.' + String(n.className).split(/\s+/).join('.') : ''));
      n = n.parentElement;
    }
    out.push({ tag: el.tagName, w: el.width, h: el.height, chain });
  });
  return out;
});
console.log('QR DOM:', JSON.stringify(dom, null, 1));

// Vue 组件 props —— 二维码组件往往把文本塞在 __vue__ 或 data 里
const props = await p.evaluate(() => {
  const res = [];
  for (const el of document.querySelectorAll('canvas, img[src^="data:image"]')) {
    let n = el;
    for (let i = 0; i < 8 && n; i++) {
      if (n.__vue__ || n.__vueParentComponent) {
        const c = n.__vueParentComponent || n.__vue__.$parent;
        res.push({ depth: i, name: c && c.type && (c.type.name || 'anon'), props: c && c.props ? JSON.stringify(c.props).slice(0, 300) : null });
      }
      n = n.parentElement;
    }
  }
  return res;
});
console.log('VUE PROPS:', JSON.stringify(props, null, 1));

console.log('SCRIPTS:');
for (const s of scripts) console.log('  ', s);
await b.close();