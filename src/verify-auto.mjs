// 无登录验证 v0.4.0 读书钩子: cfg 读取 → routeTick → auto/pull 调用 → 日志
import { chromium } from 'playwright';

(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  const page = await ctx.newPage();
  const netlog = [];
  page.on('response', async (r) => {
    const u = r.url();
    if (u.indexOf('5111') >= 0) netlog.push(r.status() + ' ' + u.replace(/^https?:\/\/[^/]+/, ''));
  });
  page.on('pageerror', (e) => console.log('PAGEERROR', e.message));

  await page.goto('http://127.0.0.1:5000/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(4000);

  const cfg = await page.evaluate(() => (window.__ZY__ ? window.__ZY__.auto() : null));
  console.log('auto state after boot:', JSON.stringify(cfg && { on: cfg.on, gap: cfg.gap, deb: cfg.debounce }));

  // 用 pushState 模拟 SPA 进书, 触发 routeTick
  await page.evaluate(() => {
    history.pushState({}, '', '/library/1/series/26/book/30');
  });
  await page.waitForTimeout(6000);

  const after = await page.evaluate(() => (window.__ZY__ ? window.__ZY__.auto() : null));
  console.log('auto log:', JSON.stringify(after && after.log, null, 0));
  console.log('net to 5111:', JSON.stringify(netlog));

  // 手动模拟一次 Kavita 存进度 (401 也算, 钩子挂在 send 上)
  await page.evaluate(() => {
    const x = new XMLHttpRequest();
    x.open('POST', '/api/reader/progress');
    x.setRequestHeader('Content-Type', 'application/json');
    x.send(JSON.stringify({ libraryId: 1, seriesId: 26, volumeId: 30, chapterId: 30, pageNum: 48 }));
  });
  await page.waitForTimeout(11000);
  const tail = await page.evaluate(() => (window.__ZY__ ? window.__ZY__.auto().log.slice(-6) : []));
  console.log('log tail:', JSON.stringify(tail));
  console.log('net to 5111 final:', JSON.stringify(netlog));

  await browser.close();
})().catch((e) => { console.error('FATAL', e); process.exit(1); });
