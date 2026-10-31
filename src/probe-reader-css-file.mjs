// 5012 控制台「阅读样式增强」文件侧 (Patch 到 Kavita 文件 / 从文件移除) 冒烟
// 不改行间距等配置值, 只点两个按钮 + 读状态文案。
// 结束时保证文件侧是「已 patch」状态。
import { chromium } from 'playwright';
import { addZyCookie } from './zyauth.mjs';

const URL = process.argv[2] || 'http://127.0.0.1:5012/';
const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1400, height: 1000 } });
const page = await ctx.newPage();
const errs = [];
page.on('pageerror', e => errs.push('PAGEERR ' + e.message));
page.on('console', m => { if (m.type() === 'error') errs.push('CONSOLE ' + m.text().slice(0, 160)); });
page.on('dialog', d => d.accept());

await addZyCookie(page);

await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 20000 });
await page.waitForTimeout(3000);

const read = () => page.evaluate(() => ({
  file: (document.getElementById('rs-file') || {}).textContent || '',
  hasPatchBtn: !!document.getElementById('b-rs-patch'),
  hasUnpatchBtn: !!document.getElementById('b-rs-unpatch'),
  pill: (document.getElementById('rs-pill') || {}).textContent || '',
}));

const before = await read();

await page.evaluate(() => { document.getElementById('sec-rs').open = true; });

await page.click('#b-rs-unpatch');
await page.waitForTimeout(1500);
const afterUnpatch = await read();

await page.click('#b-rs-patch');
await page.waitForTimeout(1500);
const afterPatch = await read();

// 再点一次 patch: 应该报"无变化"而不是重复插入
await page.click('#b-rs-patch');
await page.waitForTimeout(1500);
const afterPatch2 = await read();

console.log(JSON.stringify({ before, afterUnpatch, afterPatch, afterPatch2, errs: errs.slice(0, 5) }, null, 1));
await browser.close();
