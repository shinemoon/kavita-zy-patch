#!/bin/bash
P="p29=zye5b814&p2=104583&p3=101010010&p4=501603&p33=com.chaozh.iReaderFree&usr=__ZY_USR__&zysid=__ZY_ZYSID__"
U=https://api-pc.zhangyue.com/bookstore/open/cloud/book/getProgress

echo '### session still alive? (user/info)'
curl -s "https://api-pc.zhangyue.com/bookstore/open/user/info?$P" | head -c 300; echo

echo
echo '### hypothesis: BookID is an int, hex string fails to parse'
for V in 6818BFD9C92C8576A0E21AD13CD28CC2 6818 1 6818BFD9; do
  printf '  BookID=%-34s ' "$V"
  curl -s "$U?BookID=$V&$P" | head -c 200
  echo
done

echo
echo '### the same hex id but as a plain query param name variants'
for K in bookId BookID bookID; do
  printf '  %-8s ' "$K"
  curl -s "$U?$K=6818&$P" | head -c 200
  echo
done