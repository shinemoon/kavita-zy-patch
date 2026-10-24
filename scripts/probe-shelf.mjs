import { chromium } from '/home/youruser/.picoclaw/workspace/skills/paperecho-receiver/bin/node_modules/playwright/index.mjs';
import fs from 'fs';

const WAIT = Number(process.argv[2] || 220);
const AUTH = JSON.parse(fs.readFileSync('/vol2/1001/zhangyue-bridge/.auth.json', 'utf8'));

const b = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
const ctx = await b.newContext({
  userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
  viewport: { width: 1440, height: 900 },
});
const p = await ctx.newPage();

// inject the session storage + cookies so the page considers us logged in
await ctx.addCookies([
  { name: 'zysid', value: AUTH.zysid, domain: '.zhangyue.com', path: '/', httpOnly: false, secure: true, sameSite: 'None' },
]);
await ctx.addInitScript((a) => {
  try {
    window.localStorage.setItem('userInfo', JSON.stringify({ usr: a.usr, zysid: a.zysid }));
  } catch (e) {}
}, AUTH);

p.on('request', (r) => {
  const u = r.url();
  if (/zhangyue|ireader/.test(u)) {
    const h = r.headers();
    console.log('REQ', r.method(), u.slice(0, 200));
  }
});
p.on('response', async (r) => {
  const u = r.url();
  if (!/zhangyue|ireader/.test(u)) return;
  try {
    const ct = r.headers()['content-type'] || '';
    if (/json|octet|binary/.test(ct) || /download|file|content|book/.test(u)) {
      const buf = await r.body().catch(() => null);
      if (buf && buf.length > 100) {
        console.log('RESP', r.status(), u.slice(0, 200), 'ct=' + ct, 'len=' + buf.length);
      } else {
        console.log('RESP', r.status(), u.slice(0, 200), 'ct=' + ct);
      }
    }
  } catch (e) {}
});

await p.goto('https://pc.ireader.com', { waitUntil: 'domcontentloaded', timeout: 30000 });
await p.waitForTimeout(3000);

console.log('--- shelving click ---');
// try clicking the "书架 / 全部" / "开始阅读" type entry
const links = await p.$$eval('a, button', (els) =>
  els.map((e, i) => ({ i, txt: (e.innerText || '').trim().slice(0, 40), href: e.getAttribute('href') || '' })).filter((x) => x.txt || x.href)
);
console.log('LINKS_FOUND', links.length);
// print first 30
for (const x of links.slice(0, 30)) console.log('  ', x.i, x.txt.slice(0, 30), x.href.slice(0, 80));

// Try shelves URL
console.log('--- goto shelves page directly ---');
await p.goto('https://pc.ireader.com/index.php?ca=shelf&pg=0', { waitUntil: 'domcontentloaded', timeout: 30000 });
await p.waitForTimeout(2500);

// hook fetch so we see what the site itself calls
await p.evaluate(() => {
  const orig = window.fetch;
  window.fetch = function (...args) {
    const u = typeof args[0] === 'string' ? args[0] : args[0].url;
    console.log('FETCH', u);
    return orig.apply(this, args);
  };
});

const heading = await p.evaluate(() => document.body.innerText.slice(0, 400));
console.log('HEAD:', heading);

const dl = Date.now() + WAIT * 1000;
let last = '';
while (Date.now() < dl) {
  await p.waitForTimeout(2500);
  const cookies = await ctx.cookies();
  const zy = cookies.find((c) => /zysid|session/i.test(c.name));
  if (zy) {
    cookies.forEach((c) => fs.appendFileSync('/tmp/zy-cookies.txt', c.name + '=' + c.value + '\n'));
  }
}
console.log('TIMEOUT');