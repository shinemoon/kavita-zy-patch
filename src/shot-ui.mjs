import { chromium } from 'playwright';

const url = 'http://127.0.0.1:5012/';
const browser = await chromium.launch({ args: ['--no-sandbox'] });
const out = [];

// 桌面 + 手机两视口, 都截「全折叠」与「展开云书」两张
for (const [name, vp] of Object.entries({ desk: { width: 1400, height: 1400 },
                                          mob: { width: 430, height: 932 } })) {
  const page = await browser.newPage({ viewport: vp, deviceScaleFactor: 2 });
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
  await page.goto(url, { waitUntil: 'networkidle' });
  await page.waitForTimeout(2500);

  const st = await page.evaluate(() => {
    const t = id => (document.getElementById(id)?.textContent || '').trim();
    const secs = ['sec-out','sec-links','sec-cloud','sec-local','sec-auth','sec-kv','sec-log']
      .map(id => { const el = document.getElementById(id);
                   return { id, open: !!el?.open, sum: (el?.querySelector('summary')?.textContent||'').replace(/\s+/g,' ').trim() }; });
    return { secs, linkCnt: t('link-cnt'), localCnt: t('local-cnt'),
             clPill: t('cl-pill'), authPill: t('auth-pill'), kvPill: t('kv-pill'),
             meta: t('meta').slice(0, 90) };
  });
  out.push({ name, st, errors });
  await page.screenshot({ path: `/tmp/ui-${name}-folded.png`, fullPage: true });

  // 展开云书 + 已下载
  await page.click('#sec-cloud > summary');
  await page.click('#sec-local > summary');
  await page.waitForTimeout(400);
  await page.screenshot({ path: `/tmp/ui-${name}-cloud.png`, fullPage: true });

  // 展开全部
  await page.evaluate(() => document.querySelectorAll('details.sec').forEach(d => d.open = true));
  await page.waitForTimeout(400);
  await page.screenshot({ path: `/tmp/ui-${name}-all.png`, fullPage: true });

  // 点「刷新状态」验证不报错
  await page.click('#b-refresh');
  await page.waitForTimeout(1200);
  out.push({ name: 'after-refresh', errors: errors.slice(0, 5) });
  await page.close();
}
await browser.close();
console.log(JSON.stringify(out, null, 1));
