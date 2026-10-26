// 验证 5012 面板里的「阅读主题」下拉: 三个选项 / 切换生效 / 不影响行间距。
// 用法: node src/probe-theme-ui.mjs
import { chromium } from 'playwright';

const b = await chromium.launch({ args: ['--no-sandbox'] });
const p = await b.newPage({ viewport: { width: 1400, height: 950 } });
const errs = [];
p.on('pageerror', e => errs.push(String(e)));
await p.goto('http://127.0.0.1:5012/', { waitUntil: 'networkidle' });
await p.waitForTimeout(1500);

await p.click('#sec-rs > summary');
await p.waitForTimeout(400);

const opts = await p.evaluate(() => {
  const s = document.getElementById('rs-theme');
  return { exists: !!s, value: s && s.value,
           options: s ? [...s.options].map(o => [o.value, o.textContent]) : [],
           desc: (document.getElementById('rs-theme-desc') || {}).textContent,
           lh: (document.getElementById('rs-lh-val') || {}).textContent };
});

const steps = [];
for (const id of ['feishu', 'console', 'default']) {
  await p.selectOption('#rs-theme', id);
  await p.waitForTimeout(1200);
  steps.push(await p.evaluate(sel => ({
    sel,
    value: document.getElementById('rs-theme').value,
    desc: (document.getElementById('rs-theme-desc') || {}).textContent.slice(0, 24),
    lh: (document.getElementById('rs-lh-val') || {}).textContent,
    meta: (document.getElementById('rs-meta') || {}).textContent.slice(-40),
    ver: (document.getElementById('rs-pill') || {}).textContent,
    cssHasTheme: (document.getElementById('rs-css') || {}).textContent.slice(0, 60),
  }), id));
}

console.log(JSON.stringify({ opts, steps, errs }, null, 1));
await b.close();
