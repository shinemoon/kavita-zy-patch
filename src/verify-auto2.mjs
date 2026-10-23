// 验证 v0.4.3: base 探测 + 连续存进度时的最大等待 push (防"读得快永远不推")
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

  const boot = await page.evaluate(() => (window.__ZY__ ? window.__ZY__.auto() : null));
  console.log('VER', await page.evaluate(() => window.__ZY__ && window.__ZY__.ver));
  console.log('state:', JSON.stringify(boot && { on: boot.on, deb: boot.debounce, gap: boot.gap }));
  console.log('bootlog:', JSON.stringify(boot && boot.log));

  // SPA 进书 → routeTick → pull
  await page.evaluate(() => history.pushState({}, '', '/library/1/series/27/book/31'));
  await page.waitForTimeout(5000);

  // 模拟"连续翻页": 每 4s 存一次进度 (未登录 → 401, 不影响 DB, 只喂钩子)
  for (let i = 0; i < 9; i++) {
    await page.evaluate(() => {
      const x = new XMLHttpRequest();
      x.open('POST', '/api/reader/progress');
      x.setRequestHeader('Content-Type', 'application/json');
      x.send(JSON.stringify({ libraryId: 1, seriesId: 27, volumeId: 27, chapterId: 31, pageNum: 821 }));
    });
    await page.waitForTimeout(4000);
  }
  await page.waitForTimeout(8000);

  const st = await page.evaluate(() => (window.__ZY__ ? window.__ZY__.auto() : null));
  console.log('log:', JSON.stringify(st && st.log));
  console.log('net:', JSON.stringify(netlog));
  await browser.close();
})().catch((e) => { console.error('FATAL', e); process.exit(1); });
