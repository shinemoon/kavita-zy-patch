import fs from 'fs';
const FILES = ['7dfd733','f31fad6','3bbb088','85f7eb5','37032d8','e92aa23'];
const NEEDLES = [
  'qrcode/result', 'qrcode/get', 'qrcode',
  'img-container', 'qr-code-img', 'qrCodeUrl', 'qrCodeImg',
  'qrImg', 'qrcodeUrl', 'src=', 'toDataURL',
];
for (const f of FILES) {
  const p = `/tmp/zyq-${f}.js`;
  if (!fs.existsSync(p)) continue;
  const s = fs.readFileSync(p, 'utf8');
  for (const n of NEEDLES) {
    let i = -1;
    while ((i = s.indexOf(n, i + 1)) !== -1) {
      if (f === '3bbb088' && (n === 'src=' || n === 'qrcode')) continue; // 库自身
      console.log(`\n--- ${f} [${n}] @${i} ---`);
      console.log(s.slice(Math.max(0, i - 260), i + 260).replace(/\s+/g, ' '));
      break;
    }
  }
}