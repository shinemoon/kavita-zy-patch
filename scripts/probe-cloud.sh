#!/bin/bash
H=https://icloud.zhangyue.com/cloud/storage2/safe
P="p29=zye5b814&p2=104583&p3=101010010&p4=501603&p33=com.chaozh.iReaderFree&usr=__ZY_USR__&zysid=__ZY_ZYSID__"
B=6818BFD9C92C8576A0E21AD13CD28CC2

g() { printf '%-26s ' "$1"; curl -s -m 15 "$H/$1?$2&$P" | head -c 300; echo; }
p() { printf '%-26s ' "POST $1"; curl -s -m 15 -X POST "$H/$1?$P" -H 'Content-Type: application/json' --data "$2" | head -c 300; echo; }

echo '########## GET side ##########'
g getProcess      "book_id=$B"
g getProcess      "bookId=$B"
g getProcess      "book_ids=$B"
g getUserDataVer  ""
g getCloudBookList ""
g getCloudBookListV2 ""
g getCloudBookInfo "book_id=$B"

echo
echo '########## POST side (harmless / non-mutating) ##########'
p getUserDataVer '{}'
p getCloudBookList '{}'
p getProcess '{"bookId":"'"$B"'"}'