#!/bin/bash
P="p29=zye5b814&p2=104583&p3=101010010&p4=501603&p33=com.chaozh.iReaderFree&usr=__ZY_USR__&zysid=__ZY_ZYSID__"

for FID in 6818BFD9C92C8576A0E21AD13CD28CC2 70776A64C32C1C065DAB241F3E9F133A; do
  echo "=== file/info $FID ==="
  curl -s "https://api-pc.zhangyue.com/bookstore/open/cloud/storage/file/info?file_ids=$FID&$P" | head -c 900
  echo
done

echo "=== batch file/info (both) ==="
curl -s "https://api-pc.zhangyue.com/bookstore/open/cloud/storage/file/info?file_ids=6818BFD9C92C8576A0E21AD13CD28CC2,70776A64C32C1C065DAB241F3E9F133A&$P" | head -c 1200
echo

echo "=== asset (trade) ==="
curl -s "https://api-pc.zhangyue.com/trade/open/book/asset?book_id=6818BFD9C92C8576A0E21AD13CD28CC2&$P" | head -c 500
echo
echo "=== book detail ==="
curl -s "https://api-pc.zhangyue.com/bookstore/open/book/detail?book_id=6818BFD9C92C8576A0E21AD13CD28CC2&$P" | head -c 500
echo