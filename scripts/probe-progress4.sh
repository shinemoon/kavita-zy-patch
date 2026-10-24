#!/bin/bash
P="p29=zye5b814&p2=104583&p3=101010010&p4=501603&p33=com.chaozh.iReaderFree&usr=__ZY_USR__&zysid=__ZY_ZYSID__"
U=https://api-pc.zhangyue.com/bookstore/open/cloud/book/getProgress
A=6818BFD9C92C8576A0E21AD13CD28CC2
B=70776A64C32C1C065DAB241F3E9F133A

t() { printf '%-38s ' "$1"; curl -s -X "$2" "$U?$P" -H 'Content-Type: application/json' --data "$3" | head -c 340; echo; }

t 'POST BookIDs[]'        POST "{\"BookIDs\":[\"$A\"]}"
t 'POST BookIDs string'   POST "{\"BookIDs\":\"$A\"}"
t 'POST book_ids[]'       POST "{\"book_ids\":[\"$A\"]}"
t 'POST book_ids str'     POST "{\"book_ids\":\"$A\"}"
t 'POST BookID=[]'        POST "{\"BookID\":[\"$A\"]}"
t 'POST BookId=[]'        POST "{\"BookId\":[\"$A\"]}"
t 'POST bookIds=[]'       POST "{\"bookIds\":[\"$A\"]}"
t 'POST book_id=[]'       POST "{\"book_id\":[\"$A\"]}"
t 'POST BookIDs 2 items'  POST "{\"BookIDs\":[\"$A\",\"$B\"]}"