#!/bin/bash
R=/vol2/1001/zhangyue-bridge/apk/jadx/sources/com/zhangyue/ReadComponent/ReadModule/ReadingRecord
echo "=== files: $(find $R -name '*.java' | wc -l) ==="
find $R -name '*.java' | sort | head -60