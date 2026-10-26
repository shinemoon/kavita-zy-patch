// 校验 v0.4.9: 提示默认静默 —— 成功不弹, 失败才弹 (半透明黑底 + 暗红描边)
import { chromium } from 'playwright';

const BASE = process.argv[2] || 'http://127.0.0.1:5000/';
const browser = await chromium.launch({ args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
const errs = [];
page.on('pageerror', e => errs.push('PAGEERR ' + e.message));
page.on('dialog', d => d.dismiss());

const toastState = () => page.evaluate(() => {
  const n = document.getElementById('zy-toast');
  if (!n) return { exists: false };
  const cs = getComputedStyle(n);
  return { exists: true, on: n.classList.contains('on'), err: n.classList.contains('err'),
           text: n.textContent, opacity: cs.opacity, bg: cs.backgroundColor,
           border: cs.borderTopColor, transition: cs.transitionDuration };
});

await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 20000 });
await page.waitForTimeout(4000);
const ver = await page.evaluate(() => window.__ZY__ && window.__ZY__.ver);

// 1) 正常路径: 进书 → auto pull 跑完, 不该有任何 toast
await page.evaluate(() => history.pushState({}, {}, '/library/1/series/27/book/31'));
await page.waitForTimeout(6000);
const afterPull = await toastState();
const log1 = await page.evaluate(() => (window.__ZY__.auto() || {}).log || []);

// 2) 失败路径: 让 /auto/push 直接 reject, 再喂一次存进度 → 必须弹 toast (err)
await page.evaluate(() => {
  const orig = window.fetch;
  window.fetch = function (u, init) {
    if (String(u).indexOf('/auto/push') >= 0) return Promise.reject(new Error('backend down'));
    return orig.apply(this, arguments);
  };
  const x = new XMLHttpRequest();
  x.open('POST', '/api/reader/progress');
  x.setRequestHeader('Content-Type', 'application/json');
  x.send(JSON.stringify({ libraryId: 1, seriesId: 27, volumeId: 27, chapterId: 31, pageNum: 821 }));
});
let visible = null;
for (let i = 0; i < 60; i++) {
  await page.waitForTimeout(300);
  const st = await toastState();
  if (st.exists && st.on) { visible = st; break; }
}
if (visible) await page.screenshot({ path: '/tmp/zy-toast-fail.png' });
await page.waitForTimeout(5000);
const afterFail = await toastState();

// 3) 成功路径: 伪造 pushed=1 的成功响应 → 必须仍然静默 (元素已淡出 / 没有新 toast)
await page.evaluate(() => {
  const orig = window.fetch;
  window.fetch = function (u, init) {
    if (String(u).indexOf('/auto/push') >= 0) {
      return Promise.resolve(new Response(JSON.stringify({
        ok: true, series_id: 27,
        detail: { pushed: 1, skipped: 0, failed: 0,
                  detail: [{ ok: true, pct: 0.278 }] }
      }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    }
    return orig.apply(this, arguments);
  };
});
const beforeOk = await toastState();
await page.evaluate(() => {
  const x = new XMLHttpRequest();
  x.open('POST', '/api/reader/progress');
  x.setRequestHeader('Content-Type', 'application/json');
  x.send(JSON.stringify({ libraryId: 1, seriesId: 27, volumeId: 27, chapterId: 31, pageNum: 822 }));
});
await page.waitForTimeout(12000);
const afterOk = await toastState();
const log3 = await page.evaluate(() => (window.__ZY__.auto() || {}).log || []);

console.log(JSON.stringify({ ver, afterPull, pullLog: log1.slice(-3), afterFail, visible,
                             beforeOk, afterOk, okLog: log3.slice(-2),
                             errs: errs.slice(0, 3) }, null, 1));
await browser.close();
