#!/bin/bash
H=https://icloud.zhangyue.com/cloud/storage2/safe
P="p29=zye5b814&p2=104583&p3=101010010&p4=501603&p33=com.chaozh.iReaderFree&usr=__ZY_USR__&zysid=__ZY_ZYSID__"
B=6818BFD9C92C8576A0E21AD13CD28CC2

echo '### getProcess bookIds= (comma list of the 5 shelf books)'
curl -s -m 20 --compressed "$H/getProcess?bookIds=$B&$P" | head -c 1200
echo
echo
echo '### getCloudBookInfo bookId='
curl -s -m 20 --compressed "$H/getCloudBookInfo?bookId=$B&$P" | head -c 800
echo
echo
echo '### getUserDataVer (raw, decompressed)'
curl -s -m 20 --compressed "$H/getUserDataVer?$P" | head -c 600