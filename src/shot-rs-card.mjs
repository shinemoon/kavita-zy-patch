// 只截 5012 的「阅读样式增强」卡 (给人看按钮长什么样)
import { chromium } from 'playwright';

const URL = process.argv[2] || 'http://127.0.0.1:5012/';
const OUT = process.argv[3] || '/tmp/rs-card.png';
const browser = await chromium.launch({ args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1200, height: 900 } });
await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 20000 });
await page.waitForTimeout(2500);
await page.evaluate(() => {
  const s = document.getElementById('sec-rs');
  s.open = true;
  s.scrollIntoView();
});
await page.waitForTimeout(800);
await (await page.$('#sec-rs')).screenshot({ path: OUT });
await browser.close();
console.log('shot ->', OUT);
