import { chromium } from 'playwright';

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1400, height: 1600 } });
await page.goto('http://127.0.0.1:5012/', { waitUntil: 'networkidle' });
await page.waitForTimeout(2500);
const info = await page.evaluate(() => {
  return [...document.querySelectorAll('#cl-list tbody tr')].map(tr => {
    const td = [...tr.querySelectorAll('td')];
    return {
      name: (td[0]?.innerText || '').split('\n')[0],
      local: (td[4]?.innerText || '').replace(/\s+/g, ' ').trim(),
      btns: [...tr.querySelectorAll('button')].map(b => b.textContent.trim()),
    };
  });
});
console.log(JSON.stringify(info, null, 1));
await page.screenshot({ path: process.env.SHOT_OUT || '/tmp/cloud-card.png', clip: await page.locator('#cloud-card').boundingBox() });
await browser.close();
