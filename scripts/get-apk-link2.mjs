import { chromium } from '/home/youruser/.picoclaw/workspace/skills/paperecho-receiver/bin/node_modules/playwright/index.mjs';
import fs from 'fs';

const b = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
const p = await b.newPage();
const apks = new Set();
p.on('request', (r) => { if (/\.apk(\?|$)/i.test(r.url())) apks.add(r.url()); });

const urls = [
  'https://www.iReader.cn/',
  'https://www.iuuu9.com/app/history-com.chaozh.iReaderFree',
  'https://www.iuuu9.com/app/com.chaozh.iReaderFree',
  'http://m.5577.com/d/5568',
];

for (const url of urls) {
  console.log('=== ' + url);
  try {
    await p.goto(url, { waitUntil: 'domcontentloaded', timeout: 35000 });
    await p.waitForTimeout(4000);
    console.log('  final: ' + p.url() + '  title: ' + (await p.title()).slice(0, 60));
    const links = await p.evaluate(() =>
      [...document.querySelectorAll('a')].map(a => a.href)
        .filter(h => /\.apk|\.apk\?/i.test(h) || /down|download/i.test(h)).slice(0, 20));
    links.forEach(h => console.log('  a: ' + h));
    const btn = await p.evaluate(() => {
      const e = [...document.querySelectorAll('a,button,div')]
        .find(x => /下载|download/i.test(x.textContent || '') && x.children.length === 0);
      return e ? (e.tagName + ' :: ' + e.textContent.trim().slice(0, 40) + ' :: ' + (e.closest('a')?.href || '')) : null;
    });
    if (btn) console.log('  btn: ' + btn);
  } catch (e) { console.log('  ERR ' + e.message.slice(0, 90)); }
}
console.log('\nAPK URLs:');
[...apks].forEach(u => console.log('  ' + u));
fs.writeFileSync('/tmp/zy-apk-links.txt', [...apks].join('\n'));
await b.close();