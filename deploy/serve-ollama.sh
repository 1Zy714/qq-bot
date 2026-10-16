#!/usr/bin/env bash
# 启动本地 Ollama 服务（用户态，不需要 root）。
# 用法: bash ~/qwen/bin/serve.sh
set -u
export PATH="$HOME/qwen/runtime/ollama/bin:$PATH"
export OLLAMA_MODELS="$HOME/.ollama/models"
export OLLAMA_FLASH_ATTENTION=1
export OLLAMA_KV_CACHE_TYPE=q8_0
export OLLAMA_NUM_PARALLEL=1
export OLLAMA_MAX_LOADED_MODELS=1
export OLLAMA_CONTEXT_LENGTH="${OLLAMA_CONTEXT_LENGTH:-32768}"
# 常驻不卸载：默认空闲 5 分钟就换出模型，下次请求要等 28~38 秒重新加载。
# 机器人/DSH 这类低频但怕冷的场景建议保持 -1；想省显存改成 "30m" 之类即可。
export OLLAMA_KEEP_ALIVE="${OLLAMA_KEEP_ALIVE:--1}"
mkdir -p "$OLLAMA_MODELS"

if curl -sS -o /dev/null --max-time 4 http://127.0.0.1:11434/api/version 2>/dev/null; then
  echo "服务已在运行: $(curl -sS --max-time 4 http://127.0.0.1:11434/api/version)"
  exit 0
fi

# setsid 让服务脱离当前进程组，父 shell 退出后不会被带走
setsid nohup ollama serve >> "$HOME/qwen/logs/ollama.log" 2>&1 < /dev/null &
for i in $(seq 1 30); do
  sleep 1
  if curl -sS -o /dev/null --max-time 3 http://127.0.0.1:11434/api/version 2>/dev/null; then
    echo "服务已就绪（${i}s）: $(curl -sS --max-time 4 http://127.0.0.1:11434/api/version)"
    exit 0
  fi
done
echo "启动超时，看日志: tail -30 ~/qwen/logs/ollama.log" >&2
exit 1
