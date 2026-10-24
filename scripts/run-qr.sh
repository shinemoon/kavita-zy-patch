#!/bin/sh
# 跑 capture-qr.mjs (带 nvm 环境)
export NVM_DIR=/vol2/1001/.nvm
. /vol2/1001/.nvm/nvm.sh
nvm use 22 >/dev/null 2>&1
echo "node=$(node -v)"
cd /vol2/1001/zhangyue-bridge
node scripts/capture-qr.mjs