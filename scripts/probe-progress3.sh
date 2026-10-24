#!/bin/bash
P="p29=zye5b814&p2=104583&p3=101010010&p4=501603&p33=com.chaozh.iReaderFree&usr=__ZY_USR__&zysid=__ZY_ZYSID__"
B=6818BFD9C92C8576A0E21AD13CD28CC2
H=https://api-pc.zhangyue.com/bookstore/open

echo '### path-style'
curl -s "$H/cloud/book/getProgress/$B?$P" | head -c 300; echo

echo '### GET with json body'
curl -s -X GET "$H/cloud/book/getProgress?$P" -H 'Content-Type: application/json' -d "{\"BookID\":\"$B\"}" | head -c 300; echo

echo '### GET with json body book_id'
curl -s -X GET "$H/cloud/book/getProgress?$P" -H 'Content-Type: application/json' -d "{\"book_id\":\"$B\"}" | head -c 300; echo

echo '### POST form'
curl -s -X POST "$H/cloud/book/getProgress?$P" -d "BookID=$B" | head -c 300; echo

echo '### GET Body[]= (echo-style binder)'
curl -s -G "$H/cloud/book/getProgress" --data-urlencode "BookID=$B" --data-urlencode "p29=zye5b814" --data-urlencode "p2=104583" --data-urlencode "p3=101010010" --data-urlencode "p4=501603" --data-urlencode "p33=com.chaozh.iReaderFree" --data-urlencode "usr=__ZY_USR__" --data-urlencode "zysid=__ZY_ZYSID__" | head -c 300; echo

echo '### OPTIONS (find allowed)'
curl -s -i -X OPTIONS "$H/cloud/book/getProgress" | head -12