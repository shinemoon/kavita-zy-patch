// 5012 掌阅云书籍卡片: 确认
//   1) 顶部有"重排版/字节数不同属预期"的说明
//   2) 页面里不再出现 "≠ 云端" 这种错误提示
//   3) 已下载的行有「已切片」pill + 「补齐」按钮 + 「补齐收尾」总按钮
//   4) 本地清单里每行也有「补齐」
// 用法: node src/probe-cloud-finalize-ui.mjs [origin]
import { chromium } from 'playwright';

const ORIGIN = process.argv[2] || 'http://127.0.0.1:5012';

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1400, height: 1400 } });
const errs = [];
page.on('pageerror', (e) => errs.push(String(e)));

await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
// 云书籍是折叠卡, 需要先展开才会渲染列表
await page.click('#sec-cloud > summary');
await page.waitForTimeout(1500);

const r = await page.evaluate(() => {
  const txt = document.body.innerText;
  const cloud = document.querySelector('#cl-list');
  const rows = cloud ? Array.from(cloud.querySelectorAll('tbody tr')) : [];
  return {
    hasNote: txt.includes('重排版') && txt.includes('预期行为'),
    hasNotEqual: txt.includes('≠ 云端'),
    hasFinalizeAll: !!document.querySelector('#b-cl-finalize'),
    noteText: (document.querySelector('#cl-note')?.innerText || '').slice(0, 120),
    rowCount: rows.length,
    rows: rows.slice(0, 3).map((tr) => ({
      text: tr.innerText.replace(/\s+/g, ' ').slice(0, 90),
      // 多卷 / 图片型书按设计不切片 (原书 pill), 这也算正常状态
      hasEditionPill: tr.innerText.includes('已切片') || tr.innerText.includes('原书'),
      hasFinalize: !!tr.querySelector('[data-fin]'),
    })),
  };
});

// 本地清单
await page.click('#sec-local > summary');
await page.waitForTimeout(1200);
const local = await page.evaluate(() => {
  const host = document.querySelector('#cl-local');
  const rows = host ? Array.from(host.querySelectorAll('tbody tr')) : [];
  return {
    rowCount: rows.length,
    rows: rows.slice(0, 3).map((tr) => ({
      text: tr.innerText.replace(/\s+/g, ' ').slice(0, 90),
      hasFinalize: !!tr.querySelector('[data-fin]'),
    })),
  };
});

await page.screenshot({ path: '/tmp/cloud-finalize-ui.png', fullPage: true });
await browser.close();

const ok =
  r.hasNote && !r.hasNotEqual && r.hasFinalizeAll &&
  r.rows.length > 0 && r.rows.every((x) => x.hasEditionPill && x.hasFinalize) &&
  local.rows.length > 0 && local.rows.every((x) => x.hasFinalize) &&
  errs.length === 0;

console.log(JSON.stringify({ ok, cloud: r, local, errs }, null, 2));
process.exit(ok ? 0 : 1);
