#!/usr/bin/env bash
# 启动 QQ 机器人适配器（幂等：已在跑就提示）。
#
# 用法:
#   bash ~/qq-bot/run.sh              启动
#   bash ~/qq-bot/run.sh --selftest   只测 Ollama 端点，不需要 NapCat
#   bash ~/qq-bot/run.sh -f           前台运行（看实时日志）
set -uo pipefail

cd "$(dirname "$0")"
VENV="${VENV:-$HOME/.venvs/qqbot}"
LOG="$HOME/qq-bot/bot.log"
FOREGROUND=0
ARGS=()

for arg in "$@"; do
  if [ "$arg" = "-f" ] || [ "$arg" = "--foreground" ]; then
    FOREGROUND=1
  else
    ARGS+=("$arg")
  fi
done

[ -d "$VENV" ] || python3 -m venv "$VENV"
if ! "$VENV/bin/python" -c 'import httpx, websockets' >/dev/null 2>&1; then
  echo "安装依赖 (httpx, websockets)..."
  "$VENV/bin/pip" install -q -U pip httpx websockets || {
    echo "依赖安装失败；国内可加源: $VENV/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple httpx websockets" >&2
    exit 1
  }
fi

[ -f config.json ] || { echo "缺少 config.json，先: cp config.example.json config.json" >&2; exit 1; }

# 带任何额外参数（例如 --selftest）一律前台执行，避免把一次性命令丢到后台
if [ "$FOREGROUND" = "1" ] || [ "${#ARGS[@]}" -gt 0 ]; then
  exec "$VENV/bin/python" -u bot.py "${ARGS[@]+"${ARGS[@]}"}"
fi

if pgrep -f "$PWD/bot\.py" >/dev/null 2>&1; then
  echo "适配器已在运行: $(pgrep -f "$PWD/bot\.py" | tr '\n' ' ')"
  echo "重启: bash ~/qq-bot/svc.sh stop napcat && bash ~/qq-bot/svc.sh start napcat"
  exit 0
fi

# setsid 脱离当前进程组，父 shell 退出后不会把适配器带走
setsid nohup "$VENV/bin/python" -u "$PWD/bot.py" "${ARGS[@]+"${ARGS[@]}"}" >> "$LOG" 2>&1 < /dev/null &
sleep 2
if pgrep -f "$PWD/bot\.py" >/dev/null 2>&1; then
  echo "适配器已启动 (PID $(pgrep -f "$PWD/bot\.py" | tr '\n' ' '))"
  echo "日志: tail -f $LOG"
  grep -q listening "$LOG" && grep listening "$LOG" | tail -1
else
  echo "启动失败，日志尾部:" >&2
  tail -20 "$LOG" >&2
  exit 1
fi
