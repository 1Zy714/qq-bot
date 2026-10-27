#!/bin/bash
# 游戏没开就不挂载 nagi MCP：DSH 侧工具为空，聊天不会被「调工具失败」拖慢
if ! curl -s -m 2 -o /dev/null http://localhost:7842/status; then
  echo "[gate-nagi] NagiBridge 未就绪，跳过挂载" >&2
  exit 1
fi
exec /home/lzy959/.venvs/qqbot/bin/python /home/lzy959/qq-bot/tools/nagi_mcp.py
