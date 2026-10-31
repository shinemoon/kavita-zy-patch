// 5012 控制台「阅读样式增强」卡片的 UI 冒烟: 折叠/展开 + 增删规则
import { chromium } from 'playwright';
import { addZyCookie } from './zyauth.mjs';

const URL = process.argv[2] || 'http://127.0.0.1:5012/';
const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1400, height: 1000 } });
const page = await ctx.newPage();
const errs = [];
page.on('pageerror', e => errs.push('PAGEERR ' + e.message));
page.on('console', m => { if (m.type() === 'error') errs.push('CONSOLE ' + m.text().slice(0, 160)); });

await addZyCookie(page);

await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 20000 });
await page.waitForTimeout(3000);

const before = await page.evaluate(() => ({
  open: document.getElementById('sec-rs').open,
  pill: document.getElementById('rs-pill').textContent,
  rows: document.querySelectorAll('#rs-items .rs-item').length,
  lh: document.getElementById('rs-lh').value,
  lhVal: document.getElementById('rs-lh-val').textContent,
  presets: document.querySelectorAll('#rs-preset option').length,
}));

// 展开 + 添加一条规则, 再删掉 (不保存)
await page.evaluate(() => { document.getElementById('sec-rs').open = true; });
await page.selectOption('#rs-preset', '1');
await page.click('#b-rs-add');
await page.waitForTimeout(300);
const added = await page.evaluate(() => ({
  rows: document.querySelectorAll('#rs-items .rs-item').length,
  lastName: [...document.querySelectorAll('#rs-items .rs-name')].pop().value,
}));
await page.evaluate(() => { [...document.querySelectorAll('#rs-items .rs-del')].pop().click(); });
await page.waitForTimeout(600);
const afterDel = await page.evaluate(() => ({
  rows: document.querySelectorAll('#rs-items .rs-item').length,
  meta: document.getElementById('rs-meta').textContent.slice(0, 90),
}));

// 行间距滑块 -> 自动保存
await page.evaluate(() => {
  const s = document.getElementById('rs-lh');
  s.value = '1.9';
  s.dispatchEvent(new Event('input'));
});
await page.waitForTimeout(2000);
const afterSlide = await page.evaluate(() => ({
  pill: document.getElementById('rs-pill').textContent,
  decl: document.querySelector('#rs-items .rs-item .rs-decl').value,
  meta: document.getElementById('rs-meta').textContent.slice(0, 90),
}));

console.log(JSON.stringify({ before, added, afterDel, afterSlide, errs: errs.slice(0, 5) }, null, 1));
await browser.close();
