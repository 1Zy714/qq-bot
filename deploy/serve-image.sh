#!/usr/bin/env bash
# 启动本地 ComfyUI 服务（Qwen-Image-2.1）。幂等：已在跑就直接返回。
# 用法: bash ~/qwen-image/bin/serve.sh [额外参数...]
#   bash ~/qwen-image/bin/serve.sh --lowvram        # 显存紧张时
set -u

ROOT="$HOME/qwen-image"
COMFY="$ROOT/ComfyUI"
PORT="${COMFY_PORT:-8188}"
LOG="$ROOT/logs/comfy.log"
mkdir -p "$ROOT/logs"

# --reserve-vram: 给系统留出显存，避免顶满时把 WSL 显示驱动搞崩
EXTRA=("$@")
if [ ${#EXTRA[@]} -eq 0 ]; then
    EXTRA=(--reserve-vram 0.8)
fi

if curl -sS -o /dev/null --max-time 4 "http://127.0.0.1:$PORT/system_stats" 2>/dev/null; then
    echo "服务已在运行: http://127.0.0.1:$PORT"
    exit 0
fi

cd "$COMFY" || exit 1
# setsid 让服务脱离当前进程组，父 shell 退出后不会被带走
setsid nohup "$ROOT/venv/bin/python" main.py \
    --listen 0.0.0.0 --port "$PORT" "${EXTRA[@]}" \
    >> "$LOG" 2>&1 < /dev/null &

for i in $(seq 1 90); do
    sleep 1
    if curl -sS -o /dev/null --max-time 3 "http://127.0.0.1:$PORT/system_stats" 2>/dev/null; then
        echo "服务已就绪（${i}s）: http://127.0.0.1:$PORT"
        echo "Windows 浏览器直接打开 http://localhost:$PORT"
        exit 0
    fi
    if ! pgrep -f '[m]ain.py --listen' > /dev/null 2>&1; then
        echo "进程已退出，日志尾部：" >&2
        tail -30 "$LOG" >&2
        exit 1
    fi
done

echo "启动超时，看日志: tail -50 $LOG" >&2
exit 1
