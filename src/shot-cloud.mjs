import { chromium } from 'playwright';

const url = process.env.SHOT_URL || 'http://127.0.0.1:5012/';
const out = process.env.SHOT_OUT || '/tmp/cloud-card.png';

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1400, height: 1600 } });
const errors = [];
page.on('pageerror', e => errors.push(String(e)));
page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
await page.goto(url, { waitUntil: 'networkidle' });
await page.waitForTimeout(2500);

const card = page.locator('#cloud-card');
const info = await page.evaluate(() => {
  const t = id => (document.getElementById(id)?.textContent || '').trim();
  const rows = [...document.querySelectorAll('#cl-list tbody tr')].map(tr =>
    [...tr.querySelectorAll('td')].map(td => td.textContent.replace(/\s+/g, ' ').trim()).slice(0, 2));
  return {
    pill: t('cl-pill'), meta: t('cl-meta'), hint: t('cl-dir-hint'),
    dest: document.getElementById('cl-dest')?.value, rows,
    hasSave: !!document.getElementById('b-cl-dir-save'),
    hasRoots: !!document.getElementById('cl-roots'),
    localRows: [...document.querySelectorAll('#cl-local tbody tr')].length,
  };
});
await card.screenshot({ path: out });
console.log(JSON.stringify({ ...info, errors }, null, 2));
await browser.close();
