import { chromium } from 'playwright';
import { addZyCookie } from './zyauth.mjs';
const b = await chromium.launch({ args: ['--no-sandbox'] });
const p = await b.newPage({ viewport: { width: 430, height: 932 } });
const errs = [];
p.on('pageerror', e => errs.push(String(e)));
await addZyCookie(p);
await p.goto('http://127.0.0.1:5012/', { waitUntil: 'networkidle' });
await p.waitForTimeout(1800);

const order = await p.evaluate(() => {
  const ids = ['b-push','b-pull','b-refresh','meta','sec-out','sec-links','sec-cloud','sec-local','sec-auth','sec-kv','sec-log'];
  const tops = {};
  for (const id of ids) { const el = document.getElementById(id);
    tops[id] = el ? Math.round(el.getBoundingClientRect().top + window.scrollY) : null; }
  return tops;
});
const sorted = Object.entries(order).sort((a, b) => a[1] - b[1]).map(x => x[0]);

// 折叠 -> 点击展开 -> 再点收起
const toggle = [];
for (const id of ['sec-cloud', 'sec-kv']) {
  toggle.push([id, 'init', await p.evaluate(i => document.getElementById(i).open, id)]);
  await p.click('#' + id + ' > summary');
  await p.waitForTimeout(150);
  toggle.push([id, 'after1', await p.evaluate(i => document.getElementById(i).open, id)]);
  await p.click('#' + id + ' > summary');
  await p.waitForTimeout(150);
  toggle.push([id, 'after2', await p.evaluate(i => document.getElementById(i).open, id)]);
}

// 点「推到掌阅」→ 执行结果应自动展开
const outBefore = await p.evaluate(() => document.getElementById('sec-out').open);
// 不真跑 push, 直接验证 openSec 生效性: 挂到 push 后观察 2s 内 open 变化
const btn = await p.$('#b-push');
await btn.click();
await p.waitForTimeout(1500);
const outAfter = await p.evaluate(() => document.getElementById('sec-out').open);

console.log(JSON.stringify({ order: sorted, toggle, outBefore, outAfter,
  outHead: await p.evaluate(() => document.getElementById('out-h').textContent), errs }, null, 1));
await b.close();
