#!/bin/bash
P="p29=zye5b814&p2=104583&p3=101010010&p4=501603&p33=com.chaozh.iReaderFree&usr=__ZY_USR__&zysid=__ZY_ZYSID__"
U=https://api-pc.zhangyue.com/bookstore/open/cloud/book/getProgress
A=6818BFD9C92C8576A0E21AD13CD28CC2
B=70776A64C32C1C065DAB241F3E9F133A

g() { printf '%-40s ' "$1"; curl -s "$U?$1&$P" | head -c 400; echo; }

g "BookIDs=$A"
g "BookIDs=$A,$B"
g "BookIDs[]=$A"
g "BookIDs[]=$A&BookIDs[]=$B"
g "book_ids=$A"
g "BookID=$A"
echo '--- duplicate-key form (curl sends both, Go binds first) ---'
g "BookIDs=bad&$A"