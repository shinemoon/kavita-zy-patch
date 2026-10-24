import { chromium } from '/home/youruser/.picoclaw/workspace/skills/paperecho-receiver/bin/node_modules/playwright/index.mjs';
import fs from 'fs';

const b = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
const p = await b.newPage();
const apks = new Set();
p.on('request', (r) => { if (/\.apk(\?|$)/i.test(r.url())) apks.add(r.url()); });

for (const url of [
  'https://www.ireader.com.cn/index/download.html',
  'https://www.ireader.com.cn/',
  'https://pc.ireader.com/bookshelf',
]) {
  console.log('=== ' + url);
  try {
    await p.goto(url, { waitUntil: 'networkidle', timeout: 40000 });
    await p.waitForTimeout(3000);
    console.log('  final: ' + p.url());
    const hrefs = await p.evaluate(() =>
      [...document.querySelectorAll('a')]
        .map(a => a.href)
        .filter(h => /\.apk/i.test(h) || /download/i.test(h))
        .slice(0, 25));
    hrefs.forEach(h => console.log('  a: ' + h));
  } catch (e) { console.log('  ERR ' + e.message.slice(0, 90)); }
}
console.log('\nAPK requests seen:');
[...apks].forEach(u => console.log('  ' + u));
fs.writeFileSync('/tmp/zy-apk-links.txt', [...apks].join('\n'));
await b.close();