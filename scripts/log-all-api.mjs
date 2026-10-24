import { chromium } from '/home/youruser/.picoclaw/workspace/skills/paperecho-receiver/bin/node_modules/playwright/index.mjs';
import fs from 'fs';

const ck = JSON.parse(fs.readFileSync('/vol2/1001/zhangyue-bridge/.auth-cookies.json', 'utf8'));
const b = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
const ctx = await b.newContext();
await ctx.addCookies(ck.map(c => ({ name: c.name, value: c.value, domain: c.domain, path: c.path || '/' })));
const p = await ctx.newPage();

// log EVERY api call the pc site makes, on the plain bookshelf page
const seen = new Map();
p.on('response', async (r) => {
  const u = r.url();
  if (!/zhangyue\.com/.test(u)) return;
  if (/\.(png|jpg|jpeg|gif|css|js|woff2?|svg|ico|map)$/.test(u)) return;
  const path = u.replace(/^https:\/\/[^/]+/, '').split('?')[0];
  if (!seen.has(path)) {
    seen.set(path, true);
    let t = '';
    try { t = (await r.text()).slice(0, 600); } catch {}
    console.log(r.status() + '  ' + r.request().method() + '  ' + path);
    if (t && t.length < 600 && !t.startsWith('<')) console.log('      ' + t);
  }
});

await p.goto('https://pc.ireader.com/bookshelf', { waitUntil: 'networkidle', timeout: 45000 });
await p.waitForTimeout(5000);
console.log('FINAL URL: ' + p.url());
console.log('TITLE: ' + await p.title());
await b.close();