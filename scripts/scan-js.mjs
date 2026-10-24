import fs from 'fs';
const FILES = ['7dfd733','f31fad6','3bbb088','85f7eb5','37032d8','e92aa23'];
const HITS = /qrcode|qrCode|toDataURL|QRCode|qr-code/gi;
for (const f of FILES) {
  const p = `/tmp/zyq-${f}.js`;
  if (!fs.existsSync(p)) { console.log(f, 'MISSING'); continue; }
  const s = fs.readFileSync(p, 'utf8');
  const lines = s.split('\n');
  lines.forEach((ln, i) => {
    if (HITS.test(ln)) {
      HITS.lastIndex = 0;
      console.log(`--- ${f} L${i} ---`);
      console.log(ln.slice(0, 400));
    }
    HITS.lastIndex = 0;
  });
}