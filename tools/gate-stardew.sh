#!/bin/bash
# 游戏没开就不挂载 stardew MCP。
# 只看文件「存在」不够——退出游戏后 bridge_data.json 会留在盘上，
# 必须看它的最后写入时间（mod 每 30 tick 写一次，2 分钟没更新 = 游戏没跑）。
BRIDGE="/mnt/z/STEAAM/steamapps/common/Stardew Valley/Mods/StardewMCPBridge/bridge_data.json"
if [ ! -f "$BRIDGE" ]; then
  echo "[gate-stardew] 没有 bridge_data.json，跳过挂载" >&2
  exit 1
fi
age=$(( $(date +%s) - $(stat -c %Y "$BRIDGE") ))
if [ "$age" -gt 120 ]; then
  echo "[gate-stardew] bridge_data.json 已 ${age}s 未更新（游戏没跑），跳过挂载" >&2
  exit 1
fi
exec "/mnt/c/Program Files/nodejs/node.exe" "C:/Users/12451/StardewValley-MCP/mcp-server/build/index.js"
