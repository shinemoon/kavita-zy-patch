#!/bin/bash
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36'

echo '### 1. official landing / apk index'
for U in \
  'https://pc.ireader.com/index/2/app.html' \
  'https://www.ireader.com.cn/index/download.html' \
  'https://pc.ireader.com/download' ; do
  printf '%-52s ' "$(echo $U | cut -c1-50)"
  code=$(curl -s -m 12 -o /tmp/zypage.html -w '%{http_code}' -A "$UA" "$U")
  echo -n "$code  "
  grep -o 'https\?://[^"'"'"' ]*\.apk' /tmp/zypage.html 2>/dev/null | head -2 | tr '\n' ' '
  echo
done

echo
echo '### 2. common mirror CDNs (HEAD only)'
for U in \
  'https://down.zhangyue.com/apk/ireader.apk' \
  'https://cdn.zhangyue.com/ireader/ireader.apk' \
  'https://img.zhangyue.com/ireader/ireader.apk' \
  'https://mobile.zhangyue.com/apk/iReader.apk' ; do
  printf '%-56s ' "$(echo $U | cut -c1-54)"
  curl -s -m 12 -I -A "$UA" "$U" | awk 'NR==1{c=$2} /content-length/{l=$2} END{printf "code=%s len=%s\n", c, l}'
done