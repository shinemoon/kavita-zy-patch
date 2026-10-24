// 验证「浏览器经域名访问 Kavita」时自动同步是否真的通 (2026-10-24)
//   node src/verify-domain.mjs http://iclaud.zicp.fun:5000
// 关注点: base 探测落到哪个 origin (5012 反代 / 5111 直连 / 同源),
//         进书 pull、存进度 push 是否都 200。
import { chromium } from 'playwright';

const ORIGIN = process.argv[2] || 'http://iclaud.zicp.fun:5000';
const BOOK = process.env.ZY_BOOK_PATH || '/library/1/series/27/book/31';

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
const page = await ctx.newPage();
const net = [];
page.on('requestfailed', (r) => {
  const u = r.url();
  if (u.indexOf('/zy/api') >= 0) net.push('FAILED ' + u + ' :: ' + (r.failure() && r.failure().errorText));
});
page.on('response', (r) => {
  const u = r.url();
  if (u.indexOf('/zy/api') >= 0) net.push(r.status() + ' ' + u.replace(/^https?:\/\/[^/]+/, ''));
});
page.on('pageerror', (e) => net.push('PAGEERROR ' + e.message));

console.log('origin =', ORIGIN);
await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded', timeout: 25000 });
await page.waitForTimeout(5000);

const boot = await page.evaluate(() => (window.__ZY__ ? window.__ZY__.auto() : null));
console.log('ver  =', await page.evaluate(() => window.__ZY__ && window.__ZY__.ver));
console.log('base =', boot && boot.base);
console.log('on   =', boot && boot.on);

await page.evaluate((p) => history.pushState({}, '', p), BOOK);
await page.waitForTimeout(6000);

for (let i = 0; i < 3; i++) {
  await page.evaluate(() => {
    const x = new XMLHttpRequest();
    x.open('POST', '/api/reader/progress');
    x.setRequestHeader('Content-Type', 'application/json');
    x.send(JSON.stringify({ libraryId: 1, seriesId: 27, volumeId: 27, chapterId: 31, pageNum: 821 }));
  });
  await page.waitForTimeout(3000);
}
await page.waitForTimeout(12000);

const st = await page.evaluate(() => (window.__ZY__ ? window.__ZY__.auto() : null));
console.log('log  =', JSON.stringify(st && st.log, null, 1));
console.log('net  =', JSON.stringify([...new Set(net)], null, 1));
await browser.close();
