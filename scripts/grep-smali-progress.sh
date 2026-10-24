#!/bin/bash
# grep smali for progress-sync related strings
R=/vol2/1001/zhangyue-bridge/apk/apktool-out
OUT=/tmp/zy-progress-hits.txt
: > "$OUT"

pat='getBookProcess|updateBookProcess|BookProcess|readProcess|ReadProcess|bookProgress|BookProgress|syncProgress|SyncProgress|uploadProgress|pushProgress|process/'

echo "### smali string hits ###" >> "$OUT"
grep -rhoE "\"[^\"]*($pat)[^\"]*\"" "$R"/smali "$R"/smali_classes2 2>/dev/null \
  | sort -u | head -80 >> "$OUT"
wc -l < "$OUT"

echo "### class-name hits ###" >> "$OUT"
grep -rhoE 'L[a-zA-Z0-9/]*(Progress|Process)[a-zA-Z0-9/$]*;' "$R"/smali "$R"/smali_classes2 2>/dev/null \
  | sort -u | head -60 >> "$OUT"

echo "### book-open-position / last-read ###" >> "$OUT"
grep -rhoE '"[^"]*(lastRead|LastRead|last_read|readPos|ReadPos|lastPos|LastPos|bookmark|Bookmark|readChapter|ReadChapter|lastChapter)[^"]*"' \
  "$R"/smali "$R"/smali_classes2 2>/dev/null | sort -u | head -60 >> "$OUT"

cat "$OUT"