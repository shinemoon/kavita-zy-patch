import { chromium } from '/home/youruser/.picoclaw/workspace/skills/paperecho-receiver/bin/node_modules/playwright/index.mjs';
import fs from 'fs';

const ck = JSON.parse(fs.readFileSync('/vol2/1001/zhangyue-bridge/.auth-cookies.json', 'utf8'));
const b = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
const ctx = await b.newContext();
await ctx.addCookies(ck.map(c => ({ name: c.name, value: c.value, domain: c.domain, path: c.path || '/' })));
const p = await ctx.newPage();

const hits = [];
p.on('response', async (r) => {
  const u = r.url();
  if (!/_nuxt\/.*\.js/.test(u)) return;
  try {
    const t = await r.text();
    if (/getProgress|BookID|book_progress|updateProgress|setProgress|ProcessRequest/i.test(t)) {
      hits.push({ url: u, text: t });
      fs.writeFileSync('/tmp/zy-js-' + (hits.length) + '.js', t);
    }
  } catch {}
});

await p.goto('https://pc.ireader.com/bookshelf', { waitUntil: 'networkidle', timeout: 45000 });
await p.waitForTimeout(3000);
console.log('MATCHING BUNDLES: ' + hits.length);
hits.forEach((h, i) => console.log('  [' + i + '] ' + h.url.split('/').pop() + '  bytes=' + h.text.length));
await b.close();