#!/bin/bash
P="p29=zye5b814&p2=104583&p3=101010010&p4=501603&p33=com.chaozh.iReaderFree&usr=__ZY_USR__&zysid=__ZY_ZYSID__"
BID=6818BFD9C92C8576A0E21AD13CD28CC2

probe() {
  echo "--- $1 $2"
  curl -s -X "$1" "https://api-pc.zhangyue.com$2" -H 'Content-Type: application/json' \
    --data "${3:-}" | head -c 400
  echo
}

echo "########## GET variants ##########"
probe GET  "/bookstore/open/cloud/book/getProgress?book_id=$BID&$P"
probe GET  "/bookstore/open/cloud/book/getProgress?book_ids=$BID&$P"
probe GET  "/bookstore/open/cloud/book/getProgress?bookId=$BID&$P"
probe GET  "/bookstore/open/cloud/book/progress?book_id=$BID&$P"
probe GET  "/bookstore/open/cloud/bookshelf/progress?book_id=$BID&$P"
probe GET  "/bookstore/open/user/book/progress?book_id=$BID&$P"
probe GET  "/bookstore/open/book/progress?book_id=$BID&$P"

echo "########## POST variants (write test, harmless) ##########"
probe POST "/bookstore/open/cloud/book/getProgress" "{\"book_id\":\"$BID\"}"
probe POST "/bookstore/open/cloud/bookshelf/progress/update" "{\"book_id\":\"$BID\",\"section_id\":\"1\",\"offset\":0}"