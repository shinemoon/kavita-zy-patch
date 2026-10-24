import { chromium } from '/home/youruser/.picoclaw/workspace/skills/paperecho-receiver/bin/node_modules/playwright/index.mjs';
import fs from 'fs';

const ck = JSON.parse(fs.readFileSync('/tmp/zy-cookies-real.json', 'utf8'));
const P = 'p29=zye5b814&p2=104583&p3=101010010&p4=501603&p33=com.chaozh.iReaderFree';
const USR = '__ZY_USR__', ZYSID = '__ZY_ZYSID__';

const b = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
const ctx = await b.newContext();
await ctx.addCookies(ck.map(c => ({ name: c.name, value: c.value, domain: c.domain, path: c.path || '/' })));
const p = await ctx.newPage();

const captured = [];
p.on('response', async (r) => {
  if (!/zhangyue\.com|ireader\.com/.test(r.url())) return;
  if (/\.(png|jpg|jpeg|gif|css|js|woff2?|svg|ico)/.test(r.url())) return;
  let t = '';
  try { t = (await r.text()).slice(0, 20000); } catch {}
  captured.push({ url: r.url(), status: r.status(), body: t });
});

// load shelf and click into a book -> watch what the reader calls
await p.goto('https://pc.ireader.com/bookshelf', { waitUntil: 'networkidle', timeout: 45000 });
await p.waitForTimeout(4000);
fs.writeFileSync('/tmp/zy-capture-shelf.json', JSON.stringify(captured, null, 2));
console.log('SHELF captured ' + captured.length);

for (const c of captured) {
  if (c.url.includes('bookshelf/list')) {
    fs.writeFileSync('/tmp/zy-shelf.json', c.body);
    const j = JSON.parse(c.body);
    const books = j.body?.book_list || j.body?.list || j.body || [];
    const arr = Array.isArray(books) ? books : Object.values(books)[0] || [];
    console.log('BOOKS ' + arr.length);
    for (const bk of arr) {
      console.log(JSON.stringify({
        name: bk.book_name, id: bk.book_id, type: bk.book_type,
        local: bk.is_local_book, author: bk.author, size: bk.book_size,
        sold: bk.sale_flag ?? bk.sold_status, keys: Object.keys(bk).filter(k => /local|type|asset|server|download|price|sold|free/i.test(k))
      }));
    }
  }
}
await b.close();