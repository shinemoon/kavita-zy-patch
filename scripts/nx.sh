#!/bin/sh
# node wrapper: sh nx.sh <script.mjs> [args...]
export NVM_DIR=/vol2/1001/.nvm
. /vol2/1001/.nvm/nvm.sh
nvm use 22 >/dev/null 2>&1
cd /vol2/1001/zhangyue-bridge || exit 1
SCRIPT="$1"
shift
node "scripts/$SCRIPT" "$@"