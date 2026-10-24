#!/bin/bash
P="p29=zye5b814&p2=104583&p3=101010010&p4=501603&p33=com.chaozh.iReaderFree&usr=__ZY_USR__&zysid=__ZY_ZYSID__"
B=6818BFD9C92C8576A0E21AD13CD28CC2
U=https://api-pc.zhangyue.com/bookstore/open/cloud/book/getProgress

for K in BookID BookId bookID bookid Book_ID BOOK_ID id; do
  printf '%-10s ' "$K"
  curl -s "$U?$K=$B&$P" | head -c 260
  echo
done