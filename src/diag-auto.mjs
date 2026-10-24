// 诊断: 从两个 origin 打开 Kavita, 看补丁徽标 / __ZY__ / 到 5111 的请求结果
import { chromium } from 'playwright';

const ORIGINS = process.argv.slice(2);
if (!ORIGINS.length) ORIGINS.push('http://127.0.0.1:5000', 'http://192.168.0.129:5000');

const browser = await chromium.launch({ args: ['--no-sandbox'] });

for (const origin of ORIGINS) {
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  const page = await ctx.newPage();
  const net = [];
  page.on('requestfailed', (r) => net.push('FAILED ' + r.url().slice(0, 90) + ' :: ' + (r.failure() && r.failure().errorText)));
  page.on('response', (r) => {
    const u = r.url();
    if (u.indexOf('/zy/api') >= 0 || u.indexOf(':5111') >= 0) net.push(r.status() + ' ' + u.slice(0, 90));
  });
  page.on('pageerror', (e) => net.push('PAGEERROR ' + e.message));
  page.on('console', (m) => { if (m.type() === 'error') net.push('CONSOLE ' + m.text().slice(0, 160)); });

  console.log('\n===== ' + origin + ' =====');
  try {
    await page.goto(origin + '/', { waitUntil: 'domcontentloaded', timeout: 20000 });
  } catch (e) {
    console.log('GOTO FAIL', e.message);
    await ctx.close();
    continue;
  }
  await page.waitForTimeout(6000);
  const info = await page.evaluate(() => {
    const top = document.getElementById('zy-top-item');
    const badge = document.getElementById('zy-nav-badge');
    const a = window.__ZY__ || null;
    return {
      ver: a ? a.ver : null,
      auto: a && a.auto ? a.auto() : null,
      topCls: top ? top.className : null,
      badge: badge ? badge.textContent : null,
      navItemCls: (document.getElementById('zy-nav-item') || {}).className || null,
      jsSrc: (document.querySelector('script[src*="zy-kavita-patch"]') || {}).src || null,
    };
  });
  console.log(JSON.stringify(info, null, 1));
  console.log('net:', JSON.stringify(net.slice(0, 25), null, 1));
  await ctx.close();
}
await browser.close();
