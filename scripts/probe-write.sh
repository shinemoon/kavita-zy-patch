#!/bin/bash
# WRITE-SIDE TEST. Sends the EXACT current server value back (readPosition="" readPercent="")
# so this is effectively a no-op upsert -- it cannot desync anything.
H=https://icloud.zhangyue.com/cloud/storage2/safe
P="p29=zye5b814&p2=104583&p3=101010010&p4=501603&p33=com.chaozh.iReaderFree&usr=__ZY_USR__&zysid=__ZY_ZYSID__"
B=6818BFD9C92C8576A0E21AD13CD28CC2

echo '### PRE: read current'
curl -s -m 20 --compressed "$H/getProcess?bookIds=$B&$P"
echo; echo

echo '### WRITE: uploadProcess with identical payload (idempotent no-op)'
curl -s -m 25 -X POST "$H/uploadProcess?$P" \
  -H 'Content-Type: application/json' \
  -d "{\"usr\":\"__ZY_USR__\",\"bookId\":\"$B\",\"bookName\":\"\",\"deviceName\":\"Probe\",\"updateTime\":\"$(date +%s)000\",\"readProcess\":{\"readPosition\":\"\",\"readPercent\":\"\",\"lastReadDate\":$(date +%s)000,\"totalCount\":0,\"chapterId\":\"0\",\"readComplete\":0,\"bookOverStatus\":0,\"isCartoon\":0,\"isFineBook\":0,\"readingSource\":\"web\"}}" \
  | head -c 500
echo; echo

sleep 2
echo '### POST: read back to confirm round-trip'
curl -s -m 20 --compressed "$H/getProcess?bookIds=$B&$P"
echo