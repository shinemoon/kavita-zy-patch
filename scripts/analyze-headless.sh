#!/bin/sh
export JAVA_HOME=/vol2/1001/tools/jdk-21.0.5+11
export PATH="$JAVA_HOME/bin:$PATH"
GH=/vol2/1001/tools/ghidra_12.1.4_PUBLIC
PROJ=/vol2/1001/zhangyue-bridge/ghidra-proj
NAME=UiCtrl
"$GH/support/analyzeHeadless" "$PROJ" "$NAME" \
  -import /vol2/1001/zhangyue-bridge/apk/sos_full/lib/arm64-v8a/libUiControl.so \
  -overwrite \
  -analysisTimeoutPerFile 3600
