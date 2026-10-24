#!/bin/bash
# fetch all shelf epubs via OSS signed urls
set -u
P="p29=zye5b814&p2=104583&p3=101010010&p4=501603&p33=com.chaozh.iReaderFree&usr=__ZY_USR__&zysid=__ZY_ZYSID__"
OUT=/vol2/1001/zhangyue-bridge/books
mkdir -p "$OUT"

python3 - <<'EOF' > /tmp/zy-fileids.txt
import json
d = json.load(open('/tmp/zy-nuxt.json'))
bl = d['state']['bookshelf']['list']
bi = d['state']['bookshelf'].get('bookInfo') or {}
for bk in bl:
    fid = bk.get('book_file_id') or bk.get('book_id')
    extra = (bi.get(bk['book_id']) or {}).get('cover_file_id') or ''
    print(f"{bk['book_id']}|{fid}|{bk['book_name']}|{bk.get('book_file_size')}|{extra}")
EOF

echo "id|file_id|name|size|cover"
cat /tmp/zy-fileids.txt

while IFS='|' read -r BID FID NAME SIZE COVER; do
  [ -z "$FID" ] && continue
  SAFE=$(echo "$NAME" | tr -d '/\\:*?"<>|' | tr ' ' '_')
  DEST="$OUT/${SAFE}.epub"
  if [ -s "$DEST" ]; then echo "SKIP $SAFE (exists)"; continue; fi
  URL=$(curl -s "https://api-pc.zhangyue.com/bookstore/open/cloud/storage/file/info?file_ids=$FID&$P" \
        | python3 -c "import sys,json;d=json.load(sys.stdin);print((d.get('body') or {}).get('$FID',{}).get('sign_url',''))" 2>/dev/null)
  if [ -z "$URL" ]; then echo "NO_URL $SAFE"; continue; fi
  curl -sL --max-time 300 "$URL" -o "$DEST"
  echo "GOT $SAFE $(stat -c%s "$DEST" 2>/dev/null) bytes (expect $SIZE)"
done < /tmp/zy-fileids.txt

echo '--- result ---'
ls -la "$OUT"
for f in "$OUT"/*.epub; do
  echo "$(basename "$f") :: $(file -b "$f" | head -c 60)"
done