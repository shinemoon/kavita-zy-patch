import { chromium } from '/home/youruser/.picoclaw/workspace/skills/paperecho-receiver/bin/node_modules/playwright/index.mjs';
import fs from 'fs';

const ck = JSON.parse(fs.readFileSync('/tmp/zy-cookies-real.json', 'utf8'));
const b = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
const ctx = await b.newContext();
await ctx.addCookies(ck.map(c => ({ name: c.name, value: c.value, domain: c.domain, path: c.path || '/' })));
const p = await ctx.newPage();

await p.goto('https://pc.ireader.com/bookshelf', { waitUntil: 'networkidle', timeout: 45000 });
await p.waitForTimeout(4000);

const data = await p.evaluate(() => {
  const raw = window.__NUXT__;
  return raw ? JSON.stringify(raw) : null;
});
fs.writeFileSync('/tmp/zy-nuxt.json', data || '{}');
console.log('NUXT bytes=' + (data || '').length);

// also grab the DOM anchors for each book
const dom = await p.evaluate(() => {
  const out = [];
  document.querySelectorAll('a[href]').forEach((a) => {
    const t = (a.innerText || '').trim().replace(/\s+/g, ' ');
    if (t && /book|read|detail/i.test(a.getAttribute('href') || '')) out.push({ text: t.slice(0, 60), href: a.getAttribute('href') });
  });
  return out;
});
fs.writeFileSync('/tmp/zy-shelf-links.json', JSON.stringify(dom, null, 2));
console.log('LINKS ' + dom.length);
dom.slice(0, 20).forEach(d => console.log('  ' + d.text + '  ->  ' + d.href));
await b.close();