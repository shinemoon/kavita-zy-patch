// 校验 Kavita 里 toast 的实际样式 (背景半透明黑 + 淡入淡出) 与补丁版本
import { chromium } from 'playwright';

const URL = process.argv[2] || 'http://127.0.0.1:5000/';
const browser = await chromium.launch({ args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
const errs = [];
page.on('pageerror', e => errs.push('PAGEERR ' + e.message));
await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 20000 });
await page.waitForTimeout(3500);

const out = await page.evaluate(() => {
  const n = document.createElement('div');
  n.id = 'zy-toast';
  n.textContent = '已同步掌阅进度 41 → 49/100';
  document.body.appendChild(n);
  const off = getComputedStyle(n);
  const before = { opacity: off.opacity, transform: off.transform, bg: off.backgroundColor,
                   transition: off.transitionProperty + ' ' + off.transitionDuration };
  n.classList.add('on');
  const on = getComputedStyle(n);
  const after = { opacity: on.opacity, transform: on.transform, bg: on.backgroundColor };
  return {
    ver: (window.__ZY__ && window.__ZY__.ver) || null,
    readerCss: (window.__ZY__ && window.__ZY__.readerCss) ? window.__ZY__.readerCss() : null,
    styleTag: !!document.getElementById('zy-reader-css'),
    fileStyleTag: !!document.getElementById('zy-reader-css-file'),
    lineHeight: (() => { const d = document.querySelector('.book-content p') || document.querySelector('.book-content');
      return d ? getComputedStyle(d).lineHeight : null; })(),
    before, after,
  };
});
await page.waitForTimeout(200);
console.log(JSON.stringify({ out, errs: errs.slice(0, 3) }, null, 1));
await browser.close();
