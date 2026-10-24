import { chromium } from '/home/youruser/.picoclaw/workspace/skills/paperecho-receiver/bin/node_modules/playwright/index.mjs';
import fs from 'fs';

const ck = JSON.parse(fs.readFileSync('/vol2/1001/zhangyue-bridge/.auth-cookies.json', 'utf8'));
const b = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
const ctx = await b.newContext();
await ctx.addCookies(ck.map(c => ({ name: c.name, value: c.value, domain: c.domain, path: c.path || '/' })));
const p = await ctx.newPage();

const calls = [];
p.on('request', (r) => {
  const u = r.url();
  if (/getProgress|chapter-content|chapter\/web\/list|getAssets|book\/detail/i.test(u)) {
    calls.push({ method: r.method(), url: r.url(), headers: r.headers(), post: (r.postData() || '').slice(0, 800) });
  }
});
p.on('response', async (r) => {
  if (/getProgress/i.test(r.url())) {
    let t = ''; try { t = (await r.text()).slice(0, 1500); } catch {}
    console.log('RESP ' + r.status() + ' :: ' + t);
  }
});

// visit a book reader page so progress APIs fire
await p.goto('https://pc.ireader.com/bookshelf', { waitUntil: 'networkidle', timeout: 45000 });
await p.waitForTimeout(2500);
const books = await p.evaluate(() => [...document.querySelectorAll('*')]
  .filter(e => /狐的故事|剑来/.test(e.textContent || '') && e.children.length === 0)
  .map(e => e.textContent.trim()).slice(0, 3));
console.log('CANDIDATES ' + JSON.stringify(books));

// try clicking the first book cover to open reader
const clicked = await p.evaluate(() => {
  const els = [...document.querySelectorAll('div,li,span,img')].filter(e => {
    const t = (e.getAttribute?.('alt') || e.textContent || '').trim();
    return t && t.length < 40 && /狐的故事|剑来|藤井/.test(t);
  });
  if (!els.length) return null;
  els[0].click();
  return els[0].textContent.trim();
});
console.log('CLICKED ' + clicked);
await p.waitForTimeout(9000);
console.log('URL NOW ' + p.url());

fs.writeFileSync('/tmp/zy-reader-calls.json', JSON.stringify(calls, null, 2));
calls.forEach((c, i) => console.log('[' + i + '] ' + c.method + ' ' + c.url.slice(0, 160) + (c.post ? ' BODY=' + c.post.slice(0, 300) : '')));
await b.close();