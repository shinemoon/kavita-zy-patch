import { chromium } from '/home/youruser/.picoclaw/workspace/skills/paperecho-receiver/bin/node_modules/playwright/index.mjs';

const b = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
const ctx = await b.newContext({
  userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
  viewport: { width: 1440, height: 900 },
});
const p = await ctx.newPage();

const SKIP = ['_nuxt', 'zhangyuecdn', '.css', '.png', '.jpg', '.svg', '.woff', '.ico'];
const ok = (u) => !SKIP.some((k) => u.includes(k));
const net = [];

p.on('request', (r) => { if (ok(r.url())) net.push('> ' + r.method() + ' ' + r.url()); });
p.on('response', (r) => { if (ok(r.url())) net.push('  <- ' + r.status() + ' ' + r.url()); });

await p.goto('https://pc.ireader.com/login', { waitUntil: 'networkidle', timeout: 30000 });
await p.waitForTimeout(1500);

const btn = await p.$('button:has-text("登录")');
if (btn) { await btn.click(); await p.waitForTimeout(4500); }

console.log('URL AFTER:', p.url());
console.log('=== NET ===');
net.slice(-30).forEach((s) => console.log(s));
console.log('=== TEXT ===');
console.log((await p.evaluate(() => document.body.innerText)).slice(0, 700));
console.log('=== INPUTS ===');
console.log(await p.evaluate(() => Array.from(document.querySelectorAll('input')).map((e) => e.type + '|' + (e.name || '') + '|' + (e.placeholder || '')).join('\n')));
console.log('=== IFRAMES ===');
console.log(await p.evaluate(() => Array.from(document.querySelectorAll('iframe')).map((e) => e.src).join('\n')));
console.log('=== COOKIES ===');
console.log((await ctx.cookies()).map((c) => c.name + '=' + String(c.value).slice(0, 20)).join('\n'));

await p.screenshot({ path: '/tmp/zhangyue-login2.png' });
await b.close();